"""Audio bridge connecting the master bot (listener) to the speaker bot (speaker)."""

import queue as thread_queue
from dataclasses import dataclass, field
from typing import ClassVar

import discord
from discord.ext import commands, voice_recv

from evergale_bot.src.logger import log

_QUEUE_MAX_FRAMES: int = 15
_PCM_FRAME_BYTES: int = 3840  # 20ms at 48kHz stereo 16-bit


class UserSink(voice_recv.AudioSink):
    """Captures PCM audio from a single target user and feeds it into the bridge queue.

    Frames from all other users are silently discarded. When the queue is full
    the oldest frame is dropped to prevent unbounded latency growth.
    """

    def __init__(self, target_user_id: int, audio_queue: thread_queue.Queue[bytes]) -> None:
        """Initialise the sink.

        Args:
            target_user_id: Discord user ID whose audio will be captured.
            audio_queue: Shared thread-safe queue to push PCM frames into.
        """
        self.__target_user_id: int = target_user_id
        self.__queue: thread_queue.Queue[bytes] = audio_queue

    def write(self, data: voice_recv.VoiceData, user: discord.User) -> None:
        """Write a received audio frame, filtering to the target user.

        Args:
            data: Voice frame containing decoded PCM data.
            user: The Discord user who produced the audio.
        """
        if user.id != self.__target_user_id:
            return
        try:
            self.__queue.put_nowait(data.pcm)
        except thread_queue.Full:
            self.__queue.get_nowait()
            self.__queue.put_nowait(data.pcm)

    def cleanup(self) -> None:
        """Called by discord.py when the sink is detached."""

    def wants_opus(self):
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

    def read(self) -> bytes:
        """Return the next PCM frame, or silence if none is available.

        Returns:
            3840 bytes of 48kHz stereo 16-bit PCM.
        """
        try:
            return self.__queue.get_nowait()
        except thread_queue.Empty:
            return self._SILENCE

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
    _vc_master: discord.VoiceClient | None = field(default=None, repr=False)
    _vc_speaker: discord.VoiceClient | None = field(default=None, repr=False)

    async def start(self, invoker: discord.Member, listen_ch: discord.VoiceChannel,
                    speak_ch: discord.VoiceChannel) -> None:
        """Connect both bots and start the audio pipeline.

        Args:
            invoker: The member who triggered the relay.
            listen_ch: Voice channel Bot 1 (master) joins.
            speak_ch: Voice channel Bot 2 (speaker) joins.
        """
        self.invoker_id = invoker.id
        self.listen_channel = listen_ch
        self.speak_channel = speak_ch

        try:
            self._vc_master = await listen_ch.connect(cls=voice_recv.VoiceRecvClient)
            self._vc_master.listen(UserSink(invoker.id, self.queue))

            speaker_ch = self.bot_speaker.get_channel(speak_ch.id)
            if speaker_ch is None:
                raise RuntimeError(
                    f"Speaker bot cannot see channel #{speak_ch.name} — "
                    "ensure it is invited to the guild and has cached the channel.",
                )
            self._vc_speaker = await speaker_ch.connect()
            self._vc_speaker.play(BridgeAudioSource(self.queue))
        except Exception:
            await self.teardown("connection failed during start")
            raise

        self.active = True
        log(f"[RELAY] Started: @{invoker.display_name} "
            f"#{listen_ch.name} -> #{speak_ch.name}")

    async def teardown(self, reason: str = "manual") -> None:
        """Stop the audio pipeline and disconnect both bots.

        Args:
            reason: Short description logged alongside the stop event.
        """
        if not self.active and self._vc_master is None and self._vc_speaker is None:
            return
        self.active = False

        if self._vc_master and self._vc_master.is_connected():
            self._vc_master.stop_listening()
            await self._vc_master.disconnect()

        if self._vc_speaker and self._vc_speaker.is_connected():
            self._vc_speaker.stop()
            await self._vc_speaker.disconnect()

        self.__drain_queue()

        self.invoker_id = None
        self.listen_channel = None
        self.speak_channel = None
        self._vc_master = None
        self._vc_speaker = None

        log(f"[RELAY] Stopped: {reason}")

    def __drain_queue(self) -> None:
        """Empty the audio queue after teardown."""
        while True:
            try:
                self.queue.get_nowait()
            except thread_queue.Empty:
                break
