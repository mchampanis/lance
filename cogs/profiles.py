"""User profile management: /lance settings command.

Stores per-user Embark ID and timezone. Profiles are global per Discord user
(not per-guild) since both fields are intrinsic to the user, not the server.
"""

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

import config
import db

log = logging.getLogger("lance.profiles")


# -- Timezone resolution -----------------------------------------------------

# Common, unambiguous abbreviations -> IANA names.
# Maps to the city that best represents the rule (DST behaviour matters).
TIMEZONE_ALIASES: dict[str, str] = {
    "UTC": "UTC",
    "GMT": "Etc/GMT",
    "Z": "UTC",
    # Europe
    "CET": "Europe/Berlin",
    "CEST": "Europe/Berlin",
    "EET": "Europe/Helsinki",
    "EEST": "Europe/Helsinki",
    "WET": "Europe/Lisbon",
    "WEST": "Europe/Lisbon",
    "MSK": "Europe/Moscow",
    # Asia
    "JST": "Asia/Tokyo",
    "KST": "Asia/Seoul",
    "HKT": "Asia/Hong_Kong",
    "SGT": "Asia/Singapore",
    "PHT": "Asia/Manila",
    # Oceania
    "AEDT": "Australia/Sydney",
    "AEST": "Australia/Sydney",
    "ACDT": "Australia/Adelaide",
    "ACST": "Australia/Adelaide",
    "AWST": "Australia/Perth",
    "NZST": "Pacific/Auckland",
    "NZDT": "Pacific/Auckland",
    # Africa
    "CAT": "Africa/Harare",
    "EAT": "Africa/Nairobi",
    "WAT": "Africa/Lagos",
    "SAST": "Africa/Johannesburg",
    # Americas (US-centric -- "EST" almost always means New York in chat)
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "AKST": "America/Anchorage",
    "AKDT": "America/Anchorage",
    "HST": "Pacific/Honolulu",
    "AST": "America/Halifax",
    "ADT": "America/Halifax",
    "NST": "America/St_Johns",
    "NDT": "America/St_Johns",
    "BRT": "America/Sao_Paulo",
    "ART": "America/Argentina/Buenos_Aires",
}

# Abbreviations that mean different things in different parts of the world.
AMBIGUOUS_TIMEZONES: dict[str, str] = {
    "CST": "`America/Chicago` (US Central), `Asia/Shanghai` (China), or `America/Havana` (Cuba)",
    "IST": "`Asia/Kolkata` (India), `Asia/Jerusalem` (Israel), or `Europe/Dublin` (Ireland)",
    "BST": "`Europe/London` (British Summer Time) or `Asia/Dhaka` (Bangladesh)",
    "ECT": "`America/Guayaquil` (Ecuador) or `Europe/Paris` (Central European)",
}


class TimezoneError(ValueError):
    """Raised when a timezone string can't be resolved."""


def resolve_timezone(input_str: str) -> str:
    """Resolve a user input to an IANA timezone name.

    Accepts IANA names directly (`Europe/Amsterdam`) and a small set of
    unambiguous abbreviations (`CET`, `PST`). Rejects ambiguous abbreviations
    with a helpful error.
    """
    s = input_str.strip()
    if not s:
        raise TimezoneError("Timezone cannot be empty.")

    upper = s.upper()

    if upper in AMBIGUOUS_TIMEZONES:
        raise TimezoneError(
            f"`{s}` is ambiguous -- did you mean: {AMBIGUOUS_TIMEZONES[upper]}?"
        )

    if upper in TIMEZONE_ALIASES:
        return TIMEZONE_ALIASES[upper]

    # Try as IANA name directly (case-sensitive)
    try:
        ZoneInfo(s)
        return s
    except (ZoneInfoNotFoundError, ValueError):
        raise TimezoneError(
            f"`{s}` is not a recognised timezone. Use an IANA name like "
            f"`Europe/Amsterdam`, a common abbreviation like `CET`, or visit "
            f"the [helper page]({config.TIMEZONE_HELPER_URL})."
        )


# -- UI ----------------------------------------------------------------------


class EmbarkModal(discord.ui.Modal, title="Set Embark ID"):
    embark_id: discord.ui.TextInput = discord.ui.TextInput(
        label="Embark ID",
        placeholder="e.g. PlayerName#1234",
        required=True,
        max_length=64,
    )

    def __init__(self, cog: "Profiles"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await db.set_embark_id(self.cog.bot.db, interaction.user.id, self.embark_id.value.strip())
        embed = await self.cog.build_settings_embed(interaction.user)
        view = SettingsView(self.cog, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)


class TimezoneModal(discord.ui.Modal, title="Set Timezone"):
    timezone: discord.ui.TextInput = discord.ui.TextInput(
        label="Timezone",
        placeholder="e.g. Europe/Amsterdam, CET, PST",
        required=True,
        max_length=64,
    )

    def __init__(self, cog: "Profiles"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            resolved = resolve_timezone(self.timezone.value)
        except TimezoneError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await db.set_timezone(self.cog.bot.db, interaction.user.id, resolved)
        embed = await self.cog.build_settings_embed(interaction.user)
        view = SettingsView(self.cog, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)


class SettingsView(discord.ui.View):
    def __init__(self, cog: "Profiles", user_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your settings panel -- run `/lance settings` to open your own.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Set Embark ID", style=discord.ButtonStyle.primary, emoji="\N{VIDEO GAME}")
    async def set_embark(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbarkModal(self.cog))

    @discord.ui.button(label="Set Timezone", style=discord.ButtonStyle.primary, emoji="\N{CLOCK FACE THREE OCLOCK}")
    async def set_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TimezoneModal(self.cog))

    @discord.ui.button(label="Clear All", style=discord.ButtonStyle.danger, emoji="\N{WASTEBASKET}")
    async def clear_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.clear_embark_id(self.cog.bot.db, interaction.user.id)
        await db.clear_timezone(self.cog.bot.db, interaction.user.id)
        embed = await self.cog.build_settings_embed(interaction.user)
        view = SettingsView(self.cog, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)


# -- Cog ---------------------------------------------------------------------


class Profiles(commands.Cog):
    """User profile management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    lance = app_commands.Group(name="lance", description="Lance bot commands")

    async def build_settings_embed(self, user: discord.abc.User) -> discord.Embed:
        profile = await db.get_profile(self.bot.db, user.id)
        embark_id = profile["embark_id"] if profile and profile["embark_id"] else "*not set*"
        timezone = profile["timezone"] if profile and profile["timezone"] else "*not set*"

        embed = discord.Embed(
            title="Your Lance settings",
            description=(
                "These settings are tied to your Discord account and shared "
                "across all servers where Lance lives."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="\N{VIDEO GAME} Embark ID", value=embark_id, inline=False)
        embed.add_field(
            name="\N{CLOCK FACE THREE OCLOCK} Timezone",
            value=(
                f"{timezone}\n"
                f"-# Don't know yours? [Find your timezone]({config.TIMEZONE_HELPER_URL})"
            ),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} - settings are private to you")
        return embed

    @lance.command(name="settings", description="View or change your Lance settings")
    async def settings(self, interaction: discord.Interaction):
        embed = await self.build_settings_embed(interaction.user)
        view = SettingsView(self, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Profiles(bot))
