"""Community giveaway board.

A single live-updating embed per guild tracks available items. Users can
list items, claim them, and manage their listings via buttons or slash
commands.

Lifecycle of an item:
  1. User posts via Give button or /lance give
  2. Item appears on the board as "available"
  3. Another user clicks Claim -> joins a queue (ordered by time)
  4. First in queue is presented to the lister via DM (Accept/Decline)
  5. Accept -> item removed from the board, remaining queue notified
  6. Decline -> claimer notified, next in queue presented
  7. Lister can remove an item at any time (remaining queue notified)
  8. Items auto-expire after GIVEAWAY_EXPIRY_HOURS (default 120)
  9. Gone items purged from DB after 7 days
"""

import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db

log = logging.getLogger("lance.giveaways")

MAX_SELECT_OPTIONS = 25
BLUEPRINT_PATTERN = re.compile(r"\b(bp|blueprint)s?\b", re.IGNORECASE)


async def _dismiss_ephemeral(interaction: discord.Interaction) -> None:
    """Delete the ephemeral message this component interaction was attached to."""
    try:
        await interaction.response.defer()
        await interaction.delete_original_response()
    except discord.HTTPException:
        try:
            await interaction.edit_original_response(
                content="Cancelled.", embed=None, view=None,
            )
        except discord.HTTPException:
            pass


def _item_emoji(item_name: str, guild: discord.Guild) -> str:
    """Return the emoji for an item: custom :blueprint: if matched, else a package."""
    if BLUEPRINT_PATTERN.search(item_name):
        bp_emoji = discord.utils.get(guild.emojis, name="blueprint")
        if bp_emoji:
            return str(bp_emoji)
    return "\N{PACKAGE}"


# -- Helpers ------------------------------------------------------------------


def _age(created_at_str: str) -> str:
    """Convert a DB timestamp string to a human-readable relative age."""
    created = datetime.fromisoformat(created_at_str).replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    hours = int(delta.total_seconds() / 3600)
    if hours < 1:
        mins = max(1, int(delta.total_seconds() / 60))
        return f"{mins}m ago"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# -- Board embed builder ------------------------------------------------------


async def build_board_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    items = await db.get_available_items(bot.db, guild.id)

    embed = discord.Embed(
        title="\N{WRAPPED PRESENT} Community Giveaways",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )

    if not items:
        embed.description = (
            "No items available right now.\n\n"
            "Click **Add New Item** below or use `/lance give` to list something!"
        )
    else:
        lines = []
        for item in items:
            member = guild.get_member(item["user_id"])
            who = member.mention if member else f"<@{item['user_id']}>"

            created_dt = datetime.fromisoformat(item["created_at"]).replace(tzinfo=timezone.utc)
            ts = int(created_dt.timestamp())

            emoji = _item_emoji(item["item_name"], guild)
            line = (
                f"{emoji} **{item['item_name']}**\n"
                f"-# from {who} · <t:{ts}:R>"
            )

            # Show accepted claims
            accepted = await db.get_accepted_claims_for_item(bot.db, item["id"])
            for claim in accepted:
                claimer = guild.get_member(claim["claimer_id"])
                claimer_name = claimer.mention if claimer else f"<@{claim['claimer_id']}>"
                line += f"\n> \N{WHITE HEAVY CHECK MARK} Promised to {claimer_name}"

            # Show queue size
            pending = await db.get_pending_claims_for_item(bot.db, item["id"])
            if pending:
                line += f"\n> \N{RAISED HAND} {len(pending)} in queue"

            lines.append(line)

        embed.description = "\n\n".join(lines)

    embed.set_footer(
        text=f"Items expire after {config.GIVEAWAY_EXPIRY_HOURS}h"
    )
    return embed


async def refresh_board(bot: commands.Bot, guild: discord.Guild) -> None:
    """Re-render and edit the board message for a guild."""
    board = await db.get_giveaway_board(bot.db, guild.id)
    if board is None:
        return

    channel = guild.get_channel(board["channel_id"])
    if channel is None:
        return

    embed = await build_board_embed(bot, guild)
    view = BoardView(guild.id)
    try:
        msg = await channel.fetch_message(board["message_id"])
        await msg.edit(embed=embed, view=view)
    except discord.NotFound:
        # Board message was deleted -- post a new one
        msg = await channel.send(embed=embed, view=view)
        await db.set_giveaway_board(bot.db, guild.id, channel.id, msg.id)
    except discord.HTTPException:
        log.warning("Failed to refresh giveaway board in guild %s", guild.id)


async def _send_next_claim_dm(bot: commands.Bot, item, guild: discord.Guild) -> None:
    """DM the item lister about the next pending claim in the queue, if any."""
    pending = await db.get_pending_claims_for_item(bot.db, item["id"])
    if not pending:
        return

    next_claim = pending[0]

    lister = await _resolve_member(guild, item["user_id"])
    if lister is None:
        return

    claimer = await _resolve_member(guild, next_claim["claimer_id"])

    claimer_name = claimer.display_name if claimer else f"User {next_claim['claimer_id']}"

    embed = discord.Embed(
        title="Next in queue for your item!",
        description=(
            f"**{claimer_name}** wants your **{item['item_name']}**"
        ),
        color=discord.Color.gold(),
    )
    if claimer:
        embed.set_thumbnail(url=claimer.display_avatar.url)

    queue_remaining = len(pending) - 1
    if queue_remaining > 0:
        embed.set_footer(text=f"{queue_remaining} more in queue")

    view = ClaimResponseView(next_claim["id"])
    try:
        await lister.send(embed=embed, view=view)
    except discord.Forbidden:
        log.warning("Cannot DM user %s for claim %s", lister.id, next_claim["id"])


async def _resolve_member(
    guild: discord.Guild, user_id: int,
) -> discord.Member | None:
    """Try cache first, fall back to API fetch."""
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.Forbidden, discord.HTTPException):
            pass
    return member


async def _check_milestone_roles(
    bot: commands.Bot, guild: discord.Guild, user_id: int, new_total: int,
) -> str | None:
    """Award the correct milestone role and remove any lower ones.

    Returns the name of a *newly* awarded role (i.e. one the member didn't
    already have), or None if no new role was awarded.
    """
    if not config.GIVEAWAY_MILESTONES:
        return None

    earned_role_name = None
    lower_role_names = []
    for threshold, role_name in config.GIVEAWAY_MILESTONES:
        if new_total >= threshold:
            # Everything below the current earned tier is a lower role
            if earned_role_name is not None:
                lower_role_names.append(earned_role_name)
            earned_role_name = role_name
        else:
            break

    if earned_role_name is None:
        return None

    member = await _resolve_member(guild, user_id)
    if member is None:
        return None

    # Build a lookup of guild roles by name
    guild_roles = {r.name: r for r in guild.roles}

    # Award the earned role
    newly_earned = None
    earned_role = guild_roles.get(earned_role_name)
    if earned_role and earned_role not in member.roles:
        try:
            await member.add_roles(earned_role, reason=f"Giveaway milestone: {new_total} items given")
            log.info("Awarded %s to %s (%d items given)", earned_role_name, member, new_total)
            newly_earned = earned_role_name
        except discord.Forbidden:
            log.warning("Missing permissions to assign role %s", earned_role_name)

    # Remove lower milestone roles
    for name in lower_role_names:
        role = guild_roles.get(name)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Superseded by {earned_role_name}")
            except discord.Forbidden:
                pass

    return newly_earned


async def _complete_handoff(
    bot: commands.Bot, claim, item, guild: discord.Guild,
) -> tuple[int, str | None]:
    """Called when both giver and taker confirm. Increments stats and checks milestones.

    Returns (new_total, newly_earned_role_name).
    """
    new_total = await db.increment_items_given(bot.db, guild.id, item["user_id"])
    newly_earned = await _check_milestone_roles(bot, guild, item["user_id"], new_total)
    log.info(
        "Handoff complete: item %d, claim %d, giver %d now at %d total",
        item["id"], claim["id"], item["user_id"], new_total,
    )
    return new_total, newly_earned


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _page_count(total_items: int) -> int:
    return max(1, (total_items + MAX_SELECT_OPTIONS - 1) // MAX_SELECT_OPTIONS)


def _page_bounds(page: int, total_items: int) -> tuple[int, int]:
    start = page * MAX_SELECT_OPTIONS
    end = min(start + MAX_SELECT_OPTIONS, total_items)
    return start, end


# -- Modals -------------------------------------------------------------------


class GiveItemModal(discord.ui.Modal, title="Give Away Items"):
    item_names: discord.ui.TextInput = discord.ui.TextInput(
        label="Item name(s)",
        placeholder="e.g. Titanium Plate, Salvaged Motor, Polymer Sheet",
        required=True,
        max_length=300,
    )

    def __init__(self, cog: "Giveaways"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        names = [n.strip() for n in self.item_names.value.split(",") if n.strip()]
        if not names:
            await interaction.response.send_message("Item name can't be empty.", ephemeral=True)
            return

        for name in names:
            await db.create_item(
                self.cog.bot.db, interaction.guild_id, interaction.user.id, name,
            )

        if len(names) == 1:
            summary = f"**{names[0]}**"
        else:
            summary = ", ".join(f"**{n}**" for n in names)
        await interaction.response.send_message(
            f"Listed {summary} on the giveaway board!",
            ephemeral=True,
        )
        await refresh_board(self.cog.bot, interaction.guild)


# -- Persistent board buttons (survive restart) -------------------------------


class BoardView(discord.ui.View):
    """Attached to the giveaway board message. Buttons use DynamicItem for persistence."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.add_item(GiveButton(guild_id))
        self.add_item(ClaimButton(guild_id))
        self.add_item(MyItemsButton(guild_id))
        self.add_item(MyClaimsButton(guild_id))


class GiveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:give:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="Add New Item",
                style=discord.ButtonStyle.green,
                custom_id=f"giveaway:give:{guild_id}",
                emoji="\N{PACKAGE}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        await interaction.response.send_modal(GiveItemModal(cog))


class ClaimButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:claim:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="Claim an item",
                style=discord.ButtonStyle.primary,
                custom_id=f"giveaway:claim:{guild_id}",
                emoji="\N{SHOPPING TROLLEY}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        items = await db.get_available_items(cog.bot.db, self.guild_id)
        # Exclude the user's own items (unless testing)
        if not config.TESTING:
            items = [i for i in items if i["user_id"] != interaction.user.id]

        if not items:
            await interaction.response.send_message(
                "Nothing available to claim right now.", ephemeral=True,
            )
            return

        view = ClaimSelectView(cog, interaction.guild, items)
        await interaction.response.send_message(
            "Select an item to claim:", view=view, ephemeral=True,
        )


class MyItemsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:mine:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="My Giveaways",
                style=discord.ButtonStyle.secondary,
                custom_id=f"giveaway:mine:{guild_id}",
                emoji="\N{MEMO}",
                row=1,
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        items = await db.get_user_items(cog.bot.db, self.guild_id, interaction.user.id)

        if not items:
            await interaction.response.send_message(
                "You have no active giveaway listings.", ephemeral=True,
            )
            return

        view = ManageSelectView(cog, interaction.guild, items)
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class MyClaimsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:myclaims:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="My Claims",
                style=discord.ButtonStyle.secondary,
                custom_id=f"giveaway:myclaims:{guild_id}",
                emoji="\N{RAISED HAND}",
                row=1,
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        claims = await db.get_user_pending_claims(
            cog.bot.db, self.guild_id, interaction.user.id,
        )
        if not claims:
            await interaction.response.send_message(
                "You have no pending claims.", ephemeral=True,
            )
            return

        view = CancelClaimSelectView(cog, interaction.guild, claims)
        embed = await view.build_embed()
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True,
        )


# -- Ephemeral views (not persistent, timeout OK) ----------------------------


class CancelClaimSelectView(discord.ui.View):
    def __init__(self, cog: "Giveaways", guild: discord.Guild, claims: list, page: int = 0):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild
        self.claims = claims
        self.page = page
        self.select = discord.ui.Select(
            placeholder="Select a claim to cancel...",
            options=[],
        )
        self.select.callback = self.on_select
        self.add_item(self.select)
        self._sync_page()

    async def build_embed(self) -> discord.Embed:
        start, end = _page_bounds(self.page, len(self.claims))
        lines = []
        options = []
        for claim in self.claims[start:end]:
            item = await db.get_item(self.cog.bot.db, claim["item_id"])
            if item is None:
                continue
            pending = await db.get_pending_claims_for_item(self.cog.bot.db, item["id"])
            position = next(
                (i + 1 for i, p in enumerate(pending) if p["id"] == claim["id"]),
                None,
            )
            pos_label = f"#{position} in queue"
            lines.append(f"**{item['item_name']}** -- {pos_label}")
            options.append(
                discord.SelectOption(
                    label=item["item_name"][:100],
                    description=pos_label,
                    value=str(claim["id"]),
                )
            )

        self.select.options = options
        embed = discord.Embed(
            title="Your pending claims",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        if _page_count(len(self.claims)) > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{_page_count(len(self.claims))}")
        return embed

    def _sync_page(self) -> None:
        start, end = _page_bounds(self.page, len(self.claims))
        self.select.placeholder = f"Select a claim to cancel... ({start + 1}-{end} of {len(self.claims)})"
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= _page_count(len(self.claims)) - 1

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_page()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_page()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    async def on_select(self, interaction: discord.Interaction):
        claim_id = int(self.select.values[0])
        claim = await db.get_claim(self.cog.bot.db, claim_id)
        if not claim or claim["status"] != "pending":
            await interaction.response.edit_message(
                content="That claim is no longer active.", view=None,
            )
            return
        item = await db.get_item(self.cog.bot.db, claim["item_id"])
        item_name = item["item_name"] if item else "Unknown item"

        view = ConfirmCancelClaimView(self.cog, claim_id, item_name)
        await interaction.response.edit_message(
            content=f"Cancel your claim on **{item_name}**?",
            embed=None,
            view=view,
        )


class ConfirmCancelClaimView(discord.ui.View):
    def __init__(self, cog: "Giveaways", claim_id: int, item_name: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.claim_id = claim_id
        self.item_name = item_name

    @discord.ui.button(label="Cancel Claim", style=discord.ButtonStyle.danger, emoji="\N{CROSS MARK}")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        claim = await db.get_claim(self.cog.bot.db, self.claim_id)
        if not claim or claim["status"] != "pending":
            await interaction.response.edit_message(
                content="That claim is no longer active.", view=None,
            )
            return

        item = await db.get_item(self.cog.bot.db, claim["item_id"])
        if item is None:
            await interaction.response.edit_message(
                content="Item no longer exists.", view=None,
            )
            return

        # Check if this claimer was first in queue (their removal should advance the queue)
        pending_before = await db.get_pending_claims_for_item(self.cog.bot.db, item["id"])
        was_first = pending_before and pending_before[0]["id"] == self.claim_id

        await db.decline_claim(self.cog.bot.db, self.claim_id)

        await interaction.response.edit_message(
            content=f"Your claim on **{self.item_name}** has been cancelled.",
            view=None,
        )

        guild = interaction.guild
        if guild is not None:
            if was_first:
                # Present next person in queue to the lister
                await _send_next_claim_dm(self.cog.bot, item, guild)
            await refresh_board(self.cog.bot, guild)

    @discord.ui.button(label="Keep Claim", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)


# -- Ephemeral views (not persistent, timeout OK) ----------------------------


class ClaimSelectView(discord.ui.View):
    def __init__(self, cog: "Giveaways", guild: discord.Guild, items: list, page: int = 0):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild
        self.items = items
        self.page = page
        self.select = discord.ui.Select(
            placeholder="Choose an item...",
            options=[],
        )
        self.select.callback = self.on_select
        self.add_item(self.select)
        self._sync_page()

    def _sync_page(self) -> None:
        start, end = _page_bounds(self.page, len(self.items))
        self.select.options = [
            discord.SelectOption(
                label=item["item_name"],
                description=(
                    f"From "
                    f"{(self.guild.get_member(item['user_id']).display_name if self.guild.get_member(item['user_id']) else 'Unknown')} "
                    f"({_age(item['created_at'])})"
                ),
                value=str(item["id"]),
            )
            for item in self.items[start:end]
        ]
        self.select.placeholder = f"Choose an item... ({start + 1}-{end} of {len(self.items)})"
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= _page_count(len(self.items)) - 1

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_page()
        await interaction.response.edit_message(content="Select an item to claim:", view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_page()
        await interaction.response.edit_message(content="Select an item to claim:", view=self)

    async def on_select(self, interaction: discord.Interaction):
        item_id = int(self.select.values[0])
        item = await db.get_item(self.cog.bot.db, item_id)

        if not item or item["status"] != "available":
            await interaction.response.edit_message(content="That item is no longer available.", view=None)
            return

        # One claim per person per item
        already = await db.has_active_claim(self.cog.bot.db, item_id, interaction.user.id)
        if already:
            await interaction.response.edit_message(
                content="You already have a claim on that item.", view=None,
            )
            return

        # Confirm before creating the claim
        view = ConfirmClaimView(self.cog, item_id, item["item_name"])
        await interaction.response.edit_message(
            content=f"Claim **{item['item_name']}**?",
            view=view,
        )


class ConfirmClaimView(discord.ui.View):
    def __init__(self, cog: "Giveaways", item_id: int, item_name: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.item_id = item_id
        self.item_name = item_name

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="\N{RAISED HAND}")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await db.get_item(self.cog.bot.db, self.item_id)

        if not item or item["status"] != "available":
            await interaction.response.edit_message(
                content="That item is no longer available.", view=None,
            )
            return

        already = await db.has_active_claim(
            self.cog.bot.db, self.item_id, interaction.user.id,
        )
        if already:
            await interaction.response.edit_message(
                content="You already have a claim on that item.", view=None,
            )
            return

        # Check existing queue before creating the claim
        existing_pending = await db.get_pending_claims_for_item(
            self.cog.bot.db, self.item_id,
        )
        claim = await db.create_claim(
            self.cog.bot.db, self.item_id, interaction.user.id,
        )

        guild = interaction.guild
        if existing_pending:
            # Someone is already first in queue -- just join behind them
            position = len(existing_pending) + 1
            await interaction.response.edit_message(
                content=(
                    f"You're **#{position}** in the queue for **{self.item_name}**. "
                    f"You'll be notified when it's your turn."
                ),
                view=None,
            )
        else:
            # First in queue -- DM the lister
            await interaction.response.edit_message(
                content=f"Claim submitted for **{self.item_name}**! The owner has been notified.",
                view=None,
            )

            lister = await _resolve_member(guild, item["user_id"]) if guild else None
            dm_sent = False
            if lister is not None:
                embed = discord.Embed(
                    title="Someone wants your item!",
                    description=(
                        f"**{interaction.user.display_name}** wants your "
                        f"**{self.item_name}**"
                    ),
                    color=discord.Color.gold(),
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)

                view = ClaimResponseView(claim["id"])
                try:
                    await lister.send(embed=embed, view=view)
                    dm_sent = True
                except discord.Forbidden:
                    log.warning("Cannot DM user %s for claim %s", lister.id, claim["id"])

            if not dm_sent:
                try:
                    await interaction.user.send(
                        f"Heads up -- I couldn't DM the owner of **{self.item_name}** "
                        f"to notify them of your claim. They may have DMs disabled. "
                        f"You might need to reach out to them directly."
                    )
                except discord.Forbidden:
                    pass

        await refresh_board(self.cog.bot, guild)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)


class ClaimResponseView(discord.ui.View):
    """Sent in DM to the item lister. Accept or decline a claim. Persistent."""

    def __init__(self, claim_id: int):
        super().__init__(timeout=None)
        self.add_item(AcceptClaimButton(claim_id))
        self.add_item(DeclineClaimButton(claim_id))


class DismissDMButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:dismiss",
):
    def __init__(self):
        super().__init__(
            discord.ui.Button(
                label="Dismiss Message",
                style=discord.ButtonStyle.secondary,
                custom_id="giveaway:dismiss",
                emoji="\N{WASTEBASKET}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            await interaction.edit_original_response(
                content="Could not delete the message.", view=None,
            )


def _dismiss_view() -> discord.ui.View:
    """A view with only a Dismiss button, for terminal/orphan states."""
    view = discord.ui.View(timeout=None)
    view.add_item(DismissDMButton())
    return view


class AcceptClaimButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:accept:(?P<claim_id>\d+)",
):
    def __init__(self, claim_id: int):
        super().__init__(
            discord.ui.Button(
                label="Accept",
                style=discord.ButtonStyle.green,
                custom_id=f"giveaway:accept:{claim_id}",
                emoji="\N{WHITE HEAVY CHECK MARK}",
            )
        )
        self.claim_id = claim_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["claim_id"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        claim = await db.get_claim(bot.db, self.claim_id)
        if not claim or claim["status"] != "pending":
            await interaction.response.edit_message(
                content="This claim has already been resolved.", view=_dismiss_view(),
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        if not item:
            await interaction.response.edit_message(
                content="Item no longer exists.", view=_dismiss_view(),
            )
            return

        # Snapshot the rest of the queue before state change
        other_pending = [
            c for c in await db.get_pending_claims_for_item(bot.db, item["id"])
            if c["id"] != self.claim_id
        ]

        await db.accept_claim(bot.db, self.claim_id)
        await db.mark_item_gone(bot.db, item["id"])

        # Show Confirm Hand Over + Dismiss buttons to the lister
        confirm_view = discord.ui.View(timeout=None)
        confirm_view.add_item(ConfirmGivenButton(self.claim_id))
        confirm_view.add_item(DismissDMButton())
        await interaction.response.edit_message(
            content=(
                f"\N{WHITE HEAVY CHECK MARK} Accepted! **{item['item_name']}**.\n"
                f"Once you've handed the item over, click **Confirm Hand Over**."
            ),
            view=confirm_view,
        )

        # Notify accepted claimer with Confirm Received button
        for guild in bot.guilds:
            if guild.id == item["guild_id"]:
                claimer = await _resolve_member(guild, claim["claimer_id"])
                if claimer:
                    taker_view = discord.ui.View(timeout=None)
                    taker_view.add_item(ConfirmReceivedButton(self.claim_id))
                    try:
                        await claimer.send(
                            f"\N{WHITE HEAVY CHECK MARK} Your claim on **{item['item_name']}** "
                            f"was accepted by **{interaction.user.display_name}**! "
                            f"Reach out to them to arrange the hand over, then click "
                            f"**Confirm Received** once you have the item.",
                            view=taker_view,
                        )
                    except discord.Forbidden:
                        pass

                # Item is gone -- notify remaining queue
                for dc in other_pending:
                    dc_member = await _resolve_member(guild, dc["claimer_id"])
                    if dc_member:
                        try:
                            await dc_member.send(
                                f"\N{CROSS MARK} Your claim on **{item['item_name']}** "
                                f"was declined -- the item is no longer available."
                            )
                        except discord.Forbidden:
                            pass

                await refresh_board(bot, guild)
                break


class DeclineClaimButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:decline:(?P<claim_id>\d+)",
):
    def __init__(self, claim_id: int):
        super().__init__(
            discord.ui.Button(
                label="Decline",
                style=discord.ButtonStyle.danger,
                custom_id=f"giveaway:decline:{claim_id}",
                emoji="\N{CROSS MARK}",
            )
        )
        self.claim_id = claim_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["claim_id"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        claim = await db.get_claim(bot.db, self.claim_id)
        if not claim or claim["status"] != "pending":
            await interaction.response.edit_message(
                content="This claim has already been resolved.", view=_dismiss_view(),
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        await db.decline_claim(bot.db, self.claim_id)

        item_name = item["item_name"] if item else "Unknown item"
        await interaction.response.edit_message(
            content=f"\N{CROSS MARK} Declined the claim on **{item_name}**.",
            view=_dismiss_view(),
        )

        # Notify declined claimer, present next in queue, refresh board
        if item:
            for guild in bot.guilds:
                if guild.id == item["guild_id"]:
                    claimer = await _resolve_member(guild, claim["claimer_id"])
                    if claimer:
                        try:
                            await claimer.send(
                                f"\N{CROSS MARK} Your claim on **{item_name}** was declined."
                            )
                        except discord.Forbidden:
                            pass
                    # Present next person in the queue to the lister
                    await _send_next_claim_dm(bot, item, guild)
                    await refresh_board(bot, guild)
                    break


class ConfirmGivenButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:given:(?P<claim_id>\d+)",
):
    def __init__(self, claim_id: int):
        super().__init__(
            discord.ui.Button(
                label="Confirm Hand Over",
                style=discord.ButtonStyle.green,
                custom_id=f"giveaway:given:{claim_id}",
                emoji="\N{HANDSHAKE}",
            )
        )
        self.claim_id = claim_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["claim_id"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        claim = await db.get_claim(bot.db, self.claim_id)
        if not claim or claim["status"] != "accepted":
            await interaction.response.edit_message(
                content="This claim has already been resolved.", view=_dismiss_view(),
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        item_name = item["item_name"] if item else "Unknown item"
        # Re-running confirm_given is safe (sets flag idempotently) and
        # allows the 48h giver override to trigger on a second click
        completed = await db.confirm_given(bot.db, self.claim_id)

        if completed:
            # Complete stats/milestones first so we can include the count in the DM
            new_total = 0
            newly_earned = None
            if item:
                for guild in bot.guilds:
                    if guild.id == item["guild_id"]:
                        new_total, newly_earned = await _complete_handoff(
                            bot, claim, item, guild,
                        )
                        break

            content = (
                f"\N{HANDSHAKE} Hand over complete for **{item_name}**! "
                f"Thank you, this is your {_ordinal(new_total)} giveaway!"
            )
            if newly_earned:
                content += (
                    f"\n\n\N{PARTY POPPER} **Congratulations!** You've earned the "
                    f"**{newly_earned}** role!"
                )
            await interaction.response.edit_message(
                content=content, view=_dismiss_view(),
            )

            # Notify taker that the hand over is complete
            if item:
                for guild in bot.guilds:
                    if guild.id == item["guild_id"]:
                        taker = await _resolve_member(guild, claim["claimer_id"])
                        if taker:
                            try:
                                await taker.send(
                                    f"\N{HANDSHAKE} Hand over complete for **{item_name}**! "
                                    f"Both sides confirmed."
                                )
                            except discord.Forbidden:
                                pass
                        break
        else:
            confirm_view = discord.ui.View(timeout=None)
            confirm_view.add_item(ConfirmGivenButton(self.claim_id))
            confirm_view.add_item(DismissDMButton())
            await interaction.response.edit_message(
                content=(
                    f"\N{WHITE HEAVY CHECK MARK} You confirmed the hand over for **{item_name}**. "
                    f"Waiting for the recipient to confirm receipt.\n"
                    f"-# If they don't respond within 48h of the accept, "
                    f"click the button again to complete it."
                ),
                view=confirm_view,
            )


class ConfirmReceivedButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:received:(?P<claim_id>\d+)",
):
    def __init__(self, claim_id: int):
        super().__init__(
            discord.ui.Button(
                label="Confirm Received",
                style=discord.ButtonStyle.green,
                custom_id=f"giveaway:received:{claim_id}",
                emoji="\N{HANDSHAKE}",
            )
        )
        self.claim_id = claim_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["claim_id"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        claim = await db.get_claim(bot.db, self.claim_id)
        if not claim or claim["status"] != "accepted":
            await interaction.response.edit_message(
                content="This claim has already been resolved.", view=_dismiss_view(),
            )
            return

        if claim["taker_confirmed"]:
            await interaction.response.edit_message(
                content="You've already confirmed this receipt.", view=_dismiss_view(),
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        item_name = item["item_name"] if item else "Unknown item"
        completed = await db.confirm_received(bot.db, self.claim_id)

        if completed:
            await interaction.response.edit_message(
                content=(
                    f"\N{HANDSHAKE} Hand over complete for **{item_name}**! "
                    f"Both sides confirmed."
                ),
                view=_dismiss_view(),
            )
            # Complete stats/milestones and notify the giver
            if item:
                for guild in bot.guilds:
                    if guild.id == item["guild_id"]:
                        new_total, newly_earned = await _complete_handoff(
                            bot, claim, item, guild,
                        )
                        giver = await _resolve_member(guild, item["user_id"])
                        if giver:
                            msg = (
                                f"\N{HANDSHAKE} Hand over complete for **{item_name}**! "
                                f"Thank you, this is your {_ordinal(new_total)} giveaway!"
                            )
                            if newly_earned:
                                msg += (
                                    f"\n\n\N{PARTY POPPER} **Congratulations!** "
                                    f"You've earned the **{newly_earned}** role!"
                                )
                            try:
                                await giver.send(msg)
                            except discord.Forbidden:
                                pass
                        break
        else:
            await interaction.response.edit_message(
                content=(
                    f"\N{WHITE HEAVY CHECK MARK} You confirmed receipt of **{item_name}**. "
                    f"Waiting for the giver to confirm hand over."
                ),
                view=_dismiss_view(),
            )


class ManageSelectView(discord.ui.View):
    def __init__(self, cog: "Giveaways", guild: discord.Guild, items: list, page: int = 0):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild = guild
        self.items = items
        self.page = page
        self.select = discord.ui.Select(
            placeholder="Remove item...",
            options=[],
        )
        self.select.callback = self.on_select
        self.add_item(self.select)
        self._sync_page()

    async def build_embed(self) -> discord.Embed:
        start, end = _page_bounds(self.page, len(self.items))
        lines = []
        for item in self.items[start:end]:
            line = f"**#{item['id']}** {item['item_name']} ({_age(item['created_at'])})"

            pending = await db.get_pending_claims_for_item(self.cog.bot.db, item["id"])
            if pending:
                for i, claim in enumerate(pending, 1):
                    claimer = self.guild.get_member(claim["claimer_id"])
                    name = claimer.display_name if claimer else f"User {claim['claimer_id']}"
                    line += f"\n> #{i} in queue: {name}"

            accepted = await db.get_accepted_claims_for_item(self.cog.bot.db, item["id"])
            for claim in accepted:
                claimer = self.guild.get_member(claim["claimer_id"])
                name = claimer.display_name if claimer else f"User {claim['claimer_id']}"
                line += f"\n> Promised to {name}"

            lines.append(line)

        embed = discord.Embed(
            title="Your giveaway listings",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        if _page_count(len(self.items)) > 1:
            embed.set_footer(text=f"Page {self.page + 1}/{_page_count(len(self.items))}")
        return embed

    def _sync_page(self) -> None:
        start, end = _page_bounds(self.page, len(self.items))
        self.select.options = [
            discord.SelectOption(
                label=f"#{item['id']} {item['item_name']}",
                value=str(item["id"]),
            )
            for item in self.items[start:end]
        ]
        self.select.placeholder = f"Remove item... ({start + 1}-{end} of {len(self.items)})"
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= _page_count(len(self.items)) - 1

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_page()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_page()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    async def on_select(self, interaction: discord.Interaction):
        item_id = int(self.select.values[0])
        item = await db.get_item(self.cog.bot.db, item_id)

        if not item or item["status"] != "available":
            await interaction.response.edit_message(
                content="That item has already been removed.", view=None,
            )
            return

        if item["user_id"] != interaction.user.id:
            await interaction.response.edit_message(
                content="That's not your item.", view=None,
            )
            return

        # Show confirm/cancel buttons before marking gone
        view = ConfirmMarkGoneView(self.cog, item_id, item["item_name"])
        await interaction.response.edit_message(
            content=f"Remove **{item['item_name']}** from the board?",
            embed=None,
            view=view,
        )


class ConfirmMarkGoneView(discord.ui.View):
    def __init__(self, cog: "Giveaways", item_id: int, item_name: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.item_id = item_id
        self.item_name = item_name

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger, emoji="\N{WASTEBASKET}")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await db.get_item(self.cog.bot.db, self.item_id)
        if not item or item["status"] != "available":
            await interaction.response.edit_message(
                content="That item has already been removed.", view=None,
            )
            return

        # Snapshot pending claims before marking gone so we can notify them
        pending = await db.get_pending_claims_for_item(self.cog.bot.db, self.item_id)

        await db.mark_item_gone(self.cog.bot.db, self.item_id)
        await interaction.response.edit_message(
            content=f"**{self.item_name}** removed from the board.",
            view=None,
        )

        # Notify auto-declined claimers and refresh the board
        for guild in self.cog.bot.guilds:
            if guild.id == item["guild_id"]:
                for claim in pending:
                    member = await _resolve_member(guild, claim["claimer_id"])
                    if member:
                        try:
                            await member.send(
                                f"\N{CROSS MARK} Your claim on **{self.item_name}** "
                                f"was declined -- the item is no longer available."
                            )
                        except discord.Forbidden:
                            pass
                await refresh_board(self.cog.bot, guild)
                break

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dismiss_ephemeral(interaction)


# -- Cog ----------------------------------------------------------------------


class Giveaways(commands.Cog):
    """Community giveaway board with live tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()

    # -- cleanup task ---------------------------------------------------------

    @tasks.loop(minutes=5)
    async def cleanup(self):
        expired = await db.expire_old_items(self.bot.db, config.GIVEAWAY_EXPIRY_HOURS)
        purged = await db.purge_gone_items(self.bot.db, hours=168)
        if expired or purged:
            log.info("Giveaway cleanup: expired=%d, purged=%d", expired, purged)
            for guild in self.bot.guilds:
                await refresh_board(self.bot, guild)

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    from cogs import lance

    # Register dynamic items for persistence across restarts
    bot.add_dynamic_items(
        GiveButton, ClaimButton, MyItemsButton, MyClaimsButton,
        AcceptClaimButton, DeclineClaimButton,
        ConfirmGivenButton, ConfirmReceivedButton,
        DismissDMButton,
    )

    cog = Giveaways(bot)
    await bot.add_cog(cog)

    @lance.command(name="give", description="List item(s) on the giveaway board")
    @app_commands.describe(items="Item name(s), comma-separated for multiple")
    async def give(interaction: discord.Interaction, items: str):
        names = [n.strip() for n in items.split(",") if n.strip()]
        if not names:
            await interaction.response.send_message("Item name can't be empty.", ephemeral=True)
            return

        for name in names:
            await db.create_item(bot.db, interaction.guild_id, interaction.user.id, name)

        if len(names) == 1:
            summary = f"**{names[0]}**"
        else:
            summary = ", ".join(f"**{n}**" for n in names)
        await interaction.response.send_message(
            f"Listed {summary} on the giveaway board!",
            ephemeral=True,
        )
        await refresh_board(bot, interaction.guild)

    @lance.command(name="giveaway-setup", description="Post the giveaway board in this channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def giveaway_setup(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        existing = await db.get_giveaway_board(bot.db, interaction.guild_id)
        embed = await build_board_embed(bot, interaction.guild)
        view = BoardView(interaction.guild_id)
        if existing is not None:
            old_channel = interaction.guild.get_channel(existing["channel_id"])
            if old_channel is not None:
                try:
                    old_msg = await old_channel.fetch_message(existing["message_id"])
                except discord.NotFound:
                    old_msg = None
                except discord.HTTPException:
                    old_msg = None
                else:
                    if old_channel.id == interaction.channel_id:
                        await old_msg.edit(embed=embed, view=view)
                        await interaction.followup.send(
                            "Giveaway board refreshed.",
                            ephemeral=True,
                        )
                        return
                    try:
                        await old_msg.delete()
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        msg = await interaction.channel.send(embed=embed, view=view)
        await db.set_giveaway_board(
            bot.db, interaction.guild_id, interaction.channel_id, msg.id,
        )
        await interaction.followup.send(
            "Giveaway board posted! This message will auto-update.",
            ephemeral=True,
        )
