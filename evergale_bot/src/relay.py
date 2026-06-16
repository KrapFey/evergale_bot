"""Relay command group for the voice bridge feature."""

import discord
from discord import app_commands

from evergale_bot.src.audio_bridge import AudioBridge
from evergale_bot.src.logger import log


class Relay(app_commands.Group, name="relay", description="Voice relay management"):
    """Slash command group for starting and stopping the voice relay."""

    def __init__(self, bridge: AudioBridge) -> None:
        """Initialise with a shared AudioBridge instance.

        Args:
            bridge: The shared bridge that owns both voice connections.
        """
        super().__init__()
        self.__bridge: AudioBridge = bridge

    @app_commands.command(name="listen", description="Start relaying your voice to another channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(speak="Voice channel where the speaker bot will play your audio")
    async def listen(self, interaction: discord.Interaction,
                     speak: discord.VoiceChannel) -> None:
        """Join the invoker's voice channel and relay audio to the speak channel.

        Args:
            interaction: The Discord interaction context.
            speak: The voice channel Bot 2 will join and play audio in.
        """
        await interaction.response.defer(ephemeral=True)

        if self.__bridge.active:
            await interaction.followup.send("A relay is already active.", ephemeral=True)
            return

        if not interaction.user.voice:
            await interaction.followup.send("You are not in a voice channel.", ephemeral=True)
            return

        listen_ch = interaction.user.voice.channel
        log(f"[RELAY] Listen requested by @{interaction.user.display_name} "
            f"-> #{listen_ch.name} | speak: #{speak.name}")

        await self.__bridge.start(
            invoker=interaction.user,
            listen_ch=listen_ch,
            speak_ch=speak,
        )

        await interaction.followup.send(
            f"Relay active — listening in **#{listen_ch.name}**, "
            f"speaking in **#{speak.name}**.",
            ephemeral=True,
        )

    @app_commands.command(name="stop_listening", description="Stop the active voice relay")
    @app_commands.default_permissions(administrator=True)
    async def stop_listening(self, interaction: discord.Interaction) -> None:
        """Disconnect both bots and stop the relay.

        Args:
            interaction: The Discord interaction context.
        """
        await interaction.response.defer(ephemeral=True)

        if not self.__bridge.active:
            await interaction.followup.send("No relay is currently active.", ephemeral=True)
            return

        if interaction.user.id != self.__bridge.invoker_id:
            await interaction.followup.send(
                "Only the person who started the relay can stop it.", ephemeral=True)
            return

        log(f"[RELAY] Stop requested by @{interaction.user.display_name}")
        await self.__bridge.teardown("stopped by user")
        await interaction.followup.send("Relay stopped.", ephemeral=True)
