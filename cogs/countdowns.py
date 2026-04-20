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
MAX_SELECT_OPTIONS = 25


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
    embed.set_footer(text=f"{countdown['name']} countdown")
    return embed


def _is_admin(interaction: discord.Interaction) -> bool:
    return interaction.permissions.manage_channels


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
                "PREFER_DATES_FROM": "future",
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


class CountdownPanelView(discord.ui.View):
    """Ephemeral panel shown by /lance countdown. Browse + admin buttons."""

    def __init__(
        self, bot: commands.Bot,
        countdowns: list,
        is_admin: bool,
        page: int = 0,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.countdowns = countdowns
        self.page = page
        self.is_admin = is_admin

        # Add select menu if there are countdowns to show
        if countdowns:
            self.select = discord.ui.Select(
                placeholder="Pick a countdown to post...",
                options=[],
            )
            self.select.callback = self._on_select
            self.add_item(self.select)

        # Admin buttons
        if is_admin:
            create_btn = discord.ui.Button(
                label="Create New",
                style=discord.ButtonStyle.green,
                emoji="\N{HEAVY PLUS SIGN}",
            )
            create_btn.callback = self._on_create
            self.add_item(create_btn)

            if countdowns:
                delete_btn = discord.ui.Button(
                    label="Delete",
                    style=discord.ButtonStyle.danger,
                    emoji="\N{WASTEBASKET}",
                )
                delete_btn.callback = self._on_delete
                self.add_item(delete_btn)

        self._sync_page()

    def _sync_page(self) -> None:
        if not self.countdowns:
            self.prev_page.disabled = True
            self.next_page.disabled = True
            return

        start = self.page * MAX_SELECT_OPTIONS
        end = start + MAX_SELECT_OPTIONS
        page_count = max(1, (len(self.countdowns) + MAX_SELECT_OPTIONS - 1) // MAX_SELECT_OPTIONS)

        self.select.options = [
            discord.SelectOption(label=cd["label"], value=cd["name"])
            for cd in self.countdowns[start:end]
        ]
        self.select.placeholder = f"Pick a countdown to post... ({start + 1}-{min(end, len(self.countdowns))} of {len(self.countdowns)})"
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= page_count - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="\N{STOPWATCH} Countdowns",
            color=discord.Color.blue(),
        )
        if not self.countdowns:
            embed.description = "No countdowns configured yet."
            return embed

        start = self.page * MAX_SELECT_OPTIONS
        end = start + MAX_SELECT_OPTIONS
        page_count = max(1, (len(self.countdowns) + MAX_SELECT_OPTIONS - 1) // MAX_SELECT_OPTIONS)
        lines = [
            f"**{cd['label']}** -- <t:{cd['timestamp']}:R>"
            for cd in self.countdowns[start:end]
        ]
        embed.description = "\n".join(lines)
        if page_count > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{page_count}")
        return embed

    async def _on_select(self, interaction: discord.Interaction):
        name = self.select.values[0]
        cd = await db.get_countdown(self.bot.db, interaction.guild_id, name)
        if cd is None:
            await interaction.response.edit_message(
                content="That countdown no longer exists.", view=None,
            )
            return
        embed = _build_countdown_embed(cd)
        await interaction.response.edit_message(
            content="Posted!", embed=None, view=None,
        )
        await interaction.followup.send(embed=embed)

    async def _on_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateCountdownModal(self.bot))

    async def _on_delete(self, interaction: discord.Interaction):
        all_countdowns = await db.get_all_countdowns(
            self.bot.db, interaction.guild_id,
        )
        if not all_countdowns:
            await interaction.response.edit_message(
                content="No countdowns to delete.", view=None,
            )
            return

        view = DeleteCountdownView(self.bot, all_countdowns)
        await interaction.response.edit_message(
            content="Select a countdown to delete:",
            embed=None,
            view=view,
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_page()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_page()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class DeleteCountdownView(discord.ui.View):
    """Ephemeral select menu for deleting a countdown."""

    def __init__(self, bot: commands.Bot, countdowns: list, page: int = 0):
        super().__init__(timeout=60)
        self.bot = bot
        self.countdowns = countdowns
        self.page = page
        self.select = discord.ui.Select(
            placeholder="Select countdown to delete...",
            options=[],
        )
        self.select.callback = self.on_select
        self.add_item(self.select)
        self._sync_page()

    def _sync_page(self) -> None:
        start = self.page * MAX_SELECT_OPTIONS
        end = start + MAX_SELECT_OPTIONS
        page_count = max(1, (len(self.countdowns) + MAX_SELECT_OPTIONS - 1) // MAX_SELECT_OPTIONS)
        self.select.options = [
            discord.SelectOption(
                label=f"{cd['name']} -- {cd['label']}",
                value=cd["name"],
            )
            for cd in self.countdowns[start:end]
        ]
        self.select.placeholder = f"Select countdown to delete... ({start + 1}-{min(end, len(self.countdowns))} of {len(self.countdowns)})"
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= page_count - 1

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

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_page()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_page()
        await interaction.response.edit_message(view=self)


# -- Commands -----------------------------------------------------------------


async def setup(bot: commands.Bot):
    from cogs import lance

    @lance.command(name="countdown", description="View or manage event countdown timers")
    @app_commands.describe(name="Countdown name (omit to browse)")
    async def countdown(interaction: discord.Interaction, name: str | None = None):
        # Direct lookup by name -- post publicly
        if name is not None:
            cd = await db.get_countdown(bot.db, interaction.guild_id, name)
            if cd is None:
                all_countdowns = await db.get_all_countdowns(
                    bot.db, interaction.guild_id,
                )
                if all_countdowns:
                    names = ", ".join(f"`{c['name']}`" for c in all_countdowns)
                    await interaction.response.send_message(
                        f"No countdown named `{name}`. Available: {names}",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "No countdowns configured.", ephemeral=True,
                    )
                return
            embed = _build_countdown_embed(cd)
            await interaction.response.send_message(embed=embed)
            return

        # No name -- show the panel
        all_countdowns = await db.get_all_countdowns(bot.db, interaction.guild_id)
        admin = _is_admin(interaction)

        # Single countdown, non-admin -- just post it
        if len(all_countdowns) == 1 and not admin:
            embed = _build_countdown_embed(all_countdowns[0])
            await interaction.response.send_message(embed=embed)
            return

        if not all_countdowns and not admin:
            await interaction.response.send_message(
                "No countdowns configured.", ephemeral=True,
            )
            return

        view = CountdownPanelView(bot, all_countdowns, admin)
        await interaction.response.send_message(
            embed=view.build_embed(), view=view, ephemeral=True,
        )
