"""Evergale BOT — entry point."""

import asyncio
import os
import sys
from dataclasses import dataclass

import discord
import discord.voice_state
from discord.ext import commands

from evergale_bot.src.audio_bridge import AudioBridge
from evergale_bot.src.boss import Boss
from evergale_bot.src.config import Config
from evergale_bot.src.logger import log
from evergale_bot.src.relay import Relay
from evergale_bot.src.roster import Roster
from evergale_bot.src.utility import Utility


@dataclass
class _RunState:
    bot_speaker: commands.Bot | None = None
    bridge: AudioBridge | None = None


_STATE: _RunState = _RunState()

_INTENTS: discord.Intents = discord.Intents.default()
_INTENTS.guilds = True
_INTENTS.members = True
_INTENTS.messages = True
_INTENTS.message_content = True
_INTENTS.voice_states = True

BOT: commands.Bot = commands.Bot(command_prefix="!", intents=_INTENTS)

BOT.tree.add_command(Boss())
BOT.tree.add_command(Roster())
BOT.tree.add_command(Utility())


def _log_online(bot: commands.Bot, tag: str) -> None:
    """Log a standardised 'bot is online' message.

    Args:
        bot: The connected bot instance.
        tag: Log category label (e.g. ``BOT``, ``SPEAKER``).
    """
    log(f"[{tag}] Online as {bot.user.display_name} ({bot.user.id})")


@BOT.event
async def on_ready() -> None:
    """Sync slash commands to the configured guild on startup."""
    _log_online(BOT, "BOT")
    if Config.GUILD_ID == 0:
        log("[BOT] WARNING: GUILD_ID is not set — commands will not sync")
        return
    guild = discord.Object(id=Config.GUILD_ID)
    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)
    BOT.tree.clear_commands(guild=None)
    await BOT.tree.sync(guild=None)
    log(f"[BOT] Synced {len(synced)} commands to guild {Config.GUILD_ID}")


@BOT.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState,
                                after: discord.VoiceState) -> None:
    """Auto-stop the relay when the invoker leaves their voice channel.

    Only the invoker leaving triggers teardown — voice-state flaps for the bots
    themselves (e.g. the brief voice-websocket reconnect during a DAVE downgrade)
    must not tear down the relay.

    Args:
        member: The member whose voice state changed.
        before: Voice state before the change.
        after: Voice state after the change.
    """
    bridge = _STATE.bridge
    if bridge is None or not bridge.active:
        return
    if member.id != bridge.invoker_id:
        return
    if before.channel == bridge.listen_channel and after.channel != bridge.listen_channel:
        log(f"[RELAY] Auto-stop: @{member.display_name} left #{before.channel.name}")
        await bridge.teardown("invoker left the channel")


async def _on_speaker_ready() -> None:
    """Log when the speaker bot connects."""
    if _STATE.bot_speaker:
        _log_online(_STATE.bot_speaker, "SPEAKER")


async def _run(master_token: str, speaker_token: str | None) -> None:
    """Launch the master bot and optionally the speaker bot.

    Args:
        master_token: Discord token for the master (ear) bot.
        speaker_token: Discord token for the speaker bot, or None if not configured.
    """
    if speaker_token and _STATE.bot_speaker:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(BOT.start(master_token))
            tg.create_task(_STATE.bot_speaker.start(speaker_token))
    else:
        await BOT.start(master_token)


def _disable_dave() -> None:
    """Disable Discord's DAVE end-to-end voice encryption.

    The voice-recv extension cannot decrypt DAVE-protected audio, so while DAVE
    is active the master bot receives no decodable frames and the relay stays
    silent. Advertising DAVE protocol version 0 makes Discord fall back to
    transport-only encryption, which voice-recv supports. The bot intentionally
    reads the invoker's audio, so end-to-end encryption is not applicable here.
    """
    try:
        discord.voice_state.has_dave = False
        log("[BOT] DAVE end-to-end encryption disabled (required for relay receive)")
    except AttributeError as exc:
        log(f"[BOT] Could not disable DAVE — discord.py internals may have changed: {exc}")


def _ensure_opus() -> bool:
    """Attempt to load libopus if not already loaded.

    Delegates to discord.py's own platform-aware loader so no library names or
    paths are hardcoded here.

    Returns:
        True if Opus is available, False otherwise.
    """
    if discord.opus.is_loaded():
        return True
    return discord.opus._load_default()  # noqa: SLF001


def main() -> int:
    """Start the bot.

    Returns:
        Exit code returned to the operating system.
    """
    master_token = os.getenv("MAGIC")
    if not master_token:
        return 1

    speaker_token = os.getenv("SPEAKER_TOKEN")
    if speaker_token:
        _disable_dave()
        if not _ensure_opus():
            log("[BOT] WARNING: libopus not found — relay audio will not work. "
                "Install the Opus library via your system package manager (e.g. apt/brew/choco).")
        _STATE.bot_speaker = commands.Bot(command_prefix="!", intents=_INTENTS)
        _STATE.bot_speaker.add_listener(_on_speaker_ready, "on_ready")
        _STATE.bridge = AudioBridge(bot_speaker=_STATE.bot_speaker)
        BOT.tree.add_command(Relay(bridge=_STATE.bridge))
        log("[BOT] Speaker token found — relay commands enabled")
    else:
        log("[BOT] No SPEAKER_TOKEN — relay commands disabled, all other commands available")

    asyncio.run(_run(master_token, speaker_token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
