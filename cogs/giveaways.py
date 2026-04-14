"""Community giveaway board.

A single live-updating embed per guild tracks available items. Users can
list items, claim them, and manage their listings via buttons or slash
commands.

Lifecycle of an item:
  1. User posts via Give button or /lance give
  2. Item appears on the board as "available"
  3. Another user clicks Claim -> lister gets a DM with Accept/Decline
  4. Accept -> quantity decremented (gone if 0), claimer notified
  5. Decline -> claimer notified, item stays available
  6. Lister can Mark Gone at any time
  7. Items auto-expire after GIVEAWAY_EXPIRY_HOURS (default 72)
  8. Gone items purged from DB after 7 days
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

            # Show pending claim count
            pending = await db.get_pending_claims_for_item(bot.db, item["id"])
            if pending:
                line += f"\n> {len(pending)} pending claim{'s' if len(pending) != 1 else ''}"

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
                for claim in pending:
                    claimer = interaction.guild.get_member(claim["claimer_id"])
                    name = claimer.display_name if claimer else f"User {claim['claimer_id']}"
                    line += f"\n> Pending: {name}"

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

        claim = await db.create_claim(self.cog.bot.db, item_id, interaction.user.id)

        await interaction.response.edit_message(
            content=f"Claim submitted for **{item['item_name']}**! The owner has been notified.",
            view=None,
        )

        # DM the lister
        guild = interaction.guild
        lister = guild.get_member(item["user_id"]) if guild else None
        if lister is None and guild is not None:
            try:
                lister = await guild.fetch_member(item["user_id"])
            except (discord.Forbidden, discord.HTTPException):
                lister = None

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
            except discord.Forbidden:
                log.warning("Cannot DM user %s for claim %s", lister.id, claim["id"])

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

        await db.accept_claim(bot.db, self.claim_id)
        new_qty = await db.decrement_item(bot.db, item["id"])

        status = "gone" if new_qty <= 0 else f"{new_qty} remaining"
        await interaction.response.edit_message(
            content=(
                f"\N{WHITE HEAVY CHECK MARK} Accepted! **{item['item_name']}** -- {status}."
            ),
            view=None,
        )

        # Notify claimer and refresh board
        for guild in bot.guilds:
            if guild.id == item["guild_id"]:
                claimer = guild.get_member(claim["claimer_id"])
                if claimer is None:
                    try:
                        claimer = await guild.fetch_member(claim["claimer_id"])
                    except (discord.Forbidden, discord.HTTPException):
                        claimer = None
                if claimer:
                    try:
                        await claimer.send(
                            f"\N{WHITE HEAVY CHECK MARK} Your claim on **{item['item_name']}** "
                            f"was accepted by **{interaction.user.display_name}**! "
                            f"Reach out to them to arrange the handoff."
                        )
                    except discord.Forbidden:
                        pass

                # Notify claimers who were auto-declined because the item ran out
                if new_qty <= 0:
                    auto_declined = await db.get_declined_claims_for_item(bot.db, item["id"])
                    for dc in auto_declined:
                        if dc["id"] == self.claim_id:
                            continue  # This is the accepted claim
                        dc_member = guild.get_member(dc["claimer_id"])
                        if dc_member is None:
                            try:
                                dc_member = await guild.fetch_member(dc["claimer_id"])
                            except (discord.Forbidden, discord.HTTPException):
                                dc_member = None
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

        # Notify claimer and refresh board
        if item:
            for guild in bot.guilds:
                if guild.id == item["guild_id"]:
                    claimer = guild.get_member(claim["claimer_id"])
                    if claimer is None:
                        try:
                            claimer = await guild.fetch_member(claim["claimer_id"])
                        except (discord.Forbidden, discord.HTTPException):
                            claimer = None
                    if claimer:
                        try:
                            await claimer.send(
                                f"\N{CROSS MARK} Your claim on **{item_name}** was declined."
                            )
                        except discord.Forbidden:
                            pass
                    await refresh_board(bot, guild)
                    break


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

        await db.mark_item_gone(self.cog.bot.db, item_id)
        await interaction.response.edit_message(
            content=f"**{item['item_name']}** marked as gone.",
            view=None,
        )

        # Refresh the board in the appropriate guild
        for guild in self.cog.bot.guilds:
            if guild.id == item["guild_id"]:
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
