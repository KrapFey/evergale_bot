"""Audio bridge connecting the master bot (listener) to the speaker bot (speaker)."""

import asyncio
import contextlib
import os
import queue as thread_queue
from dataclasses import dataclass, field
from typing import ClassVar

import discord
from discord.ext import commands, voice_recv

from evergale_bot.src.logger import log

_QUEUE_MAX_FRAMES: int = 15
_PCM_FRAME_BYTES: int = 3840  # 20ms at 48kHz stereo 16-bit
_DEBUG_EVERY_FRAMES: int = 250  # ~5s of audio at 20ms/frame
_RELAY_DEBUG: bool = os.getenv("RELAY_DEBUG", "").strip().lower() not in ("", "0", "false")


class UserSink(voice_recv.AudioSink):
    """Captures PCM audio from a single target user and feeds it into the bridge queue.

    Frames from all other users are silently discarded. When the queue is full
    the oldest frame is dropped to prevent unbounded latency growth.
    """

    def __init__(self, target_user_id: int, audio_queue: thread_queue.Queue[bytes],
                 voice_client: voice_recv.VoiceRecvClient) -> None:
        """Initialise the sink.

        Args:
            target_user_id: Discord user ID whose audio will be captured.
            audio_queue: Shared thread-safe queue to push PCM frames into.
            voice_client: The master voice client, used to map SSRC to user id.
        """
        super().__init__()
        self.__target_user_id: int = target_user_id
        self.__queue: thread_queue.Queue[bytes] = audio_queue
        self.__vc: voice_recv.VoiceRecvClient = voice_client
        self.__calls: int = 0
        self.__queued: int = 0
        self.__drop_user: int = 0
        self.__drop_size: int = 0
        self.__last_seen_id: int | None = None
        if _RELAY_DEBUG:
            log(f"[RELAY-DBG] sink listening for user_id={target_user_id}")

    def write(self, user: discord.Member | discord.User | None,
              data: voice_recv.VoiceData) -> None:
        """Write a received audio frame, filtering to the target user.

        Args:
            user: The Discord user who produced the audio, or None if unknown.
            data: Voice frame containing decoded PCM data.
        """
        self.__calls += 1
        self.__debug_first_frames(user, data)
        # Resolve the speaker from the raw SSRC->id map (populated by the SPEAKING
        # gateway op), which does not depend on the member cache like ``data.source``
        # does. Accept the frame if either path identifies the target.
        speaker_id = self.__vc._get_id_from_ssrc(data.packet.ssrc)  # noqa: SLF001
        is_target = (speaker_id == self.__target_user_id
                     or (user is not None and user.id == self.__target_user_id))
        if not is_target:
            self.__drop_user += 1
            self.__last_seen_id = speaker_id if user is None else user.id
            self.__log_stats()
            return
        pcm = data.pcm
        # voice_recv yields b'' or partial buffers for lost/concealment packets.
        # Forwarding those downstream stops the speaker's player, so only enqueue
        # exact 20ms frames.
        if len(pcm) != _PCM_FRAME_BYTES:
            self.__drop_size += 1
            self.__log_stats()
            return
        self.__enqueue(pcm)
        self.__queued += 1
        self.__log_stats()

    def __enqueue(self, pcm: bytes) -> None:
        """Push a frame, dropping the oldest when the queue is full.

        Args:
            pcm: A full 20ms PCM frame to enqueue.
        """
        try:
            self.__queue.put_nowait(pcm)
        except thread_queue.Full:
            with contextlib.suppress(thread_queue.Empty):
                self.__queue.get_nowait()
            with contextlib.suppress(thread_queue.Full):
                self.__queue.put_nowait(pcm)

    def __log_stats(self) -> None:
        """Emit a periodic capture summary when RELAY_DEBUG is enabled."""
        if not _RELAY_DEBUG or self.__calls % _DEBUG_EVERY_FRAMES != 0:
            return
        log(f"[RELAY-DBG] sink calls={self.__calls} queued={self.__queued} "
            f"drop_user={self.__drop_user} drop_size={self.__drop_size} "
            f"last_other_id={self.__last_seen_id} qsize={self.__queue.qsize()}")

    def __debug_first_frames(self, user: discord.Member | discord.User | None,
                             data: voice_recv.VoiceData) -> None:
        """Log raw details of the first few frames for one-shot diagnosis.

        Args:
            user: The resolved source member/user, or None.
            data: The voice frame being processed.
        """
        if not _RELAY_DEBUG or self.__calls > 5:
            return
        sid = self.__vc._get_id_from_ssrc(data.packet.ssrc)  # noqa: SLF001
        src = user.id if user is not None else None
        log(f"[RELAY-DBG] frame#{self.__calls} ssrc={data.packet.ssrc} "
            f"resolved_id={sid} source_id={src} pcm_len={len(data.pcm)}")

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member: discord.Member) -> None:
        """Log when the gateway reports a member started speaking (debug only).

        Args:
            member: The member who began speaking.
        """
        if _RELAY_DEBUG:
            log(f"[RELAY-DBG] speaking_start member_id={getattr(member, 'id', None)}")

    def cleanup(self) -> None:
        """Called by discord.py when the sink is detached."""

    def wants_opus(self) -> bool:
        """Dictates if the sink wants encoded Opus data or decoded PCM data.

        Return False if you want uncompressed PCM data (recommended for relays).
        Return True if you want compressed Opus packets.
        """
        return False


class BridgeAudioSource(discord.AudioSource):
    """Reads PCM frames from the bridge queue for playback.

    When the queue is empty a silent PCM frame is returned so the player
    thread keeps running without disconnecting.
    """

    _SILENCE: ClassVar[bytes] = b"\x00" * _PCM_FRAME_BYTES

    def __init__(self, audio_queue: thread_queue.Queue[bytes]) -> None:
        """Initialise the source.

        Args:
            audio_queue: Shared thread-safe queue to read PCM frames from.
        """
        self.__queue: thread_queue.Queue[bytes] = audio_queue
        self.__reads: int = 0
        self.__real: int = 0

    def read(self) -> bytes:
        """Return the next PCM frame, or silence if none is available.

        Returns:
            3840 bytes of 48kHz stereo 16-bit PCM.
        """
        self.__reads += 1
        try:
            frame = self.__queue.get_nowait()
        except thread_queue.Empty:
            self.__log_stats()
            return self._SILENCE
        # discord.py stops the player permanently if read() ever returns a falsy
        # or wrong-sized buffer (player.py: ``if not data: self.stop()``). Never
        # hand it anything but a full frame.
        if len(frame) != _PCM_FRAME_BYTES:
            self.__log_stats()
            return self._SILENCE
        self.__real += 1
        self.__log_stats()
        return frame

    def __log_stats(self) -> None:
        """Emit a periodic playback summary when RELAY_DEBUG is enabled."""
        if not _RELAY_DEBUG or self.__reads % _DEBUG_EVERY_FRAMES != 0:
            return
        log(f"[RELAY-DBG] source reads={self.__reads} real={self.__real} "
            f"silence={self.__reads - self.__real}")

    def is_opus(self) -> bool:
        """Indicate that this source provides raw PCM, not Opus.

        Returns:
            Always False — discord.py handles Opus encoding.
        """
        return False


@dataclass
class AudioBridge:
    """Coordinator that owns the audio queue and both voice connections.

    Must be created before either bot starts. Pass a reference to both
    the speaker bot instance and the relay command group so all three share
    the same state object.
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
        """Initialise private voice client handles and the lifecycle lock."""
        self.__vc_master: voice_recv.VoiceRecvClient | None = None
        self.__vc_speaker: discord.VoiceClient | None = None
        self.__lock: asyncio.Lock = asyncio.Lock()

    async def start(self, invoker: discord.User | discord.Member, listen_ch: discord.VoiceChannel,
                    speak_ch: discord.VoiceChannel) -> None:
        """Connect both bots and start the audio pipeline.

        Args:
            invoker: The user or member who triggered the relay.
            listen_ch: Voice channel Bot 1 (master) joins.
            speak_ch: Voice channel Bot 2 (speaker) joins.

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
        log(f"[RELAY] Started: @{invoker.display_name} "
            f"#{listen_ch.name} -> #{speak_ch.name}")

    async def teardown(self, reason: str = "manual") -> None:
        """Stop the audio pipeline and disconnect both bots.

        Args:
            reason: Short description logged alongside the stop event.
        """
        async with self.__lock:
            await self.__teardown_locked(reason)

    async def __connect(self, invoker: discord.User | discord.Member,
                        listen_ch: discord.VoiceChannel, speak_ch: discord.VoiceChannel) -> None:
        """Open both voice connections and wire the audio pipeline.

        Args:
            invoker: The user whose audio is captured.
            listen_ch: Voice channel the master bot joins.
            speak_ch: Voice channel the speaker bot joins.
        """
        self.__vc_master = await listen_ch.connect(cls=voice_recv.VoiceRecvClient)
        self.__vc_master.listen(UserSink(invoker.id, self.queue, self.__vc_master))
        speaker_ch = await self.__resolve_speaker_channel(speak_ch)
        self.__vc_speaker = await speaker_ch.connect()
        self.__vc_speaker.play(BridgeAudioSource(self.queue))

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
        self.__drain_queue()
        self.invoker_id = None
        self.listen_channel = None
        self.speak_channel = None
        self.__vc_master = None
        self.__vc_speaker = None
        log(f"[RELAY] Stopped: {reason}")

    def __drain_queue(self) -> None:
        """Empty the audio queue after teardown."""
        while True:
            try:
                self.queue.get_nowait()
            except thread_queue.Empty:
                break
