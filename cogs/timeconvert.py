"""Timezone conversion via emoji reactions.

When a user reacts to a message with the configured clock emoji, Lance:
1. Finds all time-like substrings in the message
2. Parses each one in the original author's timezone
3. Converts to the reacting user's timezone
4. DMs the reacting user with the converted message
"""

import logging
import re

import dateparser
import discord
from discord.ext import commands

import config
import db

log = logging.getLogger("lance.timeconvert")

# Matches common time formats:
#   6pm, 6PM, 6 pm, 6:30pm, 6:30 PM
#   16h45, 16H45
#   18:00, 8:30
#   noon, midnight
TIME_PATTERN = re.compile(
    r"\b("
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)"   # 6pm, 6:30pm, 6 pm
    r"|\d{1,2}[hH]\d{2}"                 # 16h45
    r"|\d{1,2}:\d{2}"                    # 18:00, 8:30
    r"|noon|midnight"                     # named times
    r")\b",
    re.IGNORECASE,
)


def build_converted_text(text: str, source_tz: str, target_tz: str) -> str | None:
    """Replace all times in text with converted versions.

    Returns the annotated text, or None if no times were found.
    """
    settings = {
        "TIMEZONE": source_tz,
        "TO_TIMEZONE": target_tz,
        "RETURN_AS_TIMEZONE_AWARE": True,
    }

    parts = []
    last_end = 0

    for match in TIME_PATTERN.finditer(text):
        raw = match.group(1)
        parsed = dateparser.parse(raw, settings=settings)
        if parsed is None:
            continue
        converted = parsed.strftime("%H:%M")
        parts.append(text[last_end:match.start(1)])
        parts.append(f"**{converted}**")
        last_end = match.end(1)

    if not parts:
        return None

    parts.append(text[last_end:])
    return "".join(parts)


class TimeConvert(commands.Cog):
    """React with a clock emoji to get times converted to your timezone."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.member and payload.member.bot:
            return

        if str(payload.emoji) != config.TIME_REACT_EMOJI:
            return

        # Fetch the message
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        if not message.content:
            return

        # Look up both users' timezones
        author_profile = await db.get_profile(self.bot.db, message.author.id)
        reactor_profile = await db.get_profile(self.bot.db, payload.user_id)

        reactor = payload.member or self.bot.get_user(payload.user_id)
        if reactor is None:
            return

        if not author_profile or not author_profile["timezone"]:
            try:
                await reactor.send(
                    f"Can't convert times -- **{message.author.display_name}** hasn't set "
                    f"their timezone yet. They can do so with `/lance settings`."
                )
            except discord.Forbidden:
                pass
            return

        if not reactor_profile or not reactor_profile["timezone"]:
            try:
                await reactor.send(
                    "Can't convert times -- you haven't set your timezone yet. "
                    "Use `/lance settings` to set it."
                )
            except discord.Forbidden:
                pass
            return

        source_tz = author_profile["timezone"]
        target_tz = reactor_profile["timezone"]

        if source_tz == target_tz:
            try:
                await reactor.send(
                    f"You and **{message.author.display_name}** are in the same timezone ({source_tz})."
                )
            except discord.Forbidden:
                pass
            return

        converted = build_converted_text(message.content, source_tz, target_tz)
        if converted is None:
            try:
                await reactor.send("No times found in that message.")
            except discord.Forbidden:
                pass
            return

        embed = discord.Embed(
            description=converted,
            color=discord.Color.blue(),
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )
        embed.set_footer(
            text=f"{source_tz} -> {target_tz} | {config.BOT_NAME}",
        )

        try:
            await reactor.send(embed=embed)
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TimeConvert(bot))
