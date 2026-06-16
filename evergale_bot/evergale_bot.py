"""Evergale BOT — entry point."""

import asyncio
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

from evergale_bot.src.audio_bridge import AudioBridge
from evergale_bot.src.boss import Boss
from evergale_bot.src.config import Config
from evergale_bot.src.logger import log
from evergale_bot.src.relay import Relay
from evergale_bot.src.roster import Roster
from evergale_bot.src.utility import Utility

load_dotenv()


def _log_online(bot: commands.Bot, tag: str) -> None:
    """Log a standardised 'bot is online' message.

    Args:
        bot: The connected bot instance.
        tag: Log category label (e.g. ``BOT``, ``SPEAKER``).
    """
    log(f"[{tag}] Online as {bot.user.display_name} ({bot.user.id})")


_INTENTS: discord.Intents = discord.Intents.default()
_INTENTS.guilds = True
_INTENTS.members = True
_INTENTS.messages = True
_INTENTS.message_content = True
_INTENTS.voice_states = True

BOT: commands.Bot = commands.Bot(command_prefix="!", intents=_INTENTS)
_BOT_SPEAKER: commands.Bot | None = None

_BRIDGE: AudioBridge | None = None

BOT.tree.add_command(Boss())
BOT.tree.add_command(Roster())
BOT.tree.add_command(Utility())


@BOT.event
async def on_ready() -> None:
    """Sync slash commands to the configured guild on startup."""
    _log_online(BOT, "BOT")
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

    Args:
        member: The member whose voice state changed.
        before: Voice state before the change.
        after: Voice state after the change.
    """
    if _BRIDGE is None or not _BRIDGE.active:
        return
    if member.id != _BRIDGE.invoker_id:
        return
    if before.channel == _BRIDGE.listen_channel and after.channel != _BRIDGE.listen_channel:
        log(f"[RELAY] Auto-stop: @{member.display_name} left #{before.channel.name}")
        await _BRIDGE.teardown("invoker left the channel")


async def _run(master_token: str, speaker_token: str | None) -> None:
    """Launch the master bot and optionally the speaker bot.

    Args:
        master_token: Discord token for the master (ear) bot.
        speaker_token: Discord token for the speaker bot, or None if not configured.
    """
    if speaker_token:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(BOT.start(master_token))
            tg.create_task(_BOT_SPEAKER.start(speaker_token))
    else:
        await BOT.start(master_token)


def main() -> int:
    """Start the bot.

    Returns:
        Exit code returned to the operating system.
    """
    global _BOT_SPEAKER, _BRIDGE  # noqa: PLW0603

    master_token = os.getenv("MAGIC")
    if not master_token:
        return 1

    speaker_token = os.getenv("SPEAKER_TOKEN")
    if speaker_token:
        _BOT_SPEAKER = commands.Bot(command_prefix="!", intents=_INTENTS)
        _BRIDGE = AudioBridge(bot_speaker=_BOT_SPEAKER)
        BOT.tree.add_command(Relay(bridge=_BRIDGE))

        @_BOT_SPEAKER.event
        async def on_speaker_ready() -> None:
            _log_online(_BOT_SPEAKER, "SPEAKER")

        log("[BOT] Speaker token found — relay commands enabled")
    else:
        log("[BOT] No SPEAKER_TOKEN — relay commands disabled, all other commands available")

    asyncio.run(_run(master_token, speaker_token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
