"""Boss command group for Evergale BOT."""

import random
from pathlib import Path

import discord
from discord import app_commands

from evergale_bot.src.logger import log

_BOSS_FILE: Path = Path("bosses.txt")


def _read_bosses() -> list[str] | None:
    """Read boss list from disk.

    Returns:
        List of boss names, or None on I/O error.
    """
    if not _BOSS_FILE.exists():
        return []
    try:
        with _BOSS_FILE.open("r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return None


def _write_bosses(bosses: list[str]) -> bool:
    """Write boss list to disk.

    Args:
        bosses: List of boss names to persist.

    Returns:
        True on success, False on I/O error.
    """
    try:
        with _BOSS_FILE.open("w", encoding="utf-8") as f:
            for entry in bosses:
                f.write(f"{entry}\n")
        return True
    except OSError:
        return False


def _find_boss_index(bosses: list[str], identifier: str) -> int:
    """Return the 0-based index of the matching boss, or -1 if not found.

    Args:
        bosses: Current boss list.
        identifier: Numeric index string or boss name.

    Returns:
        Matching index, or -1.
    """
    if identifier.isdigit():
        idx = int(identifier)
        if 1 <= idx <= len(bosses):
            return idx - 1
        return -1
    lower = identifier.lower()
    for i, entry in enumerate(bosses):
        if entry.lower() == lower:
            return i
    return -1


class Boss(app_commands.Group, name="boss", description="Boss database management"):
    """Slash command group for managing the boss list."""

    @app_commands.command(name="list", description="List all bosses")
    async def list(self, interaction: discord.Interaction) -> None:
        """Display the ordered boss list.

        Args:
            interaction: The Discord interaction context.
        """
        log(f"[BOSS] List requested by @{interaction.user.display_name}")
        await interaction.response.defer(ephemeral=True)
        bosses = _read_bosses()
        if bosses is None:
            await interaction.followup.send("An error occurred while reading the boss list.",
                                            ephemeral=True)
            return
        if not bosses:
            await interaction.followup.send("The boss list is currently empty.", ephemeral=True)
            return
        lines = ["### 🐉 Boss List"] + [f"{i}. {e}" for i, e in enumerate(bosses, 1)]
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 1900:
                await interaction.followup.send(chunk, ephemeral=True)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            await interaction.followup.send(chunk, ephemeral=True)

    @app_commands.command(name="add", description="Add a boss")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(name="The name of the boss to add (can contain spaces)")
    async def add(self, interaction: discord.Interaction, name: str) -> None:
        """Add a boss to the list.

        Args:
            interaction: The Discord interaction context.
            name: Boss name to add.
        """
        boss_name = name.strip()
        log(f'[BOSS] Add requested by @{interaction.user.display_name} -> "{boss_name}"')
        await interaction.response.defer(ephemeral=True)
        if not boss_name:
            await interaction.followup.send("Boss name cannot be empty.", ephemeral=True)
            return
        try:
            with _BOSS_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{boss_name}\n")
        except OSError:
            log(f"[BOSS] Add failed for @{interaction.user.display_name}")
            await interaction.followup.send("An error occurred while saving the boss.",
                                            ephemeral=True)
            return
        await interaction.followup.send(f"Successfully added **{boss_name}** to the boss list!",
                                        ephemeral=True)

    @app_commands.command(name="remove", description="Remove a boss")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(identifier="Exact name or list number of the boss to remove")
    async def remove(self, interaction: discord.Interaction, identifier: str) -> None:
        """Remove a boss by name or index.

        Args:
            interaction: The Discord interaction context.
            identifier: Boss name or 1-based index.
        """
        identifier = identifier.strip()
        log(f'[BOSS] Remove requested by @{interaction.user.display_name} -> "{identifier}"')
        await interaction.response.defer(ephemeral=True)
        bosses = _read_bosses()
        if bosses is None:
            await interaction.followup.send("An error occurred while reading the boss list.",
                                            ephemeral=True)
            return
        if not bosses:
            await interaction.followup.send("The boss list is already empty.", ephemeral=True)
            return
        target_index = _find_boss_index(bosses, identifier)
        if target_index == -1:
            await interaction.followup.send(
                f"Could not find a boss matching **{identifier}**.", ephemeral=True)
            return
        removed = bosses.pop(target_index)
        if not _write_bosses(bosses):
            await interaction.followup.send("An error occurred while saving the updated list.",
                                            ephemeral=True)
            return
        await interaction.followup.send(f"Successfully removed **{removed}** from the boss list!",
                                        ephemeral=True)

    @app_commands.command(name="random", description="Get a random boss")
    async def random(self, interaction: discord.Interaction) -> None:
        """Pick a random boss from the list.

        Args:
            interaction: The Discord interaction context.
        """
        log(f"[BOSS] Random requested by @{interaction.user.display_name}")
        await interaction.response.defer()
        bosses = _read_bosses()
        if bosses is None:
            await interaction.followup.send("An error occurred while reading the boss list.",
                                            ephemeral=True)
            return
        if not bosses:
            await interaction.followup.send("The boss list is currently empty.", ephemeral=True)
            return
        selected = random.choice(bosses)
        await interaction.followup.send(f"🎲 The randomly selected boss is: **{selected}**!")
