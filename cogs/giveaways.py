"""Community giveaway board.

A single live-updating embed per guild tracks available items. Users can
list items, claim them, and manage their listings via buttons or slash
commands.

Lifecycle of an item:
  1. User posts via Give button or /lance give
  2. Item appears on the board as "available"
  3. Another user clicks Claim -> joins a queue (ordered by time)
  4. First in queue is presented to the lister via DM (Accept/Decline)
  5. Accept -> quantity decremented, next in queue presented (or all
     notified if item is gone)
  6. Decline -> claimer notified, next in queue presented
  7. Lister can Mark Gone at any time (remaining queue notified)
  8. Items auto-expire after GIVEAWAY_EXPIRY_HOURS (default 120)
  9. Gone items purged from DB after 7 days
"""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db

log = logging.getLogger("lance.giveaways")

MAX_SELECT_OPTIONS = 25


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
        title="\N{PACKAGE} Community Giveaways",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )

    if not items:
        embed.description = (
            "No items available right now.\n\n"
            "Click **Give Item** below or use `/lance give` to list something!"
        )
    else:
        lines = []
        for item in items:
            qty = f" x{item['quantity']}" if item["quantity"] > 1 else ""
            member = guild.get_member(item["user_id"])
            who = member.mention if member else f"<@{item['user_id']}>"
            age = _age(item["created_at"])

            line = f"**#{item['id']}** {item['item_name']}{qty} -- {who} ({age})"

            # Show accepted claims
            accepted = await db.get_accepted_claims_for_item(bot.db, item["id"])
            for claim in accepted:
                claimer = guild.get_member(claim["claimer_id"])
                claimer_name = claimer.mention if claimer else f"<@{claim['claimer_id']}>"
                line += f"\n> Promised to {claimer_name}"

            # Show queue size
            pending = await db.get_pending_claims_for_item(bot.db, item["id"])
            if pending:
                line += f"\n> {len(pending)} in queue"

            lines.append(line)

        embed.description = "\n\n".join(lines)

    embed.set_footer(
        text=f"{config.BOT_NAME} -- items expire after {config.GIVEAWAY_EXPIRY_HOURS}h"
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
    footer = config.BOT_NAME
    if queue_remaining > 0:
        footer += f" -- {queue_remaining} more in queue"
    embed.set_footer(text=footer)

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
) -> None:
    """Award the correct milestone role and remove any lower ones."""
    if not config.GIVEAWAY_MILESTONES:
        return

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
        return

    member = await _resolve_member(guild, user_id)
    if member is None:
        return

    # Build a lookup of guild roles by name
    guild_roles = {r.name: r for r in guild.roles}

    # Award the earned role
    earned_role = guild_roles.get(earned_role_name)
    if earned_role and earned_role not in member.roles:
        try:
            await member.add_roles(earned_role, reason=f"Giveaway milestone: {new_total} items given")
            log.info("Awarded %s to %s (%d items given)", earned_role_name, member, new_total)
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


async def _complete_handoff(
    bot: commands.Bot, claim, item, guild: discord.Guild,
) -> None:
    """Called when both giver and taker confirm. Increments stats and checks milestones."""
    new_total = await db.increment_items_given(bot.db, guild.id, item["user_id"])
    await _check_milestone_roles(bot, guild, item["user_id"], new_total)
    log.info(
        "Handoff complete: item %d, claim %d, giver %d now at %d total",
        item["id"], claim["id"], item["user_id"], new_total,
    )


# -- Modals -------------------------------------------------------------------


class GiveItemModal(discord.ui.Modal, title="Give Away Items"):
    item_names: discord.ui.TextInput = discord.ui.TextInput(
        label="Item name(s)",
        placeholder="e.g. Titanium Plate, Salvaged Motor, Polymer Sheet",
        required=True,
        max_length=300,
    )
    quantity: discord.ui.TextInput = discord.ui.TextInput(
        label="Quantity (each)",
        placeholder="1",
        required=False,
        max_length=4,
        default="1",
    )

    def __init__(self, cog: "Giveaways"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        names = [n.strip() for n in self.item_names.value.split(",") if n.strip()]
        if not names:
            await interaction.response.send_message("Item name can't be empty.", ephemeral=True)
            return

        try:
            qty = int(self.quantity.value.strip() or "1")
            if qty < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Quantity must be a positive number.", ephemeral=True)
            return

        for name in names:
            await db.create_item(
                self.cog.bot.db, interaction.guild_id, interaction.user.id, name, qty,
            )

        qty_str = f" x{qty}" if qty > 1 else ""
        if len(names) == 1:
            summary = f"**{names[0]}{qty_str}**"
        else:
            summary = ", ".join(f"**{n}{qty_str}**" for n in names)
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


class GiveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"giveaway:give:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="Give Item",
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
                label="Claim",
                style=discord.ButtonStyle.primary,
                custom_id=f"giveaway:claim:{guild_id}",
                emoji="\N{RAISED HAND}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        items = await db.get_available_items(cog.bot.db, self.guild_id)
        # Exclude the user's own items
        items = [i for i in items if i["user_id"] != interaction.user.id]

        if not items:
            await interaction.response.send_message(
                "Nothing available to claim right now.", ephemeral=True,
            )
            return

        options = []
        for item in items[:MAX_SELECT_OPTIONS]:
            qty = f" x{item['quantity']}" if item["quantity"] > 1 else ""
            member = interaction.guild.get_member(item["user_id"])
            who = member.display_name if member else "Unknown"
            options.append(
                discord.SelectOption(
                    label=f"{item['item_name']}{qty}",
                    description=f"From {who} ({_age(item['created_at'])})",
                    value=str(item["id"]),
                )
            )

        view = ClaimSelectView(cog, options)
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
                label="My Items",
                style=discord.ButtonStyle.secondary,
                custom_id=f"giveaway:mine:{guild_id}",
                emoji="\N{MEMO}",
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

        embed = discord.Embed(
            title="Your giveaway listings",
            color=discord.Color.blurple(),
        )
        lines = []
        for item in items:
            qty = f" x{item['quantity']}" if item["quantity"] > 1 else ""
            line = f"**#{item['id']}** {item['item_name']}{qty} ({_age(item['created_at'])})"

            pending = await db.get_pending_claims_for_item(cog.bot.db, item["id"])
            if pending:
                for i, claim in enumerate(pending, 1):
                    claimer = interaction.guild.get_member(claim["claimer_id"])
                    name = claimer.display_name if claimer else f"User {claim['claimer_id']}"
                    label = "Next up" if i == 1 else f"#{i} in queue"
                    line += f"\n> {label}: {name}"

            accepted = await db.get_accepted_claims_for_item(cog.bot.db, item["id"])
            for claim in accepted:
                claimer = interaction.guild.get_member(claim["claimer_id"])
                name = claimer.display_name if claimer else f"User {claim['claimer_id']}"
                line += f"\n> Promised to {name}"

            lines.append(line)

        embed.description = "\n\n".join(lines)

        # Build management options
        options = []
        for item in items[:MAX_SELECT_OPTIONS]:
            qty = f" x{item['quantity']}" if item["quantity"] > 1 else ""
            options.append(
                discord.SelectOption(
                    label=f"#{item['id']} {item['item_name']}{qty}",
                    value=str(item["id"]),
                )
            )

        view = ManageSelectView(cog, options)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# -- Ephemeral views (not persistent, timeout OK) ----------------------------


class ClaimSelectView(discord.ui.View):
    def __init__(self, cog: "Giveaways", options: list[discord.SelectOption]):
        super().__init__(timeout=120)
        self.cog = cog
        self.select = discord.ui.Select(
            placeholder="Choose an item...",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

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

        # Check existing queue before creating the claim
        existing_pending = await db.get_pending_claims_for_item(self.cog.bot.db, item_id)
        claim = await db.create_claim(self.cog.bot.db, item_id, interaction.user.id)

        guild = interaction.guild
        if existing_pending:
            # Someone is already first in queue -- just join behind them
            position = len(existing_pending) + 1
            await interaction.response.edit_message(
                content=(
                    f"You're **#{position}** in the queue for **{item['item_name']}**. "
                    f"You'll be notified when it's your turn."
                ),
                view=None,
            )
        else:
            # First in queue -- DM the lister
            await interaction.response.edit_message(
                content=f"Claim submitted for **{item['item_name']}**! The owner has been notified.",
                view=None,
            )

            lister = await _resolve_member(guild, item["user_id"]) if guild else None
            dm_sent = False
            if lister is not None:
                embed = discord.Embed(
                    title="Someone wants your item!",
                    description=(
                        f"**{interaction.user.display_name}** wants your "
                        f"**{item['item_name']}**"
                    ),
                    color=discord.Color.gold(),
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.set_footer(text=config.BOT_NAME)

                view = ClaimResponseView(claim["id"])
                try:
                    await lister.send(embed=embed, view=view)
                    dm_sent = True
                except discord.Forbidden:
                    log.warning("Cannot DM user %s for claim %s", lister.id, claim["id"])

            if not dm_sent:
                try:
                    await interaction.user.send(
                        f"Heads up -- I couldn't DM the owner of **{item['item_name']}** "
                        f"to notify them of your claim. They may have DMs disabled. "
                        f"You might need to reach out to them directly."
                    )
                except discord.Forbidden:
                    pass

        await refresh_board(self.cog.bot, guild)


class ClaimResponseView(discord.ui.View):
    """Sent in DM to the item lister. Accept or decline a claim. Persistent."""

    def __init__(self, claim_id: int):
        super().__init__(timeout=None)
        self.add_item(AcceptClaimButton(claim_id))
        self.add_item(DeclineClaimButton(claim_id))


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
                content="This claim has already been resolved.", view=None,
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        if not item:
            await interaction.response.edit_message(content="Item no longer exists.", view=None)
            return

        # Snapshot the rest of the queue before state change
        other_pending = [
            c for c in await db.get_pending_claims_for_item(bot.db, item["id"])
            if c["id"] != self.claim_id
        ]

        await db.accept_claim(bot.db, self.claim_id)
        new_qty = await db.decrement_item(bot.db, item["id"])

        status = "gone" if new_qty <= 0 else f"{new_qty} remaining"
        # Show Confirm Handoff button to the lister
        confirm_view = discord.ui.View(timeout=None)
        confirm_view.add_item(ConfirmGivenButton(self.claim_id))
        await interaction.response.edit_message(
            content=(
                f"\N{WHITE HEAVY CHECK MARK} Accepted! **{item['item_name']}** -- {status}.\n"
                f"Once you've handed the item over, click **Confirm Handoff**."
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
                            f"Reach out to them to arrange the handoff, then click "
                            f"**Confirm Received** once you have the item.",
                            view=taker_view,
                        )
                    except discord.Forbidden:
                        pass

                if new_qty <= 0:
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
                else:
                    # Item still available -- present next in queue
                    await _send_next_claim_dm(bot, item, guild)

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
                content="This claim has already been resolved.", view=None,
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        await db.decline_claim(bot.db, self.claim_id)

        item_name = item["item_name"] if item else "Unknown item"
        await interaction.response.edit_message(
            content=f"\N{CROSS MARK} Declined the claim on **{item_name}**.",
            view=None,
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
                label="Confirm Handoff",
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
                content="This claim has already been resolved.", view=None,
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        item_name = item["item_name"] if item else "Unknown item"
        # Re-running confirm_given is safe (sets flag idempotently) and
        # allows the 48h giver override to trigger on a second click
        completed = await db.confirm_given(bot.db, self.claim_id)

        if completed:
            await interaction.response.edit_message(
                content=(
                    f"\N{HANDSHAKE} Handoff complete for **{item_name}**! "
                    f"Thanks for your generosity."
                ),
                view=None,
            )
            # Notify taker that handoff is complete
            if item:
                for guild in bot.guilds:
                    if guild.id == item["guild_id"]:
                        taker = await _resolve_member(guild, claim["claimer_id"])
                        if taker:
                            try:
                                await taker.send(
                                    f"\N{HANDSHAKE} Handoff complete for **{item_name}**! "
                                    f"Both sides confirmed."
                                )
                            except discord.Forbidden:
                                pass
                        await _complete_handoff(bot, claim, item, guild)
                        break
        else:
            confirm_view = discord.ui.View(timeout=None)
            confirm_view.add_item(ConfirmGivenButton(self.claim_id))
            await interaction.response.edit_message(
                content=(
                    f"\N{WHITE HEAVY CHECK MARK} You confirmed the handoff for **{item_name}**. "
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
                content="This claim has already been resolved.", view=None,
            )
            return

        if claim["taker_confirmed"]:
            await interaction.response.edit_message(
                content="You've already confirmed this receipt.", view=None,
            )
            return

        item = await db.get_item(bot.db, claim["item_id"])
        item_name = item["item_name"] if item else "Unknown item"
        completed = await db.confirm_received(bot.db, self.claim_id)

        if completed:
            await interaction.response.edit_message(
                content=(
                    f"\N{HANDSHAKE} Handoff complete for **{item_name}**! "
                    f"Both sides confirmed."
                ),
                view=None,
            )
            # Notify giver that handoff is complete
            if item:
                for guild in bot.guilds:
                    if guild.id == item["guild_id"]:
                        giver = await _resolve_member(guild, item["user_id"])
                        if giver:
                            try:
                                await giver.send(
                                    f"\N{HANDSHAKE} Handoff complete for **{item_name}**! "
                                    f"Both sides confirmed. Thanks for your generosity."
                                )
                            except discord.Forbidden:
                                pass
                        await _complete_handoff(bot, claim, item, guild)
                        break
        else:
            await interaction.response.edit_message(
                content=(
                    f"\N{WHITE HEAVY CHECK MARK} You confirmed receipt of **{item_name}**. "
                    f"Waiting for the giver to confirm handoff."
                ),
                view=None,
            )


class ManageSelectView(discord.ui.View):
    def __init__(self, cog: "Giveaways", options: list[discord.SelectOption]):
        super().__init__(timeout=120)
        self.cog = cog
        self.select = discord.ui.Select(
            placeholder="Select an item to mark as gone...",
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        item_id = int(self.select.values[0])
        item = await db.get_item(self.cog.bot.db, item_id)

        if not item or item["status"] != "available":
            await interaction.response.edit_message(
                content="That item is already gone.", view=None,
            )
            return

        if item["user_id"] != interaction.user.id:
            await interaction.response.edit_message(
                content="That's not your item.", view=None,
            )
            return

        # Snapshot pending claims before marking gone so we can notify them
        pending = await db.get_pending_claims_for_item(self.cog.bot.db, item_id)

        await db.mark_item_gone(self.cog.bot.db, item_id)
        await interaction.response.edit_message(
            content=f"**{item['item_name']}** marked as gone.",
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
                                f"\N{CROSS MARK} Your claim on **{item['item_name']}** "
                                f"was declined -- the item is no longer available."
                            )
                        except discord.Forbidden:
                            pass
                await refresh_board(self.cog.bot, guild)
                break


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
        GiveButton, ClaimButton, MyItemsButton,
        AcceptClaimButton, DeclineClaimButton,
        ConfirmGivenButton, ConfirmReceivedButton,
    )

    cog = Giveaways(bot)
    await bot.add_cog(cog)

    @lance.command(name="give", description="List item(s) on the giveaway board")
    @app_commands.describe(
        items="Item name(s), comma-separated for multiple",
        quantity="How many of each (default 1)",
    )
    async def give(interaction: discord.Interaction, items: str, quantity: int = 1):
        if quantity < 1:
            await interaction.response.send_message(
                "Quantity must be at least 1.", ephemeral=True,
            )
            return

        names = [n.strip() for n in items.split(",") if n.strip()]
        if not names:
            await interaction.response.send_message("Item name can't be empty.", ephemeral=True)
            return

        for name in names:
            await db.create_item(bot.db, interaction.guild_id, interaction.user.id, name, quantity)

        qty_str = f" x{quantity}" if quantity > 1 else ""
        if len(names) == 1:
            summary = f"**{names[0]}{qty_str}**"
        else:
            summary = ", ".join(f"**{n}{qty_str}**" for n in names)
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
