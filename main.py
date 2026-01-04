# main.py
from dotenv import load_dotenv
import os

# Load .env file from protected folder
load_dotenv('/root/config/.env')

TOKEN = os.getenv("TOKEN")
print("Loaded TOKEN:", TOKEN)  # temporary debug line

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Select, Button, Modal, TextInput
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import aiohttp
import sys
import io

# ---------------------------
# Config (from user)
# ---------------------------
ADMIN_ROLE_IDS = [1241428059744112791]
SUPPORT_ROLE_ID = 1241428059744112791
LOG_CHANNEL_ID = 1430287977547829300

CATEGORY_IDS = {
    # 'getting_info' is a text channel used for notifications (leave as single ID).
    "getting_info": 1430036848029204511,
    "robux_gamepass": [1430036848029204511, 1432747658501427212, 1432747708984328202],
    "robux_groupfunds": [1430036848029204511, 1432747658501427212, 1432747708984328202],
    "robux_ingame": [1430036848029204511, 1432747658501427212, 1432747708984328202],
    "other": 1430037129924317254
}

TICKET_PANEL_CHANNEL_ID = 1241428048260366438
BOOT_TIME = None

PRICES = {"gamepass": 4.75, "groupfunds": 6.25, "ingame": 4.8}

# persistable prices file
PRICES_PATH = "prices.json"

# load persisted prices (if present) to override defaults
try:
    _loaded = read_json(PRICES_PATH)
    if isinstance(_loaded, dict) and _loaded:
        PRICES.update({k.lower(): float(v) for k, v in _loaded.items()})
except Exception:
    pass

def write_prices():
    try:
        write_json(PRICES_PATH, PRICES)
    except Exception as e:
        print("Failed to write prices:", e)

PAYMENT_FEES = {
    "binance": 0,
    "crypto": 0,
    "wise": 0,
    "bank": 0,
    "paypal": 10,
    "tng": 7,
    "zelle": 7,
    "chime": 7,
    "skrill": 7,
    "giftcard": 7
}

# persistable fees file
PAYMENT_FEES_PATH = "payment_fees.json"

# load persisted fees (if present) to override defaults
try:
    _loaded = read_json(PAYMENT_FEES_PATH)
    if isinstance(_loaded, dict) and _loaded:
        PAYMENT_FEES.update({k.lower(): float(v) for k, v in _loaded.items()})
except Exception:
    pass

def write_payment_fees():
    try:
        write_json(PAYMENT_FEES_PATH, PAYMENT_FEES)
    except Exception as e:
        print("Failed to write payment fees:", e)


PAYMENT_JSON = "payment_info.json"
STICKY_JSON = "stickymessages.json"
STICKY_IDS_JSON = "sticky_message_ids.json"
TICKET_JSON = "tickets.json"
PENDING_CLOSES_JSON = "pending_closes.json"


# Helper: read/write accounting JSON
ACCOUNTING_JSON = "accounting.json"

def read_accounting():
    if not os.path.exists(ACCOUNTING_JSON):
        return {"users": {}, "totals": {}, "methods": {}}
    with open(ACCOUNTING_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def write_accounting(data):
    with open(ACCOUNTING_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# ---------------------------
# Helper functions for JSON
# ---------------------------
def read_json(path: str) -> Any:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def write_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

# Helper for pending closes
def read_pending_closes():
    data = read_json(PENDING_CLOSES_JSON)
    result = {}
    for cid, ts_str in data.items():
        try:
            result[int(cid)] = datetime.fromisoformat(ts_str)
        except:
            pass
    return result

def write_pending_closes(data: Dict[int, datetime]):
    serializable = {str(cid): dt.isoformat() for cid, dt in data.items()}
    write_json(PENDING_CLOSES_JSON, serializable)

# default payment_info structure (if file missing)
if not os.path.exists(PAYMENT_JSON):
    write_json(PAYMENT_JSON, {
        # example: "paypal": "PayPal instructions..."
    })

if not os.path.exists(TICKET_JSON):
    write_json(TICKET_JSON, {})

if not os.path.exists(STICKY_JSON):
    write_json(STICKY_JSON, {})

# Load sticky messages
sticky_messages: Dict[str, str] = read_json(STICKY_JSON) or {}
sticky_tasks: Dict[str, asyncio.Task] = {}
sticky_message_ids: Dict[str, int] = read_json(STICKY_IDS_JSON) or {}

# ---------------------------
# Intents & Bot Setup
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
tree = bot.tree

# We'll use a simple in-memory cache for tickets synchronized to tickets.json
tickets_data: Dict[str, List[Dict[str, Any]]] = read_json(TICKET_JSON) or {}

# Load pending closes
pending_auto_closes: Dict[int, datetime] = read_pending_closes()

# ---------------------------
# Utility: Permissions + Checks
# ---------------------------
def is_admin_member(member: discord.Member) -> bool:
    if member is None:
        return False
    return any(role.id in ADMIN_ROLE_IDS for role in member.roles)

def payment_fee_for(method: str) -> float:
    return PAYMENT_FEES.get(method.lower(), 0)

def price_for(subtype: str) -> Optional[float]:
    return PRICES.get(subtype)

# ---------------------------
# Embeds
# ---------------------------
def ticket_info_embed(user: discord.User, delivery_type: str, subtype: Optional[str],
                      payment_method: str, amount: float, total_cost: float,
                      notes: Optional[str]) -> discord.Embed:
    embed = discord.Embed(title="Ticket Created", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="User", value=f"{user.mention} ({user})", inline=False)
    embed.add_field(name="Delivery Type", value=delivery_type, inline=True)
    if subtype:
        embed.add_field(name="Subtype", value=subtype, inline=True)
    embed.add_field(name="Payment Method", value=payment_method, inline=True)
    embed.add_field(name="Amount (Robux)", value=f"{amount:,}", inline=True)
    embed.add_field(name="Total Cost", value=f"${total_cost:,.2f}", inline=True)
    if notes:
        embed.add_field(name="Payment Instructions", value=notes, inline=False)
    embed.set_footer(text="Support will be with you shortly.")
    return embed


# Helper: choose a category that isn't full (Discord limit ~50 channels per category)
def select_ticket_category(guild: discord.Guild, key: str) -> Optional[discord.CategoryChannel]:
    """Given a CATEGORY_IDS key, return the first CategoryChannel in the guild which has
    fewer than 50 text channels. CATEGORY_IDS value may be a single int or a list of ints.
    Returns None if no suitable category is found.
    """
    raw = CATEGORY_IDS.get(key)
    if raw is None:
        return None

    ids = raw if isinstance(raw, (list, tuple)) else [raw]
    for cid in ids:
        try:
            cat = guild.get_channel(cid)
        except Exception:
            cat = None
        if not cat:
            continue
        # CategoryChannel has .text_channels; if not present, fall back to counting by category_id
        try:
            count = len(cat.text_channels)
        except Exception:
            count = len([c for c in guild.channels if getattr(c, "category_id", None) == cid and isinstance(c, discord.TextChannel)])
        # If fewer than 50 text channels, use this category
        if count < 50:
            return cat

    # None available (all full or invalid) -> return None
    return None

# ---------------------------
# Views / UI
# ---------------------------

# Main panel view - first select delivery type
class TicketPanelView(View):
    def __init__(self, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        # Add selects/buttons dynamically in __init__
        self.add_item(DeliverySelect())

class DeliverySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Robux", description="Buy Robux (Gamepass / Group Funds / In-Game)", value="robux"),
            discord.SelectOption(label="Other", description="Other support request", value="other")
        ]
        super().__init__(placeholder="Select ticket type...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "robux":
            # Show robux subtype select view
            await interaction.response.send_message(embed=discord.Embed(title="Select Robux Delivery Type", description="Choose the delivery subtype."), view=RobuxSubtypeView(), ephemeral=True)
        else:
            # For "Other" open a modal to gather details (amount not needed)
            await interaction.response.send_modal(OtherTicketModal())

class RobuxSubtypeView(View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(RobuxSubtypeSelect())
        self.add_item(PaymentMethodSelect())  # allow selecting method before modal
        self.add_item(StartRobuxModalButton())

class RobuxSubtypeSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Gamepass", value="gamepass", description="Gamepass delivery"),
            discord.SelectOption(label="Group Funds", value="groupfunds", description="Group funds delivery"),
            discord.SelectOption(label="In-Game Gifting", value="ingame", description="In-game gifting")
        ]
        super().__init__(placeholder="Select delivery subtype...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # store selection in view state
        view: RobuxSubtypeView = self.view  # type: ignore
        view.delivery_subtype = self.values[0]
        await interaction.response.send_message(f"Selected subtype: **{self.values[0]}**", ephemeral=True)

class PaymentMethodSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=method.title(), value=method) for method in PAYMENT_FEES.keys()]
        super().__init__(placeholder="Select payment method...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: RobuxSubtypeView = self.view  # type: ignore
        view.payment_method = self.values[0]
        await interaction.response.send_message(f"Selected payment method: **{self.values[0]}**", ephemeral=True)

class StartRobuxModalButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.success, label="Proceed", custom_id="start_robux_modal")

    async def callback(self, interaction: discord.Interaction):
        view: RobuxSubtypeView = self.view  # type: ignore
        delivery_subtype = getattr(view, "delivery_subtype", None)
        payment_method = getattr(view, "payment_method", None)
        if not delivery_subtype or not payment_method:
            await interaction.response.send_message("Please choose a delivery subtype and a payment method before proceeding.", ephemeral=True)
            return
        # show modal for amount input
        modal = RobuxAmountModal(delivery_subtype, payment_method)
        await interaction.response.send_modal(modal)

class RobuxAmountModal(Modal):
    def __init__(self, subtype: str, payment_method: str):
        super().__init__(title="Enter Robux Amount")
        self.subtype = subtype
        self.payment_method = payment_method
        self.amount = TextInput(label="Amount of Robux (only numbers)", placeholder="e.g. 1000", style=discord.TextStyle.short, required=True, max_length=20)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # validate amount
            raw = self.amount.value.strip().replace(",", "")
            if not raw.isdigit():
                await interaction.response.send_message("Amount must be an integer number of Robux.", ephemeral=True)
                return
            amount = int(raw)
            # call ticket creation
            await create_ticket_for_user(interaction, delivery_type="Robux", subtype=self.subtype, payment_method=self.payment_method, amount=amount)
        except Exception as e:
            print(f"Error in RobuxAmountModal.on_submit: {e}")
            await interaction.response.send_message("An error occurred while creating the ticket. Please try again or contact support.", ephemeral=True)

class OtherTicketModal(Modal):
    def __init__(self):
        super().__init__(title="Create Other Ticket")
        self.details = TextInput(label="Describe your request", style=discord.TextStyle.paragraph, placeholder="Explain the issue or request...", required=True, max_length=2000)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            content = self.details.value.strip()
            await create_ticket_for_user(interaction, delivery_type="Other", subtype=None, payment_method="N/A", amount=0, extra_notes=content)
        except Exception as e:
            print(f"Error in OtherTicketModal.on_submit: {e}")
            await interaction.response.send_message("An error occurred while creating the ticket. Please try again or contact support.", ephemeral=True)

# ---------------------------
# Ticket creation & flow
# ---------------------------
async def create_ticket_for_user(interaction: discord.Interaction, delivery_type: str, subtype: Optional[str], payment_method: str, amount: int = 0, extra_notes: Optional[str] = None):
    """Create a ticket channel for the interaction user, enforce one-ticket-per-user, post embed & instructions."""
    user = interaction.user
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command must be used in a server (guild).", ephemeral=True)
        return

    # Check for existing tickets (allow up to 3)
    global tickets_data
    user_key = str(user.id)
    user_tickets = tickets_data.get(user_key, [])
    active_tickets = [t for t in user_tickets if guild.get_channel(int(t.get("channel_id", 0))) is not None]
    if len(active_tickets) >= 3:
        await interaction.response.send_message("You can have up to 3 active tickets.", ephemeral=True)
        return
    # Clean up stale tickets
    user_tickets = active_tickets
    tickets_data[user_key] = user_tickets
    write_json(TICKET_JSON, tickets_data)

    # Determine category key and channel name
    category_key = "other"
    subtype_key = None
    if delivery_type.lower() == "robux":
        if subtype == "gamepass":
            category_key = "robux_gamepass"
            subtype_key = "gamepass"
        elif subtype == "groupfunds":
            category_key = "robux_groupfunds"
            subtype_key = "groupfunds"
        elif subtype == "ingame":
            category_key = "robux_ingame"
            subtype_key = "ingame"
        else:
            category_key = "other"
    else:
        category_key = "other"

    # Unique channel name: ticket-username-XXXX
    safe_name = user.name.lower().replace(" ", "-")[:20]
    unique_suffix = str(user.id)[-4:]
    channel_name = f"ticket-{safe_name}-{unique_suffix}"

    # Build overwrites: only user and support role and bot can view
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
    }
    support_role = guild.get_role(SUPPORT_ROLE_ID)
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    # bot perms
    overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)

    # find category object (select the first non-full category for the key)
    category = select_ticket_category(guild, category_key)

    # if no category available (all configured categories full or invalid), we'll create the channel at top-level
    # and optionally ping staff in the created channel. This avoids failing to create a ticket when categories hit the 50-channel limit.

    # create channel
    try:
        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, category=category, topic=f"Ticket for {user} ({user.id})")
    except Exception as e:
        await interaction.response.send_message(f"Failed to create ticket channel: {e}", ephemeral=True)
        return

    # Calculate cost if Robux
    total_cost = 0.0
    notes = None
    if delivery_type.lower() == "robux" and subtype_key:
        price_per_thousand = price_for(subtype_key)
        if price_per_thousand is None:
            price_per_thousand = 0.0
        # compute
        thousands = amount / 1000.0
        base = thousands * price_per_thousand
        fee_pct = payment_fee_for(payment_method)
        total_cost = base + (base * (fee_pct / 100.0))
        # fetch payment instructions from JSON
        pay_info = read_json(PAYMENT_JSON)
        notes = pay_info.get(payment_method.lower(), None)
    else:
        # Other tickets: attach extra notes as description
        notes = extra_notes

    # Save ticket meta
    ticket_info = {
        "channel_id": channel.id,
        "user_id": user.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_activity": datetime.now(timezone.utc).isoformat(),
        "delivery_type": delivery_type,
        "subtype": subtype_key,
        "payment_method": payment_method,
        "amount": amount,
        "total_cost": total_cost,
        "warned": False,
        "warn_time": None
    }
    user_tickets = tickets_data.get(user_key, [])
    user_tickets.append(ticket_info)
    tickets_data[user_key] = user_tickets
    write_json(TICKET_JSON, tickets_data)

    # Post ticket info embed in the ticket channel
    embed = ticket_info_embed(user=user, delivery_type=delivery_type, subtype=(subtype_key or "N/A"),
                              payment_method=payment_method, amount=amount, total_cost=total_cost, notes=notes)
    # Provide a Close button for staff
    close_view = TicketChannelView(channel_owner_id=user.id)
    await channel.send(content=f"{support_role.mention if support_role else ''} • Ticket created by {user.mention}", embed=embed, view=close_view)
    # Ephemeral confirmation to user
    await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

    # Optionally post to a 'getting_info' category channel
    getting_info_cat = guild.get_channel(CATEGORY_IDS.get("getting_info"))
    if getting_info_cat:
        try:
            await getting_info_cat.send(f"New ticket {channel.mention} created by {user.mention} • Type: {delivery_type} {('(' + (subtype_key or '') + ')') if subtype_key else ''}")
        except Exception:
            pass

# ---------------------------
# Ticket channel View (Close button)
# ---------------------------
class TicketChannelView(View):
    def __init__(self, channel_owner_id: int):
        super().__init__(timeout=None)
        self.channel_owner_id = channel_owner_id
        self.add_item(CloseTicketButton())

class CloseTicketButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Close Ticket", custom_id="close_ticket_btn")

    async def callback(self, interaction: discord.Interaction):
        # Confirm: show modal or confirm view
        confirm_view = ConfirmCloseView()
        await interaction.response.send_message("Are you sure you want to close this ticket? This will delete the channel.", view=confirm_view, ephemeral=True)

class ConfirmCloseView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(ConfirmCloseButton())
        self.add_item(CancelCloseButton())

class ConfirmCloseButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Yes, close", custom_id="confirm_close_btn")

    async def callback(self, interaction: discord.Interaction):
        # Only allow staff or the ticket owner to close
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Couldn't determine channel.", ephemeral=True)
            return
        # fetch user ticket owner from tickets_data
        owner_id = None
        for uid, user_tickets in tickets_data.items():
            if not isinstance(user_tickets, list):
                continue
            for ticket in user_tickets:
                if not isinstance(ticket, dict):
                    continue
                if int(ticket.get("channel_id", 0)) == channel.id:
                    owner_id = int(uid)
                    break
            else:
                continue
            break
        # permission check: either support role or owner or admin
        member = interaction.user
        allowed = False
        if is_admin_member(member) or SUPPORT_ROLE_ID in [r.id for r in member.roles]:
            allowed = True
        if owner_id and member.id == owner_id:
            allowed = True
        if not allowed:
            await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await close_ticket(channel, closer=interaction.user, reason="Manual close (button)")

class CancelCloseButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.secondary, label="Cancel", custom_id="cancel_close_btn")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)

# ---------------------------
# Close ticket function (creates transcript, logs, deletes channel)
# ---------------------------
async def close_ticket(channel: discord.TextChannel, closer: discord.User, reason: Optional[str] = None, system_action: bool = False):
    guild = channel.guild
    # Build transcript
    transcript_lines: List[str] = []
    async for msg in channel.history(limit=None, oldest_first=True):
        timestr = msg.created_at.astimezone(timezone.utc).isoformat()
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content or ""
        # include attachments
        if msg.attachments:
            att_urls = " ".join(a.url for a in msg.attachments)
            content = content + ("\nAttachments: " + att_urls)
        line = f"[{timestr}] {author}: {content}"
        transcript_lines.append(line)
    transcript_text = "\n".join(transcript_lines) if transcript_lines else "No messages."

    # Create transcript content
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    content = f"Transcript for channel {channel.name} ({channel.id})\n"
    content += f"Closed by: {closer} ({closer.id})\n"
    if reason:
        content += f"Reason: {reason}\n"
    content += "="*40 + "\n\n"
    content += transcript_text

    # Send to log channel as file (in memory)
    try:
        log_chan = guild.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
    except Exception:
        log_chan = None
    if log_chan:
        try:
            bio = io.BytesIO(content.encode('utf-8'))
            file = discord.File(bio, filename=f"transcript_{channel.id}_{ts}.txt")
            await log_chan.send(content=f"Ticket closed: {channel.name} • Closed by: {closer.mention} ({closer.id})", file=file)
        except Exception:
            # fallback to posting a message
            await log_chan.send(f"Ticket closed: {channel.name} • Closed by: {closer} ({closer.id})\nTranscript:\n{content[:1900]}...")  # truncate if too long

    # Remove ticket from tickets_data
    for uid, user_tickets in list(tickets_data.items()):
        if not isinstance(user_tickets, list):
            continue
        for i, ticket in enumerate(user_tickets):
            if not isinstance(ticket, dict):
                continue
            if int(ticket.get("channel_id", 0)) == channel.id:
                user_tickets.pop(i)
                if not user_tickets:
                    tickets_data.pop(uid, None)
                write_json(TICKET_JSON, tickets_data)
                break
        else:
            continue
        break

    # delete the channel
    try:
        await channel.delete(reason=f"Ticket closed by {closer} ({closer.id})")
    except Exception:
        # if deletion fails, try to lock channel
        try:
            await channel.edit(reason="Ticket closed (failed to delete)")
        except Exception:
            pass

# ---------------------------
# Close fail ticket function
# ---------------------------
async def closefail_ticket(channel: discord.TextChannel, closer: Optional[discord.User] = None, reason: Optional[str] = None):
    # DM the user politely
    user = None
    for uid, user_tickets in tickets_data.items():
        if not isinstance(user_tickets, list):
            continue
        for ticket in user_tickets:
            if not isinstance(ticket, dict):
                continue
            if int(ticket.get("channel_id", 0)) == channel.id:
                try:
                    user = await bot.fetch_user(int(uid))
                except Exception:
                    user = None
                break
        else:
            continue
        break

    if user:
        try:
            embed = discord.Embed(
                title="Transaction Failed ❌",
                description=(
                    "Unfortunately, this transaction could not be completed.\n\n"
                    "If you have any questions, please contact support.\n\n"
                    "Thank you for your patience!"
                ),
                color=discord.Color.red()
            )
            await user.send(embed=embed)
        except Exception as e:
            print(f"Could not DM user: {e}")

    # Close the ticket without updating accounting
    await close_ticket(channel, closer or bot.user, reason or "Failed transaction close")

# ---------------------------
# Inactivity Auto-Close System
# ---------------------------
INACTIVITY_CATEGORIES = [
    1430037129924317254,
    1432747658501427212,
    1430036848029204511,
    1422279034867286046
]

# Track pending auto-closes: channel_id -> warning_timestamp
pending_auto_closes: Dict[int, datetime] = {}

class KeepTicketOpenButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.success, label="🛑 Keep Ticket Open", custom_id="keep_ticket_open")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        # Remove from pending closes
        pending_auto_closes.pop(channel.id, None)
        write_pending_closes(pending_auto_closes)

        # Confirm the ticket is staying open
        embed = discord.Embed(
            title="Ticket Kept Open",
            description="This ticket will remain open. Inactivity tracking has been reset.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@tasks.loop(hours=1)  # Check every hour
async def check_ticket_inactivity():
    now = datetime.now(timezone.utc)
    guild = bot.guilds[0] if bot.guilds else None  # Assuming single guild
    if not guild:
        return

    for cat_id in INACTIVITY_CATEGORIES:
        category = guild.get_channel(cat_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            continue

        for channel in category.text_channels:
            # Skip if already has pending close
            if channel.id in pending_auto_closes:
                warning_time = pending_auto_closes[channel.id]
                # Check if 12 hours have passed since warning
                if now - warning_time >= timedelta(hours=12):
                    # Auto-close based on category
                    if cat_id == 1422279034867286046:
                        await close_ticket(channel, bot.user, "Auto-closed due to inactivity (normal)")
                    else:
                        await closefail_ticket(channel, bot.user, "Auto-closed due to inactivity (failed)")
                    pending_auto_closes.pop(channel.id, None)
                    write_pending_closes(pending_auto_closes)
                continue

            # Check last message
            try:
                last_message = None
                async for msg in channel.history(limit=1):
                    last_message = msg
                    break

                if last_message:
                    time_since_last = now - last_message.created_at
                    # Special handling for category 1422279034867286046: auto-close after 1 day without warning
                    if cat_id == 1422279034867286046:
                        if time_since_last >= timedelta(hours=12):
                            await close_ticket(channel, bot.user, "Auto-closed due to completion")
                        continue  # Skip warning for this category
                    
                    if time_since_last >= timedelta(hours=48):
                        # Send warning
                        embed = discord.Embed(
                            title="Ticket Inactivity Warning",
                            description="This ticket has been inactive for 2 days and will close in 12 hours unless stopped.",
                            color=discord.Color.red()
                        )

                        view = View()
                        view.add_item(KeepTicketOpenButton())

                        try:
                            # Find ticket creator to ping
                            mention = "@here"  # fallback
                            for uid, user_tickets in tickets_data.items():
                                if not isinstance(user_tickets, list):
                                    continue
                                for ticket in user_tickets:
                                    if not isinstance(ticket, dict):
                                        continue
                                    if int(ticket.get("channel_id", 0)) == channel.id:
                                        user_id = ticket.get("user_id")
                                        if user_id:
                                            user = guild.get_member(user_id)
                                            if user:
                                                mention = user.mention
                                            else:
                                                mention = f"<@{user_id}>"
                                        break
                                else:
                                    continue
                                break
                            await channel.send(mention, embed=embed, view=view)
                            pending_auto_closes[channel.id] = now
                            write_pending_closes(pending_auto_closes)
                        except Exception as e:
                            print(f"Failed to send inactivity warning to {channel}: {e}")
                        except Exception as e:
                            print(f"Failed to send inactivity warning to {channel}: {e}")
            except Exception as e:
                print(f"Error checking channel {channel}: {e}")

# ---------------------------
# Slash Commands
# ---------------------------




# Slash command: add/update a payment fee
@bot.tree.command(name="add-payment", description="Add or update a payment method fee (admins only). Example: /add-payment paypal 10")
@app_commands.describe(name="Payment method key", fee="Fee percentage (e.g. 7 for 7%)")
async def add_payment_cmd(interaction: discord.Interaction, name: str, fee: float):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    key = name.lower()
    try:
        PAYMENT_FEES[key] = float(fee)
        write_payment_fees()
        await interaction.response.send_message(f"Saved payment fee **{key}** = {fee}%.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error saving fee: {e}", ephemeral=True)

# Slash command: delete a payment method
@bot.tree.command(name="delete-payment", description="Delete a payment method (fees and instructions) (admins only). Example: /delete-payment paypal")
@app_commands.describe(name="Payment method key to delete")
async def delete_payment_cmd(interaction: discord.Interaction, name: str):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    key = name.lower()
    if key not in PAYMENT_FEES:
        await interaction.response.send_message(f"Payment method **{key}** not found.", ephemeral=True)
        return
    PAYMENT_FEES.pop(key, None)
    write_payment_fees()
    # Also remove from payment instructions
    data = read_json(PAYMENT_JSON)
    data.pop(key, None)
    write_json(PAYMENT_JSON, data)
    await interaction.response.send_message(f"Deleted payment method **{key}**.", ephemeral=True)

# Slash command: set/update a price
@bot.tree.command(name="set-price", description="Set or update a price for a subtype (admins only). Example: /set-price gamepass 4.75")
@app_commands.describe(subtype="Subtype key", price="Price per thousand Robux")
@app_commands.choices(subtype=[
    app_commands.Choice(name="Gamepass", value="gamepass"),
    app_commands.Choice(name="Group Funds", value="groupfunds"),
    app_commands.Choice(name="In-Game Gifting", value="ingame"),
])
async def set_price_cmd(interaction: discord.Interaction, subtype: str, price: float):
    member = interaction.user
    if not isinstance(member, discord.Member) or not (1457147563475075284 in [r.id for r in member.roles]):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    key = subtype.lower()
    if key not in PRICES:
        await interaction.response.send_message(f"Invalid subtype. Valid: {', '.join(PRICES.keys())}", ephemeral=True)
        return
    try:
        PRICES[key] = float(price)
        write_prices()
        await interaction.response.send_message(f"Set price for **{key}** = ${price:.2f} per thousand Robux.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error saving price: {e}", ephemeral=True)

# Slash command: view prices
@bot.tree.command(name="view-prices", description="View current prices (admins only)")
async def view_prices_cmd(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not (1457147563475075284 in [r.id for r in member.roles]):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    text = "\n".join(f"**{k}**: ${v:.2f} per thousand Robux" for k, v in PRICES.items())
    await interaction.response.send_message(embed=discord.Embed(title="Current Prices", description=text), ephemeral=True)
# Slash command: help
@bot.tree.command(name="help", description="Display bot commands and features ")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Bot Help - Guide",
        description="This bot manages a ticket system for Robux purchases and support requests.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🎫 Ticket System",
        value=(
            "**Ticket Creation:**\n"
            "• `/ticket-panel` - Send the ticket creation panel (admins only)\n"
            "• Users can create up to 3 active tickets\n"
            "• Supports Robux purchases (Gamepass, Group Funds, In-Game) and other support\n\n"
            "**Ticket Management:**\n"
            "• `/close` - Close a ticket (staff only)\n"
            "• `/closefail` - Close without adding to balance (staff only)\n"
            "• `/conf` - Confirm payment and move ticket (staff only)\n"
            "• Automatic inactivity closing after 3 days\n"
            "• Transcripts saved to files"
        ),
        inline=False
    )

    embed.add_field(
        name="💰 Pricing & Payments",
        value=(
            "**Price Management:**\n"
            "• `/set-price` - Update Robux prices (price managers only)\n"
            "• `/view-prices` - View current prices (price managers only)\n\n"
            "**Payment Methods:**\n"
            "• `/add-payment` - Add/update payment fees (admins only)\n"
            "• `/delete-payment` - Remove payment method (admins only)\n"
            "• `/edit-payment` - Edit payment instructions (admins only)\n"
            "• `/view-payments` - View payment instructions (admins only)\n"
            "• `/stick` - Set sticky message for a channel (admins only)"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Analytics & Tools",
        value=(
            "**User Info:**\n"
            "• `/info @user` - View total spent by user (staff only)\n"
            "• `/leaderboard` - Top 10 spenders (public)\n\n"
            "**Currency Conversion:**\n"
            "• `/curr <amount> <from> <to>` - Convert currencies\n\n"
            "**Accounting:**\n"
            "• `!addbal @user <amount>` - Add to user balance (staff only)\n"
            "• `!subbal @user <amount>` - Subtract from user balance (staff only)"
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ System Features",
        value=(
            "**Automatic Systems:**\n"
            "• Inactivity warnings after 2 days\n"
            "• Auto-close after 3 days of inactivity\n"
            "• Category-based ticket organization\n"
            "• Persistent data storage (JSON files)\n"
            "• Role-based permissions\n\n"
            "**Data Files:**\n"
            "• `tickets.json` - Active tickets\n"
            "• `accounting.json` - User spending data\n"
            "• `prices.json` - Robux prices\n"
            "• `payment_fees.json` - Payment fees\n"
            "• `transcripts/` - Ticket transcripts"
        ),
        inline=False
    )

    embed.set_footer(text="Use commands in appropriate channels. Staff roles required for most commands.")

    await interaction.response.send_message(embed=embed, ephemeral=False)













@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Syncing commands...")
    try:
        await bot.tree.sync()
        print("Commands synced.")
    except Exception as e:
        print("Failed to sync commands:", e)
    check_inactivity.start()

    # Clean up old sticky messages and resend new ones on restart
    for ch_id, msg_id in list(sticky_message_ids.items()):
        channel = bot.get_channel(int(ch_id))
        if channel and ch_id in sticky_messages:
            try:
                # Delete old sticky
                old_msg = await channel.fetch_message(msg_id)
                await old_msg.delete()
            except Exception:
                pass
            try:
                # Send new sticky
                new_msg = await channel.send(sticky_messages[ch_id])
                sticky_message_ids[ch_id] = new_msg.id
            except Exception as e:
                print(f"Failed to send sticky to {ch_id}: {e}")
    # Save updated IDs
    write_json(STICKY_IDS_JSON, sticky_message_ids)

# /ticket-panel - admin only to send the panel
class AdminOnly(app_commands.CheckFailure):
    pass

def admin_check(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if isinstance(member, discord.Member):
        return is_admin_member(member)
    return False

async def send_ticket_panel(channel: discord.TextChannel):
    """Send the ticket creation panel to a channel."""
    view = TicketPanelView()
    embed = discord.Embed(title="Create a Ticket", description="Select the ticket type below to start.", color=discord.Color.green())
    embed.add_field(name="Robux", value="Buy Robux (Gamepass / Group Funds / In-Game).", inline=False)
    embed.add_field(name="Other", value="Other support requests.", inline=False)
    await channel.send(embed=embed, view=view)

@bot.tree.command(name="ticket-panel", description="Send the ticket creation panel")
async def ticket_panel(interaction: discord.Interaction):
    view = TicketPanelView()
    embed = discord.Embed(title="Create a Ticket", description="Select the ticket type below to start.", color=discord.Color.green())
    embed.add_field(name="Robux", value="Buy Robux (Gamepass / Group Funds / In-Game).", inline=False)
    embed.add_field(name="Other", value="Other support requests.", inline=False)
    await interaction.response.send_message(embed=embed, view=view)

# Use '/edit-payment' (defined below) to update payment instructions.

# ---------------------------
# Accounting helper functions
# ---------------------------
ACCOUNTING_JSON = "accounting.json"

def read_accounting():
    if not os.path.exists(ACCOUNTING_JSON):
        return {"users": {}}
    with open(ACCOUNTING_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def write_accounting(data):
    with open(ACCOUNTING_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def add_to_user_spent(user_id: int, amount: float):
    data = read_accounting()
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {"spent": 0.0}
    data["users"][uid]["spent"] += amount
    write_accounting(data)

# ---------------------------
# Slash /close command
# ---------------------------
@bot.tree.command(name="close", description="Close a ticket (staff only). If no channel provided, will attempt to close current channel.")
@app_commands.describe(channel="The ticket channel to close (optional)")
async def slash_close(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message("This command must be run in a server.", ephemeral=True)
        return

    has_support = any(r.id == SUPPORT_ROLE_ID for r in member.roles)
    if not (is_admin_member(member) or has_support):
        await interaction.response.send_message("You don't have permission to close tickets.", ephemeral=True)
        return

    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("Please provide a valid channel or run this command inside the ticket channel.", ephemeral=True)
        return

    # DM user before closing
    user = None
    ticket_amount = 0.0
    for uid, user_tickets in tickets_data.items():
        if not isinstance(user_tickets, list):
            continue
        for ticket in user_tickets:
            if not isinstance(ticket, dict):
                continue
            if int(ticket.get("channel_id", 0)) == target.id:
                ticket_amount = ticket.get("total_cost", 0.0)
                try:
                    user = await bot.fetch_user(int(uid))
                except Exception:
                    user = None
                break
        else:
            continue
        break

    if user:
        dm_message = "✅ **This transaction has been completed!**\n\nIt has been a pleasure doing business with you! Feel free to vouch 💖\n\n**HOW TO VOUCH:**\n➡️ [Go to the vouch channel](https://discord.com/channels/945694600377552916/965514182986452992)\n\n**Be very detailed on your vouches to Shiba!**\n\n__Example:__\n+Vouch <@1183784957232029742> (items) (price) (your feedback) (photo/proof)\n\n+Vouch <@1183784957232029742> 20,000 Robux via Group Payout, 110$! Very Fast. (Attached an image/photo)\n\n📌 **Please follow the exact format including the '+' as it registers to a bot.**"

        embed = discord.Embed(
            title="Transaction Completed 🎉",
            description=dm_message,
            color=discord.Color.green()
        )

        try:
            await user.send(embed=embed)
        except Exception as e:
            print(f"Could not DM user: {e}")

    # Update accounting JSON
    if ticket_amount > 0:
        add_to_user_spent(user.id, ticket_amount)

    await interaction.response.defer(ephemeral=True)
    await close_ticket(target, closer=interaction.user, reason="Manual close (/close command)")

# ---------------------------
# Prefix ?close command
# ---------------------------
@bot.command(name="close")
async def prefix_close(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    member = ctx.author
    if not isinstance(member, discord.Member):
        await ctx.send("This command must be run in a server.")
        return

    has_support = any(r.id == SUPPORT_ROLE_ID for r in member.roles)
    if not (is_admin_member(member) or has_support):
        await ctx.send("You don't have permission to close tickets.")
        return

    target = channel or ctx.channel
    if not isinstance(target, discord.TextChannel):
        await ctx.send("Please provide a valid channel or run this command inside the ticket channel.")
        return

    # DM user before closing
    user = None
    ticket_amount = 0.0
    for uid, user_tickets in tickets_data.items():
        if not isinstance(user_tickets, list):
            continue
        for ticket in user_tickets:
            if isinstance(ticket, dict) and int(ticket.get("channel_id", 0)) == target.id:
                ticket_amount = ticket.get("total_cost", 0.0)
                try:
                    user = await bot.fetch_user(int(uid))
                except Exception:
                    user = None
                break
        else:
            continue
        break

    if not user:
        await ctx.send("This is not a ticket channel.")
        return

    dm_message = (
        "✅ **This transaction has been completed!**\n\n"
        "It has been a pleasure doing business with you! "
        "Feel free to vouch 💖\n\n"
        "**HOW TO VOUCH:**\n"
        "➡️ [Go to the vouch channel](https://discord.com/channels/945694600377552916/965514182986452992)\n\n"
        "**Be very detailed on your vouches to Shiba!**\n\n"
        "__Example:__\n"
        "+Vouch <@1183784957232029742> (items) (price) (your feedback) (photo/proof)\n\n"
        "+Vouch <@1183784957232029742> 20,000 Robux via Group Payout, 110$! Very Fast. "
        "(Attached an image/photo)\n\n"
        "📌 **Please follow the exact format including the '+' as it registers to a bot.**"
    )

    embed = discord.Embed(
        title="Transaction Completed 🎉",
        description=dm_message,
        color=discord.Color.green()
    )

    try:
        await user.send(embed=embed)
    except Exception as e:
        print(f"Could not DM user: {e}")

    # Update accounting JSON
    if ticket_amount > 0:
        add_to_user_spent(user.id, ticket_amount)

    # Close the ticket
    await close_ticket(target, closer=ctx.author, reason="Manual close (!close command)")


# ---------------------------
# Events
# ---------------------------
@bot.event
async def on_message(message: discord.Message):
    # update ticket last_activity if owner posts in their ticket channel
    if message.author.bot:
        return
    chan = message.channel
    # Remove from pending auto-closes if someone sends a message
    pending_auto_closes.pop(chan.id, None)
    # check if this channel is a ticket channel in tickets_data
    for uid, user_tickets in tickets_data.items():
        if not isinstance(user_tickets, list):
            continue
        for ticket in user_tickets:
            if isinstance(ticket, dict) and int(ticket.get("channel_id", 0)) == chan.id:
                # if author is the ticket owner, update last_activity
                if int(uid) == message.author.id:
                    ticket["last_activity"] = datetime.now(timezone.utc).isoformat()
                    ticket["warned"] = False
                    ticket["warn_time"] = None
                    write_json(TICKET_JSON, tickets_data)
                # also update if support replies? spec said track messages by ticket owner; but let's update last_activity on any new messages in ticket channel
                else:
                    ticket["last_activity"] = datetime.now(timezone.utc).isoformat()
                    write_json(TICKET_JSON, tickets_data)
                break
        else:
            continue
        break

    # Sticky messages
    ch_id = str(chan.id)
    if ch_id in sticky_messages:
        # Cancel existing task
        if ch_id in sticky_tasks:
            sticky_tasks[ch_id].cancel()
        # Create new task
        async def send_sticky():
            await asyncio.sleep(3)  # Delay before sending sticky message
            try:
                # Delete old sticky message if exists
                if ch_id in sticky_message_ids:
                    try:
                        old_msg = await chan.fetch_message(sticky_message_ids[ch_id])
                        await old_msg.delete()
                    except Exception:
                        pass
                # Send new sticky message
                msg = await chan.send(sticky_messages[ch_id])
                sticky_message_ids[ch_id] = msg.id
                write_json(STICKY_IDS_JSON, sticky_message_ids)
            except Exception as e:
                print(f"Failed to send sticky to {chan.id}: {e}")
            sticky_tasks.pop(ch_id, None)
        task = asyncio.create_task(send_sticky())
        sticky_tasks[ch_id] = task

    await bot.process_commands(message)

# ---------------------------
# Background Task: Inactivity checks
# ---------------------------
@tasks.loop(minutes=60)
async def check_inactivity():
    # runs every hour
    now = datetime.now(timezone.utc)
    tickets = read_json(TICKET_JSON)
    changed = False
    for uid, user_tickets in list(tickets.items()):
        for i, data in enumerate(user_tickets):
            try:
                last_str = data.get("last_activity")
                if not last_str:
                    continue
                last = datetime.fromisoformat(last_str)
                warned = data.get("warned", False)
                warn_time_str = data.get("warn_time")
                channel_id = data.get("channel_id")
                user_id = int(uid)
                # if > 3 days and not warned -> send warning
                if not warned:
                    if now - last >= timedelta(days=3):
                        # send warning ping to user in channel
                        try:
                            chan = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
                            if chan:
                                user = chan.guild.get_member(user_id) or await chan.guild.fetch_member(user_id)
                                if user:
                                    await chan.send(content=f"{user.mention} • Your ticket is inactive. It will automatically close in 24 hours unless you reply.")
                                    # mark warned
                                    data["warned"] = True
                                    data["warn_time"] = now.isoformat()
                                    changed = True
                        except Exception:
                            pass
                else:
                    # if warned, check if warn_time >=24h ago -> auto close
                    if warn_time_str:
                        warn_time = datetime.fromisoformat(warn_time_str)
                        if now - warn_time >= timedelta(hours=24):
                            # auto-close
                            try:
                                chan = bot.get_channel(channel_id)
                                if chan:
                                    # fetch a "system" user for closer? use bot.user
                                    await close_ticket(chan, closer=bot.user, reason="Auto-closed due to inactivity")
                                else:
                                    # channel missing; just remove ticket entry
                                    user_tickets.pop(i)
                                    if not user_tickets:
                                        tickets.pop(uid, None)
                                    changed = True
                            except Exception:
                                # attempt removal
                                user_tickets.pop(i)
                                if not user_tickets:
                                    tickets.pop(uid, None)
                                changed = True
            except Exception as e:
                print("Inactivity check error for ticket", uid, e)
    if changed:
        write_json(TICKET_JSON, tickets)
        # also update in-memory
        global tickets_data
        tickets_data = tickets

# ---------------------------
# Helper: slash command parameter handling fix
# ---------------------------
# discord.py requires the app command callbacks parameter names to match; earlier I used 'intraction' typo -
# ensure the 'edit' command signature matches. We'll add a check command for admins to view payment info
@bot.tree.command(name="view-payments", description="View configured payment instructions (admins only)")
async def view_payments(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    data = read_json(PAYMENT_JSON)
    if not data:
        await interaction.response.send_message("No payment instructions configured.", ephemeral=True)
        return
    text = "\n".join(f"**{k}**: {v[:200]}{'...' if len(v) > 200 else ''}" for k, v in data.items())
    await interaction.response.send_message(embed=discord.Embed(title="Payment Instructions", description=text), ephemeral=True)

# Fix the /edit command declared earlier: we need to re-register proper handler to avoid typo
# Remove previous registration and re-add properly by aliasing - easiest is to create a wrapper command with different name:



# Modal-based editor for multiline payment instructions
class PaymentEditModal(Modal):
    def __init__(self, key: str, current_text: str = ""):
        super().__init__(title=f"Edit payment: {key}")
        self.key = key
        # paragraph style allows multiline input in modals
        self.instructions = TextInput(label="Instructions (multiline)", style=discord.TextStyle.paragraph, default=current_text or "", required=False, max_length=2000)
        self.add_item(self.instructions)

    async def on_submit(self, interaction: discord.Interaction):
        # Save the multiline text exactly as provided by the admin
        processed = self.instructions.value
        data = read_json(PAYMENT_JSON)
        data[self.key] = processed
        write_json(PAYMENT_JSON, data)
        await interaction.response.send_message(f"Saved instructions for **{self.key}**.", ephemeral=True)


class StickyEditModal(Modal):
    def __init__(self, ch_id: str, current: str):
        super().__init__(title="Edit sticky message")
        self.ch_id = ch_id
        self.message = TextInput(label="Sticky message (leave empty to remove)", style=discord.TextStyle.paragraph, default=current, required=False, max_length=2000)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        msg = self.message.value.strip()
        ch_id = self.ch_id
        channel = bot.get_channel(int(ch_id))
        if channel:
            # Delete old sticky message if exists
            if ch_id in sticky_message_ids:
                try:
                    old_msg = await channel.fetch_message(sticky_message_ids[ch_id])
                    await old_msg.delete()
                except Exception:
                    pass
                sticky_message_ids.pop(ch_id, None)
            # If new message, send it and save ID
            if msg:
                sticky_messages[ch_id] = msg
                new_msg = await channel.send(msg)
                sticky_message_ids[ch_id] = new_msg.id
            else:
                sticky_messages.pop(ch_id, None)
        else:
            # If channel not found, just update dicts
            if msg:
                sticky_messages[ch_id] = msg
            else:
                sticky_messages.pop(ch_id, None)
                sticky_message_ids.pop(ch_id, None)
        write_json(STICKY_JSON, sticky_messages)
        write_json(STICKY_IDS_JSON, sticky_message_ids)
        await interaction.response.send_message("Sticky message updated.", ephemeral=True)


@bot.tree.command(name="edit-payment", description="Edit payment method instructions (admins only)")
@app_commands.describe(payment_method="Payment method key")
async def edit_payment_cmd(interaction: discord.Interaction, payment_method: str):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("You do not have permission to run this command.", ephemeral=True)
        return
    key = payment_method.lower()
    data = read_json(PAYMENT_JSON)
    current = data.get(key, "")
    modal = PaymentEditModal(key, current)
    await interaction.response.send_modal(modal)


@bot.tree.command(name="stick", description="Set or edit sticky message for a channel (admins only)")
@app_commands.describe(channel="The channel to set sticky for")
async def stick_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    ch_id = str(channel.id)
    current = sticky_messages.get(ch_id, "")
    modal = StickyEditModal(ch_id, current)
    await interaction.response.send_modal(modal)


TICKET_JSON = "tickets.json"  # make sure this matches your existing filename

def load_tickets():
    if not os.path.exists(TICKET_JSON):
        return {}
    with open(TICKET_JSON, "r") as f:
        return json.load(f)

def save_tickets(data):
    with open(TICKET_JSON, "w") as f:
        json.dump(data, f, indent=4)


    # ----- CONFIRM COMMANDS -----
NEEDS_IGG_ID = 1430037751696330872
NEEDS_GF_ID = 1430037817249239145
NEEDS_GP_ID = 1430037995678990356

@bot.tree.command(name="conf", description="Confirm the payment and move ticket to proper category")
async def slash_conf(interaction: discord.Interaction):
    await handle_confirmation(interaction.user, interaction.channel, is_prefix=False, interaction=interaction)

@bot.command(name="conf")
async def prefix_conf(ctx: commands.Context):
    await handle_confirmation(ctx.author, ctx.channel, is_prefix=True)


async def handle_confirmation(user, channel, is_prefix=False, interaction=None):
    """Handles moving the ticket and sending the right embed"""
    tickets = load_tickets()
    ticket_id = None
    ticket = None
    for uid, user_tickets in tickets.items():
        if not isinstance(user_tickets, list):
            continue
        for t in user_tickets:
            if not isinstance(t, dict):
                continue
            if int(t.get("channel_id", 0)) == channel.id:
                ticket_id = uid
                ticket = t
                break
        if ticket:
            break

    if not ticket:
        msg = "This is not a valid ticket channel."
        if is_prefix:
            await channel.send(msg)
        elif interaction:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    robux_type = ticket.get("subtype")

    # Check staff permission
    if isinstance(user, discord.Member):
        user_roles = [r.id for r in user.roles]
        if not any(rid in ADMIN_ROLE_IDS + [SUPPORT_ROLE_ID] for rid in user_roles):
            msg = "You do not have permission to confirm tickets."
            if is_prefix:
                await channel.send(msg)
            elif interaction:
                await interaction.response.send_message(msg, ephemeral=True)
            return

    # Determine new category and embed content
    category_id = None
    embed = None
    if robux_type == "ingame":
        category_id = NEEDS_IGG_ID
        # Move channel and send a plain message ".igg", then return to avoid the later embed send.
        new_category = bot.get_channel(category_id)
        if new_category:
            await channel.edit(category=new_category)
            await channel.send(".igg")
            if not is_prefix and interaction:
                await interaction.response.send_message("Ticket confirmed and moved.", ephemeral=True)
        else:
            msg = "Error: target category not found."
            if is_prefix:
                await channel.send(msg)
            elif interaction:
                await interaction.response.send_message(msg, ephemeral=True)
        return
    elif robux_type == "groupfunds":
        category_id = NEEDS_GF_ID
        embed = discord.Embed(
            title="Group Funds Details Required",
            description=(
                "Please provide us with the following:\n\n"
                "(Username) - (Amount) - Group Funds\n\n"
                "**Example:**\n`xAriefyk - 1000 - Group Funds`\n"
                "FOLLOW THE EXAMPLE CLOSELY"
            ),
            color=discord.Color.blue()
        )
    elif robux_type == "gamepass":
        category_id = NEEDS_GP_ID
        embed = discord.Embed(
            title="Gamepass Purchase Details Required",
            description=(
                "Please send the following:\n\n"
                "• Gamepass link(s)\n"
                "• Price of each gamepass\n"
                "• Example:\n"
                "https://www.roblox.com/game-pass/1016516725/unnamed\n"
                "44286"
            ),
            color=discord.Color.green()
        )
    else:
        msg = "Robux type not found in this ticket."
        if is_prefix:
            await channel.send(msg)
        elif interaction:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    # Move the channel
    new_category = bot.get_channel(category_id)
    if new_category:
        await channel.edit(category=new_category)
        await channel.send(embed=embed)
        if not is_prefix and interaction:
            await interaction.response.send_message("Ticket confirmed and moved.", ephemeral=True)
    else:
        msg = "Error: target category not found."
        if is_prefix:
            await channel.send(msg)
        elif interaction:
            await interaction.response.send_message(msg, ephemeral=True)




#---exchange api


EXCHANGE_API_KEY = "4d06c91d8f5e07ab99bbeb3e"
EXCHANGE_URL = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/"

# ---- Slash Command ----
@tree.command(name="curr", description="Convert between currencies (e.g. /curr 100 USD IDR)")
async def curr_slash(interaction: discord.Interaction, amount: float, from_currency: str, to_currency: str):
    await interaction.response.defer(thinking=True)
    result = await convert_currency(amount, from_currency.upper(), to_currency.upper())
    if isinstance(result, str):
        await interaction.followup.send(result)
    else:
        embed = discord.Embed(
            title="💱 Currency Conversion",
            description=f"**{amount:,.2f} {from_currency.upper()}** = **{result:,.2f} {to_currency.upper()}**",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)

# ---- Prefix Command ----
@commands.command(name="curr", help="Convert between currencies (e.g. !curr 100 USD IDR)")
async def curr_prefix(ctx, amount: float, from_currency: str, to_currency: str):
    async with ctx.typing():
        result = await convert_currency(amount, from_currency.upper(), to_currency.upper())
        if isinstance(result, str):
            await ctx.send(result)
        else:
            embed = discord.Embed(
                title="💱 Currency Conversion",
                description=f"**{amount:,.2f} {from_currency.upper()}** = **{result:,.2f} {to_currency.upper()}**",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

bot.add_command(curr_prefix)

# ---- Helper Function ----
async def convert_currency(amount: float, from_currency: str, to_currency: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(EXCHANGE_URL + from_currency) as response:
            data = await response.json()

    if data["result"] != "success":
        return "⚠️ Error: Could not retrieve exchange rate."

    rates = data.get("conversion_rates", {})
    if to_currency not in rates:
        return f"❌ Invalid currency code: {to_currency}"

    converted = amount * rates[to_currency]
    return converted



# ---------------------------
# /info or ?info @user (staff only)
# ---------------------------
@bot.tree.command(name="info", description="View total spent by a user (staff only)")
@app_commands.describe(user="The user to check")
async def slash_info(interaction: discord.Interaction, user: discord.User):
    member = interaction.user
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await interaction.response.send_message("You don't have permission to view this info.", ephemeral=True)
        return

    data = read_accounting()
    spent = data["users"].get(str(user.id), {}).get("spent", 0.0)

    embed = discord.Embed(
        title=f"Account Info - {user}",
        description=f"💰 **Total Spent:** ${spent:,.2f}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="info")
@commands.has_any_role(*[SUPPORT_ROLE_ID])  # staff-only prefix version
async def prefix_info(ctx: commands.Context, user: discord.User):
    data = read_accounting()
    spent = data["users"].get(str(user.id), {}).get("spent", 0.0)

    embed = discord.Embed(
        title=f"Account Info - {user}",
        description=f"💰 **Total Spent:** ${spent:,.2f}",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

# ---------------------------
# /leaderboard or ?leaderboard (top 10)
# ---------------------------
@bot.tree.command(name="leaderboard", description="Top 10 users by total spent")
async def slash_leaderboard(interaction: discord.Interaction):
    data = read_accounting()
    users_spent = [(uid, info["spent"]) for uid, info in data["users"].items()]
    users_spent.sort(key=lambda x: x[1], reverse=True)
    top10 = users_spent[:10]

    embed = discord.Embed(title="💸 Top 10 Spenders", color=discord.Color.gold())
    if not top10:
        embed.description = "No data available."
    else:
        desc = ""
        for i, (uid, amount) in enumerate(top10, start=1):
            member = await bot.fetch_user(int(uid))
            desc += f"**{i}. {member.mention}** - ${amount:,.2f}\n"
        embed.description = desc
    await interaction.response.send_message(embed=embed)


@bot.command(name="leaderboard")
async def prefix_leaderboard(ctx: commands.Context):
    data = read_accounting()
    users_spent = [(uid, info["spent"]) for uid, info in data["users"].items()]
    users_spent.sort(key=lambda x: x[1], reverse=True)
    top10 = users_spent[:10]

    embed = discord.Embed(title="💸 Top 10 Spenders", color=discord.Color.gold())
    if not top10:
        embed.description = "No data available."
    else:
        desc = ""
        for i, (uid, amount) in enumerate(top10, start=1):
            member = await bot.fetch_user(int(uid))
            desc += f"**{i}. {member.mention}** - ${amount:,.2f}\n"
        embed.description = desc
    await ctx.send(embed=embed)

# ---------------------------
# /closefail or ?closefail <channel?> staff only
# ---------------------------

# Slash version
@bot.tree.command(name="closefail", description="Close a ticket without adding money to balance (staff only)")
@app_commands.describe(channel="The ticket channel to close (optional)")
async def slash_closefail(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    member = interaction.user
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await interaction.response.send_message("You don't have permission to close tickets.", ephemeral=True)
        return

    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("Please provide a valid channel or run this command inside the ticket channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # Close the ticket with failure logic
    await closefail_ticket(target, closer=member, reason="Manual closefail")

# Prefix version
@bot.command(name="closefail")
async def prefix_closefail(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    member = ctx.author
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await ctx.send("You don't have permission to close tickets.")
        return

    target = channel or ctx.channel
    if not isinstance(target, discord.TextChannel):
        await ctx.send("Please provide a valid channel or run this command inside the ticket channel.")
        return

    # Close the ticket with failure logic
    await closefail_ticket(target, closer=member, reason="Manual closefail")







# ---------------------------
# Slash commands
# ---------------------------
@bot.tree.command(name="addbal", description="Add balance to user's total spent (staff only)")
@app_commands.describe(user="The user to add balance to", amount="Amount to add (in USD)")
async def slash_addbal(interaction: discord.Interaction, user: discord.User, amount: float):
    member = interaction.user
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await interaction.response.send_message("You don't have permission to modify balances.", ephemeral=True)
        return

    try:
        data = read_accounting()
        uid = str(user.id)
        if uid not in data["users"]:
            data["users"][uid] = {"spent": 0.0}
        
        data["users"][uid]["spent"] += amount
        write_accounting(data)

        embed = discord.Embed(
            title="Balance Updated ✅",
            description=f"Added ${amount:,.2f} to {user.mention}'s balance\nNew total: ${data['users'][uid]['spent']:,.2f}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@bot.tree.command(name="subbal", description="Subtract balance from user's total spent (staff only)")
@app_commands.describe(user="The user to subtract balance from", amount="Amount to subtract (in USD)")
async def slash_subbal(interaction: discord.Interaction, user: discord.User, amount: float):
    member = interaction.user
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await interaction.response.send_message("You don't have permission to modify balances.", ephemeral=True)
        return

    try:
        data = read_accounting()
        uid = str(user.id)
        if uid not in data["users"]:
            data["users"][uid] = {"spent": 0.0}
        
        data["users"][uid]["spent"] = max(0, data["users"][uid]["spent"] - amount)
        write_accounting(data)

        embed = discord.Embed(
            title="Balance Updated ✅",
            description=f"Subtracted ${amount:,.2f} from {user.mention}'s balance\nNew total: ${data['users'][uid]['spent']:,.2f}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


# ---------------------------
# Prefix (!) versions for staff
# ---------------------------
@bot.command(name="addbal")
async def prefix_addbal(ctx: commands.Context, user: discord.User, amount: float):
    member = ctx.author
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await ctx.send("You don't have permission to modify balances.")
        return

    try:
        data = read_accounting()
        uid = str(user.id)
        if uid not in data["users"]:
            data["users"][uid] = {"spent": 0.0}

        data["users"][uid]["spent"] += amount
        write_accounting(data)

        embed = discord.Embed(
            title="Balance Updated ✅",
            description=f"Added ${amount:,.2f} to {user.mention}'s balance\nNew total: ${data['users'][uid]['spent']:,.2f}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")


@bot.command(name="subbal")
async def prefix_subbal(ctx: commands.Context, user: discord.User, amount: float):
    member = ctx.author
    if not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await ctx.send("You don't have permission to modify balances.")
        return

    try:
        data = read_accounting()
        uid = str(user.id)
        if uid not in data["users"]:
            data["users"][uid] = {"spent": 0.0}

        data["users"][uid]["spent"] = max(0, data['users'][uid]['spent'] - amount)
        write_accounting(data)

        embed = discord.Embed(
            title="Balance Updated ✅",
            description=f"Subtracted ${amount:,.2f} from {user.mention}'s balance\nNew total: ${data['users'][uid]['spent']:,.2f}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")





















# Role IDs for each threshold (highest to lowest)
ROLE_THRESHOLDS = [
    (10000, 1428881303926735049),  # $10k
    (1000, 1283133993008763041),   # $1k
    (500, 1257424577248755783),    # $500
    (100, 965514388759007282),     # $100
]

@tasks.loop(minutes=1)  # adjust frequency as needed
async def update_all_spender_roles():
    for guild in bot.guilds:
        data = read_accounting()
        for uid, info in data.get("users", {}).items():
            member = guild.get_member(int(uid))
            if not member:
                continue  # skip if user not in guild

            amount_spent = info.get("spent", 0)
            assigned_role = None

            # Determine the highest tier role the user qualifies for
            for threshold, role_id in ROLE_THRESHOLDS:
                if amount_spent >= threshold:
                    assigned_role = guild.get_role(role_id)
                    break

            if not assigned_role:
                continue  # user doesn't qualify for any role

            # Remove lower-tier roles
            roles_to_remove = [guild.get_role(rid) for t, rid in ROLE_THRESHOLDS if rid != assigned_role.id]
            roles_to_remove = [r for r in roles_to_remove if r in member.roles]

            try:
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Upgrading spender role")
                if assigned_role not in member.roles:
                    await member.add_roles(assigned_role, reason="Spender role based on balance")
            except Exception as e:
                print(f"Error updating roles for {member}: {e}")


@bot.event
async def on_ready():
    update_all_spender_roles.start()
    print(f"Bot ready. Spender role updater task started.")















# Role IDs for each threshold (highest to lowest)
ROLE_THRESHOLDS = [
    (10000, 1428881303926735049),  # $10k
    (1000, 1283133993008763041),   # $1k
    (500, 1257424577248755783),    # $500
    (100, 965514388759007282),     # $100
]

@tasks.loop(minutes=5)  # Adjust as needed
async def update_all_spender_roles():
    for guild in bot.guilds:
        data = read_accounting()
        for uid, info in data.get("users", {}).items():
            member = guild.get_member(int(uid))
            if not member:
                continue

            amount_spent = info.get("spent", 0)
            assigned_role = None

            # Find the highest tier role
            for threshold, role_id in ROLE_THRESHOLDS:
                if amount_spent >= threshold:
                    assigned_role = guild.get_role(role_id)
                    break

            if not assigned_role:
                continue

            # Remove lower-tier roles
            roles_to_remove = [
                guild.get_role(rid) for t, rid in ROLE_THRESHOLDS if rid != assigned_role.id
            ]
            roles_to_remove = [r for r in roles_to_remove if r in member.roles]

            try:
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Upgrading spender role")
                if assigned_role not in member.roles:
                    await member.add_roles(assigned_role, reason="Spender role based on balance")
            except Exception as e:
                print(f"Error updating roles for {member}: {e}")


# Paste your bot token here (keep it secret!)









@bot.tree.command(name="addppl", description="Add a user to the ticket (staff only).")
@app_commands.describe(user="The user to add to the ticket")
async def add_people_cmd(interaction: discord.Interaction, user: discord.User):
    member = interaction.user
    if not isinstance(member, discord.Member) or not (is_admin_member(member) or any(r.id == SUPPORT_ROLE_ID for r in member.roles)):
        await interaction.response.send_message("You don't have permission to add users to tickets.", ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
        return

    # Build overwrites for the user
    overwrites = channel.overwrites_for(user)
    overwrites.view_channel = True
    overwrites.send_messages = True
    await channel.set_permissions(user, overwrite=overwrites)

    await interaction.response.send_message(f"{user.mention} has been added to the ticket.", ephemeral=True)











@bot.event
async def on_ready():
    global BOOT_TIME
    BOOT_TIME = datetime.now(timezone.utc)
    
    try:
        await bot.tree.sync()
    except Exception as e:
        print("Failed to sync commands:", e)
    try:
        update_all_spender_roles.start()
        check_ticket_inactivity.start()
    except RuntimeError:
        # task already started
        pass
    
    # Auto-manage ticket panel
    channel = bot.get_channel(TICKET_PANEL_CHANNEL_ID)
    if channel and isinstance(channel, discord.TextChannel):
        try:
            # Fetch recent messages from bot
            messages = []
            async for msg in channel.history(limit=50):
                if msg.author == bot.user:
                    messages.append(msg)
            panel_messages = [msg for msg in messages if msg.embeds and any(embed.title == "Create a Ticket" for embed in msg.embeds)]
            
            if panel_messages:
                # Check for panels from current boot
                current_boot_panels = [msg for msg in panel_messages if msg.created_at >= BOOT_TIME]
                if not current_boot_panels:
                    # Delete old panels
                    for msg in panel_messages:
                        await msg.delete()
                    # Send new panel
                    await send_ticket_panel(channel)
                    print("Replaced old ticket panel with new one.")
                else:
                    print("Ticket panel from current boot found, keeping it.")
            else:
                # No panel found, send new one
                await send_ticket_panel(channel)
                print("Sent new ticket panel.")
        except Exception as e:
            print(f"Error managing ticket panel: {e}")
    
    print(f"Bot ready as {bot.user}. Spender role updater and inactivity check tasks started.")

# Start the bot
bot.run(TOKEN)
