import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

import config

log = logging.getLogger("lance.streams")


class Streams(commands.Cog):
    """Announces when a member starts or stops streaming in a voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track active streams: {guild_id: {user_id: announcement_message}}
        self.active_streams: dict[int, dict[int, discord.Message]] = {}

    # -- helpers --------------------------------------------------------------

    def _get_announce_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for ch in guild.text_channels:
            if ch.name == config.STREAM_CHANNEL_NAME:
                return ch
        return None

    def _get_stream_role(self, guild: discord.Guild) -> discord.Role | None:
        for role in guild.roles:
            if role.name == config.STREAM_ROLE_NAME:
                return role
        return None

    def _get_playing_activity(self, member: discord.Member) -> str | None:
        """Return the name of the game/app the member is playing, if any."""
        for activity in member.activities:
            if activity.type == discord.ActivityType.playing and activity.name:
                return activity.name
        return None

    async def _create_vc_invite(self, channel: discord.VoiceChannel) -> str | None:
        try:
            invite = await channel.create_invite(max_age=0, max_uses=0, unique=False)
            return invite.url
        except discord.HTTPException:
            log.warning("Could not create invite for %s", channel.name)
            return None

    # -- events ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        was_streaming = before.self_stream
        now_streaming = after.self_stream

        if not was_streaming and now_streaming:
            await self._on_stream_start(member, after)
        elif was_streaming and not now_streaming:
            # Covers both "stopped streaming" and "disconnected while streaming"
            # (Discord omits self_stream from disconnect payloads, so
            # discord.py defaults it to False)
            await self._on_stream_end(member)
        elif was_streaming and now_streaming and before.channel != after.channel:
            # Switched VCs while still streaming -- update the announcement
            await self._on_stream_end(member)
            await self._on_stream_start(member, after)

    async def _on_stream_start(self, member: discord.Member, state: discord.VoiceState):
        guild = member.guild
        channel = self._get_announce_channel(guild)
        if channel is None:
            log.warning("No #%s channel in %s", config.STREAM_CHANNEL_NAME, guild.name)
            return

        vc = state.channel
        invite_url = await self._create_vc_invite(vc)

        game = self._get_playing_activity(member)
        if game:
            description = (
                f"**{member.display_name}** is now streaming **{game}** in **{vc.name}**"
            )
        else:
            description = (
                f"**{member.display_name}** is now streaming in **{vc.name}**"
            )

        embed = discord.Embed(
            title="Someone went live!",
            description=description,
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if invite_url:
            embed.description += f"\n\n[Click to join channel]({invite_url})"

        role = self._get_stream_role(guild)
        role_ping = role.mention if role else ""

        msg = await channel.send(content=role_ping, embed=embed)

        self.active_streams.setdefault(guild.id, {})[member.id] = msg
        log.info("Stream started: %s in %s (#%s)", member, vc.name, guild.name)

    async def _on_stream_end(self, member: discord.Member):
        guild = member.guild
        guild_streams = self.active_streams.get(guild.id, {})
        msg = guild_streams.pop(member.id, None)

        if msg is None:
            return

        embed = discord.Embed(
            title="Stream ended",
            description=f"**{member.display_name}**'s stream has ended.",
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await msg.edit(content="", embed=embed)
        except discord.HTTPException:
            # Original message may have been deleted; post a new one
            channel = self._get_announce_channel(guild)
            if channel:
                await channel.send(embed=embed)

        log.info("Stream ended: %s (#%s)", member, guild.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Streams(bot))
