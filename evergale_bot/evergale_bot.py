"""Evergale BOT — entry point."""

import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

from evergale_bot.src.boss import Boss
from evergale_bot.src.config import Config
from evergale_bot.src.logger import log
from evergale_bot.src.roster import Roster
from evergale_bot.src.utility import Utility

load_dotenv()

_INTENTS: discord.Intents = discord.Intents.default()
_INTENTS.guilds = True
_INTENTS.members = True
_INTENTS.messages = True
_INTENTS.message_content = True

BOT: commands.Bot = commands.Bot(command_prefix="!", intents=_INTENTS)

BOT.tree.add_command(Boss())
BOT.tree.add_command(Roster())
BOT.tree.add_command(Utility())


@BOT.event
async def on_ready() -> None:
    """Sync slash commands to the configured guild on startup."""
    log(f"[BOT] Online as {BOT.user.display_name} ({BOT.user.id})")
    guild = discord.Object(id=Config.GUILD_ID)
    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)
    BOT.tree.clear_commands(guild=None)
    await BOT.tree.sync(guild=None)
    log(f"[BOT] Synced {len(synced)} commands to guild {Config.GUILD_ID}")


def main() -> int:
    """Start the bot.

    Returns:
        Exit code returned to the operating system.
    """
    token = os.getenv("MAGIC")
    if not token:
        return 1
    BOT.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
