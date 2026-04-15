"""Event countdown timers using Discord's native timestamp rendering.

Admins create named countdowns with a target date/time. Anyone can view
them via `/lance countdown [name]`. Discord renders `<t:UNIX:R>` as a
live client-side countdown -- no message editing or polling needed.
"""

import logging
from datetime import datetime, timezone

import dateparser
import discord
from discord import app_commands
from discord.ext import commands

import config
import db

log = logging.getLogger("lance.countdowns")


def _build_countdown_embed(countdown) -> discord.Embed:
    ts = countdown["timestamp"]
    now = int(datetime.now(timezone.utc).timestamp())
    past = ts <= now

    embed = discord.Embed(
        title=countdown["label"],
        description=(
            f"<t:{ts}:F>\n"
            f"{'Ended ' if past else ''}<t:{ts}:R>"
        ),
        color=discord.Color.dark_grey() if past else discord.Color.blue(),
    )
    embed.set_footer(text=f"{config.BOT_NAME} -- {countdown['name']}")
    return embed


# -- Modals -------------------------------------------------------------------


class CreateCountdownModal(discord.ui.Modal, title="Create Countdown"):
    name = discord.ui.TextInput(
        label="Name (short identifier)",
        placeholder="e.g. launch, playtest, event1",
        required=True,
        max_length=32,
    )
    label = discord.ui.TextInput(
        label="Display title",
        placeholder="e.g. Arc Raiders Early Access",
        required=True,
        max_length=100,
    )
    time = discord.ui.TextInput(
        label="Date and time (in your timezone)",
        placeholder="e.g. 2026-05-01 18:00, May 1 6pm, next Friday 8pm",
        required=True,
        max_length=64,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        profile = await db.get_profile(self.bot.db, interaction.user.id)
        user_tz = profile["timezone"] if profile and profile["timezone"] else None

        if user_tz is None:
            await interaction.response.send_message(
                "You haven't set your timezone yet -- the date/time would be "
                "interpreted as UTC. Set it first with `/lance settings`.",
                ephemeral=True,
            )
            return

        parsed = dateparser.parse(
            self.time.value,
            settings={
                "TIMEZONE": user_tz,
                "TO_TIMEZONE": "UTC",
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )
        if parsed is None:
            await interaction.response.send_message(
                f"Couldn't parse `{self.time.value}` as a date/time. "
                f"Try something like `2026-05-01 18:00` or `next Friday 8pm`.",
                ephemeral=True,
            )
            return

        ts = int(parsed.timestamp())
        clean_name = db.normalize_countdown_name(self.name.value)

        await db.create_countdown(
            self.bot.db, interaction.guild_id, clean_name,
            self.label.value.strip(), ts, interaction.user.id,
        )

        embed = _build_countdown_embed(
            {"name": clean_name, "label": self.label.value.strip(), "timestamp": ts},
        )
        await interaction.response.send_message(
            f"Countdown `{clean_name}` set!", embed=embed, ephemeral=True,
        )


# -- Views --------------------------------------------------------------------


class CountdownSelectView(discord.ui.View):
    """Ephemeral select menu for viewing a countdown."""

    def __init__(self, bot: commands.Bot, options: list[discord.SelectOption]):
        super().__init__(timeout=60)
        self.bot = bot
        self.select = discord.ui.Select(
            placeholder="Pick a countdown...",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        name = self.select.values[0]
        cd = await db.get_countdown(self.bot.db, interaction.guild_id, name)
        if cd is None:
            await interaction.response.edit_message(
                content="That countdown no longer exists.", view=None,
            )
            return
        embed = _build_countdown_embed(cd)
        # Post publicly so others can see it
        await interaction.response.edit_message(content=None, view=None)
        await interaction.followup.send(embed=embed)


class DeleteCountdownView(discord.ui.View):
    """Ephemeral select menu for deleting a countdown."""

    def __init__(self, bot: commands.Bot, options: list[discord.SelectOption]):
        super().__init__(timeout=60)
        self.bot = bot
        self.select = discord.ui.Select(
            placeholder="Select countdown to delete...",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        name = self.select.values[0]
        deleted = await db.delete_countdown(self.bot.db, interaction.guild_id, name)
        if deleted:
            await interaction.response.edit_message(
                content=f"Countdown `{name}` deleted.", view=None,
            )
        else:
            await interaction.response.edit_message(
                content=f"Countdown `{name}` no longer exists.", view=None,
            )


# -- Commands -----------------------------------------------------------------


async def setup(bot: commands.Bot):
    from cogs import lance

    @lance.command(name="countdown", description="Show an event countdown timer")
    @app_commands.describe(name="Countdown name (omit to browse)")
    async def countdown(interaction: discord.Interaction, name: str | None = None):
        all_countdowns = await db.get_all_countdowns(bot.db, interaction.guild_id)

        if not all_countdowns:
            await interaction.response.send_message(
                "No countdowns configured. An admin can create one with "
                "`/lance countdown-create`.",
                ephemeral=True,
            )
            return

        if name is not None:
            cd = await db.get_countdown(bot.db, interaction.guild_id, name)
            if cd is None:
                names = ", ".join(f"`{c['name']}`" for c in all_countdowns)
                await interaction.response.send_message(
                    f"No countdown named `{name}`. Available: {names}",
                    ephemeral=True,
                )
                return
            embed = _build_countdown_embed(cd)
            await interaction.response.send_message(embed=embed)
            return

        # No name given
        if len(all_countdowns) == 1:
            embed = _build_countdown_embed(all_countdowns[0])
            await interaction.response.send_message(embed=embed)
            return

        # Multiple -- show a select menu
        options = [
            discord.SelectOption(
                label=cd["label"],
                value=cd["name"],
            )
            for cd in all_countdowns[:25]
        ]
        view = CountdownSelectView(bot, options)
        await interaction.response.send_message(
            "Which countdown?", view=view, ephemeral=True,
        )

    @lance.command(
        name="countdown-create",
        description="Create or update an event countdown (admin)",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def countdown_create(interaction: discord.Interaction):
        await interaction.response.send_modal(CreateCountdownModal(bot))

    @lance.command(
        name="countdown-delete",
        description="Delete an event countdown (admin)",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def countdown_delete(interaction: discord.Interaction):
        all_countdowns = await db.get_all_countdowns(bot.db, interaction.guild_id)
        if not all_countdowns:
            await interaction.response.send_message(
                "No countdowns to delete.", ephemeral=True,
            )
            return

        options = [
            discord.SelectOption(
                label=f"{cd['name']} -- {cd['label']}",
                value=cd["name"],
            )
            for cd in all_countdowns[:25]
        ]
        view = DeleteCountdownView(bot, options)
        await interaction.response.send_message(
            "Select a countdown to delete:", view=view, ephemeral=True,
        )
