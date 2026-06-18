"""FFmpeg-based audio bridge — drop-in alternative to ``audio_bridge.AudioBridge``.

This variant keeps the SSRC-based capture path (``UserSink``) but replaces the
custom ``BridgeAudioSource`` playback with discord.py's well-tested
``FFmpegPCMAudio`` pipe. Captured PCM is paced into ffmpeg's stdin and ffmpeg's
output is what the speaker bot plays — so the only custom playback code is the
small, deterministic stream below instead of an ``AudioSource`` bound by
discord.py's strict read contract.

To use it instead of the pure-Python bridge, change the import in
``evergale_bot.py``::

    from evergale_bot.src.audio_bridge_ffmpeg import FfmpegAudioBridge as AudioBridge

Requirements:
    ``ffmpeg`` must be available on PATH (discord-py[voice] does not bundle it).

Latency note:
    End-to-end latency is the capture jitter-queue depth plus ffmpeg/pipe
    buffering. Lower ``_QUEUE_MAX_FRAMES`` in ``audio_bridge`` to trade
    robustness for lower latency.
"""

import asyncio
import contextlib
import queue as thread_queue
from dataclasses import dataclass, field
from typing import ClassVar

import discord
from discord.ext import commands, voice_recv

from evergale_bot.src.audio_bridge import _PCM_FRAME_BYTES, _QUEUE_MAX_FRAMES, UserSink
from evergale_bot.src.logger import log

_FRAME_SECONDS: float = 0.02  # 20ms per PCM frame


class _LivePcmStream:
    """A real-time-paced, readable PCM stream feeding ffmpeg's stdin.

    discord.py's ffmpeg pipe writer calls :meth:`read` in a tight loop and
    treats an empty return as EOF. While the relay is active this stream always
    returns exactly one 20ms frame (silence when no audio is queued), so ffmpeg
    never sees EOF and the speaker's player never stops.
    """

    _SILENCE: ClassVar[bytes] = b"\x00" * _PCM_FRAME_BYTES

    def __init__(self, audio_queue: thread_queue.Queue[bytes]) -> None:
        """Initialise the stream.

        Args:
            audio_queue: Shared thread-safe queue of captured PCM frames.
        """
        self.__queue: thread_queue.Queue[bytes] = audio_queue
        self.__closed: bool = False

    def read(self, _size: int = -1) -> bytes:
        """Return one 20ms PCM frame, blocking up to one frame for real audio.

        Args:
            _size: Requested byte count from ffmpeg; ignored. One frame is
                returned per call to keep playback paced at real time.

        Returns:
            A 3840-byte PCM frame, silence on underrun, or empty bytes once the
            stream is closed (which signals EOF to ffmpeg).
        """
        if self.__closed:
            return b""
        try:
            frame = self.__queue.get(timeout=_FRAME_SECONDS)
        except thread_queue.Empty:
            return self._SILENCE
        return frame if len(frame) == _PCM_FRAME_BYTES else self._SILENCE

    def close(self) -> None:
        """Mark the stream closed so the next read signals EOF to ffmpeg."""
        self.__closed = True


@dataclass
class FfmpegAudioBridge:
    """Audio bridge that plays captured PCM through an ffmpeg pipe.

    Exposes the same public interface as ``audio_bridge.AudioBridge`` so it can
    be swapped in without changes elsewhere.
    """

    bot_speaker: commands.Bot
    queue: thread_queue.Queue[bytes] = field(
        default_factory=lambda: thread_queue.Queue(maxsize=_QUEUE_MAX_FRAMES),
    )
    invoker_id: int | None = None
    listen_channel: discord.VoiceChannel | None = None
    speak_channel: discord.VoiceChannel | None = None
    active: bool = False

    def __post_init__(self) -> None:
        """Initialise private voice client, stream, and lock handles."""
        self.__vc_master: voice_recv.VoiceRecvClient | None = None
        self.__vc_speaker: discord.VoiceClient | None = None
        self.__stream: _LivePcmStream | None = None
        self.__lock: asyncio.Lock = asyncio.Lock()

    async def start(self, invoker: discord.User | discord.Member, listen_ch: discord.VoiceChannel,
                    speak_ch: discord.VoiceChannel) -> None:
        """Connect both bots and start the ffmpeg-backed pipeline.

        Args:
            invoker: The user or member who triggered the relay.
            listen_ch: Voice channel the master bot joins.
            speak_ch: Voice channel the speaker bot joins.

        Raises:
            RuntimeError: If a relay is already active or the speaker channel
                cannot be resolved.
        """
        async with self.__lock:
            if self.active:
                raise RuntimeError("A relay is already active.")
            self.active = True
            self.invoker_id = invoker.id
            self.listen_channel = listen_ch
            self.speak_channel = speak_ch
            try:
                await self.__connect(invoker, listen_ch, speak_ch)
            except Exception:
                await self.__teardown_locked("connection failed during start")
                raise
        log(f"[RELAY] Started (ffmpeg): @{invoker.display_name} "
            f"#{listen_ch.name} -> #{speak_ch.name}")

    async def teardown(self, reason: str = "manual") -> None:
        """Stop the pipeline and disconnect both bots.

        Args:
            reason: Short description logged alongside the stop event.
        """
        async with self.__lock:
            await self.__teardown_locked(reason)

    async def __connect(self, invoker: discord.User | discord.Member,
                        listen_ch: discord.VoiceChannel, speak_ch: discord.VoiceChannel) -> None:
        """Open both voice connections and wire capture into the ffmpeg pipe.

        Args:
            invoker: The user whose audio is captured.
            listen_ch: Voice channel the master bot joins.
            speak_ch: Voice channel the speaker bot joins.
        """
        self.__vc_master = await listen_ch.connect(cls=voice_recv.VoiceRecvClient)
        self.__vc_master.listen(UserSink(invoker.id, self.queue, self.__vc_master))
        speaker_ch = await self.__resolve_speaker_channel(speak_ch)
        self.__vc_speaker = await speaker_ch.connect()
        self.__stream = _LivePcmStream(self.queue)
        source = discord.FFmpegPCMAudio(self.__stream, pipe=True,
                                        before_options="-f s16le -ar 48000 -ac 2")
        self.__vc_speaker.play(source)

    async def __resolve_speaker_channel(self,
                                        speak_ch: discord.VoiceChannel) -> discord.VoiceChannel:
        """Resolve the speak channel through the speaker bot, fetching on cache miss.

        Args:
            speak_ch: The channel as seen by the master bot.

        Returns:
            The same channel as seen by the speaker bot.

        Raises:
            RuntimeError: If the speaker bot cannot see the channel.
        """
        channel = self.bot_speaker.get_channel(speak_ch.id)
        if channel is None:
            with contextlib.suppress(discord.HTTPException):
                channel = await self.bot_speaker.fetch_channel(speak_ch.id)
        if not isinstance(channel, discord.VoiceChannel):
            raise RuntimeError(
                f"Speaker bot cannot see channel #{speak_ch.name} — "
                "ensure it is invited to the guild with access to that channel.",
            )
        return channel

    async def __teardown_locked(self, reason: str) -> None:
        """Disconnect both bots and reset state. Caller must hold ``__lock``.

        Args:
            reason: Short description logged alongside the stop event.
        """
        if not self.active and self.__vc_master is None and self.__vc_speaker is None:
            return
        self.active = False
        if self.__vc_master and self.__vc_master.is_connected():
            self.__vc_master.stop_listening()
            await self.__vc_master.disconnect()
        if self.__vc_speaker and self.__vc_speaker.is_connected():
            self.__vc_speaker.stop()
            await self.__vc_speaker.disconnect()
        if self.__stream is not None:
            self.__stream.close()
        self.__drain_queue()
        self.__reset_state()
        log(f"[RELAY] Stopped (ffmpeg): {reason}")

    def __reset_state(self) -> None:
        """Clear invoker, channel, and handle state after teardown."""
        self.invoker_id = None
        self.listen_channel = None
        self.speak_channel = None
        self.__vc_master = None
        self.__vc_speaker = None
        self.__stream = None

    def __drain_queue(self) -> None:
        """Empty the audio queue after teardown."""
        while True:
            try:
                self.queue.get_nowait()
            except thread_queue.Empty:
                break
