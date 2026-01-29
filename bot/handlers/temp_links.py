"""Temporary links handlers with inline keyboard submenus."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)

# Track auto-delete tasks by message_id
_auto_delete_tasks: Dict[int, asyncio.Task] = {}


async def auto_delete_after(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
        if message.id in _auto_delete_tasks:
            del _auto_delete_tasks[message.id]
    except Exception:
        pass


def schedule_auto_delete(message: Message, delay: int = 60):
    """Schedule auto-delete, cancelling any existing task."""
    msg_id = message.id
    if msg_id in _auto_delete_tasks:
        _auto_delete_tasks[msg_id].cancel()
    task = asyncio.create_task(auto_delete_after(message, delay))
    _auto_delete_tasks[msg_id] = task


def reset_auto_delete(message: Message, delay: int = 60):
    """Reset auto-delete timer on interaction."""
    schedule_auto_delete(message, delay)


def format_expiry(link: Dict) -> str:
    """Format expiry info for display."""
    parts = []

    if link.get('expires_at'):
        try:
            expires_at = datetime.fromisoformat(link['expires_at'])
            now = datetime.now(timezone.utc)
            if expires_at > now:
                diff = expires_at - now
                days = diff.days
                hours = diff.seconds // 3600
                if days > 0:
                    parts.append(f"{days}d {hours}h left")
                else:
                    parts.append(f"{hours}h left")
            else:
                parts.append("expired")
        except ValueError:
            pass
    else:
        parts.append("no expiry")

    if link.get('max_uses') is not None:
        uses = link.get('current_uses', 0)
        max_uses = link['max_uses']
        parts.append(f"{uses}/{max_uses} uses")
    else:
        uses = link.get('current_uses', 0)
        parts.append(f"{uses} uses")

    return " • ".join(parts)


def build_main_menu() -> InlineKeyboardMarkup:
    """Build main temp_link menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱️ Set Expiration", callback_data="tl:menu:expiry")],
        [InlineKeyboardButton("🔢 Set Usage Limit", callback_data="tl:menu:uses")],
        [InlineKeyboardButton("🔗 Create Without Limits", callback_data="tl:create:0:0")],
        [InlineKeyboardButton("❌", callback_data="tl:close")],
    ])


def build_expiry_menu() -> InlineKeyboardMarkup:
    """Build expiration selection submenu."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 day", callback_data="tl:expiry:1"),
            InlineKeyboardButton("3 days", callback_data="tl:expiry:3"),
        ],
        [
            InlineKeyboardButton("7 days", callback_data="tl:expiry:7"),
            InlineKeyboardButton("30 days", callback_data="tl:expiry:30"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="tl:menu:main")],
    ])


def build_uses_menu(expiry_days: int = 0) -> InlineKeyboardMarkup:
    """Build usage limit selection submenu."""
    prefix = f"tl:create:{expiry_days}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 use", callback_data=f"{prefix}:1"),
            InlineKeyboardButton("5 uses", callback_data=f"{prefix}:5"),
        ],
        [
            InlineKeyboardButton("10 uses", callback_data=f"{prefix}:10"),
            InlineKeyboardButton("50 uses", callback_data=f"{prefix}:50"),
        ],
        [
            InlineKeyboardButton("♾️ Unlimited", callback_data=f"{prefix}:0"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="tl:menu:main")],
    ])


def build_expiry_then_uses_menu(expiry_days: int) -> InlineKeyboardMarkup:
    """Build menu to set uses after selecting expiry."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ {expiry_days} days expiry selected", callback_data="tl:noop")],
        [
            InlineKeyboardButton("1 use", callback_data=f"tl:create:{expiry_days}:1"),
            InlineKeyboardButton("5 uses", callback_data=f"tl:create:{expiry_days}:5"),
        ],
        [
            InlineKeyboardButton("10 uses", callback_data=f"tl:create:{expiry_days}:10"),
            InlineKeyboardButton("50 uses", callback_data=f"tl:create:{expiry_days}:50"),
        ],
        [InlineKeyboardButton("♾️ Unlimited uses", callback_data=f"tl:create:{expiry_days}:0")],
        [InlineKeyboardButton("◀️ Back", callback_data="tl:menu:expiry")],
    ])


def register_temp_links_handlers(app: Client) -> None:
    """Register temp links command handlers."""

    @app.on_message(filters.command("temp_link") & filters.private)
    async def temp_link_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            await message.reply(await gstr("temp_link_no_user", message), parse_mode=ParseMode.HTML)
            return

        keyboard = build_main_menu()
        sent_msg = await message.reply(
            await gstr("temp_link_menu", message),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        schedule_auto_delete(sent_msg, 60)
        logger.info(f"User {uid} opened temp_link menu")

    @app.on_message(filters.command("activelinks") & filters.private)
    async def activelinks_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            await message.reply(await gstr("temp_link_no_user", message), parse_mode=ParseMode.HTML)
            return

        links = store.get_active_temp_links(uid)

        if not links:
            await message.reply(
                await gstr("active_links_none", message),
                parse_mode=ParseMode.HTML
            )
            return

        # Build inline keyboard with links
        buttons = []
        for link in links[:10]:  # Max 10 links
            token = link['token']
            short_token = token[:8] + "..."
            info = format_expiry(link)
            buttons.append([
                InlineKeyboardButton(f"🔗 {short_token}", callback_data=f"al:view:{token[:16]}"),
                InlineKeyboardButton("🗑️", callback_data=f"al:del:{token[:16]}"),
            ])

        buttons.append([InlineKeyboardButton("❌", callback_data="al:close")])

        keyboard = InlineKeyboardMarkup(buttons)
        sent_msg = await message.reply(
            (await gstr("active_links_list", message)).format(count=len(links)),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        schedule_auto_delete(sent_msg, 60)
        logger.info(f"User {uid} viewed active links ({len(links)} links)")

    @app.on_callback_query(filters.regex(r"^tl:"))
    async def temp_link_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        data = callback.data

        user = store.get_user(uid)
        if not user:
            await callback.answer("Please /start first", show_alert=True)
            return

        parts = data.split(":")
        action = parts[1]

        # Reset auto-delete on interaction
        if action not in ["close", "noop"] and callback.message:
            reset_auto_delete(callback.message, 60)

        # Main menu navigation
        if action == "menu":
            submenu = parts[2]
            if submenu == "main":
                keyboard = build_main_menu()
                await callback.message.edit_text(
                    await gstr("temp_link_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            elif submenu == "expiry":
                keyboard = build_expiry_menu()
                await callback.message.edit_text(
                    await gstr("temp_link_expiry_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            elif submenu == "uses":
                keyboard = build_uses_menu(0)
                await callback.message.edit_text(
                    await gstr("temp_link_uses_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            await callback.answer()

        # Select expiry days, then show uses menu
        elif action == "expiry":
            expiry_days = int(parts[2])
            keyboard = build_expiry_then_uses_menu(expiry_days)
            await callback.message.edit_text(
                await gstr("temp_link_uses_menu", callback),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            await callback.answer()

        # Create link with settings
        elif action == "create":
            expiry_days = int(parts[2])
            max_uses = int(parts[3]) if len(parts) > 3 else 0

            token = await store.create_temp_link(
                uid,
                expires_days=expiry_days if expiry_days > 0 else None,
                max_uses=max_uses if max_uses > 0 else None
            )

            bot_username = (await client.get_me()).username
            link_url = f"https://t.me/{bot_username}?start={token}"

            # Build info string
            info_parts = []
            if expiry_days > 0:
                info_parts.append(f"⏱️ Expires in {expiry_days} days")
            else:
                info_parts.append("⏱️ No expiration")
            if max_uses > 0:
                info_parts.append(f"🔢 Max {max_uses} uses")
            else:
                info_parts.append("🔢 Unlimited uses")
            info_text = "\n".join(info_parts)

            # Cancel auto-delete task
            if callback.message.id in _auto_delete_tasks:
                _auto_delete_tasks[callback.message.id].cancel()
                del _auto_delete_tasks[callback.message.id]

            await callback.message.edit_text(
                (await gstr("temp_link_created", callback)).format(
                    link=link_url,
                    info=info_text
                ),
                parse_mode=ParseMode.HTML
            )
            await callback.answer(await gstr("temp_link_created_alert", callback))
            logger.info(f"User {uid} created temp link: {token[:8]}... (exp={expiry_days}d, max={max_uses})")

        # Close
        elif action == "close":
            if callback.message.id in _auto_delete_tasks:
                _auto_delete_tasks[callback.message.id].cancel()
                del _auto_delete_tasks[callback.message.id]
            await callback.message.delete()
            await callback.answer()

        # No-op
        elif action == "noop":
            await callback.answer()

    @app.on_callback_query(filters.regex(r"^al:"))
    async def activelinks_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        data = callback.data

        parts = data.split(":")
        action = parts[1]

        # Reset auto-delete on interaction
        if action not in ["close"] and callback.message:
            reset_auto_delete(callback.message, 60)

        if action == "close":
            if callback.message.id in _auto_delete_tasks:
                _auto_delete_tasks[callback.message.id].cancel()
                del _auto_delete_tasks[callback.message.id]
            await callback.message.delete()
            await callback.answer()
            return

        token_prefix = parts[2]

        # Find full token
        links = store.get_user_temp_links(uid)
        full_token = None
        link_data = None
        for link in links:
            if link['token'].startswith(token_prefix):
                full_token = link['token']
                link_data = link
                break

        if not full_token or not link_data:
            await callback.answer("Link not found", show_alert=True)
            return

        if action == "view":
            bot_username = (await client.get_me()).username
            link_url = f"https://t.me/{bot_username}?start={full_token}"
            info = format_expiry(link_data)

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Delete", callback_data=f"al:del:{token_prefix}")],
                [InlineKeyboardButton("◀️ Back", callback_data="al:back")],
            ])

            await callback.message.edit_text(
                (await gstr("active_link_view", callback)).format(
                    link=link_url,
                    info=info,
                    uses=link_data.get('current_uses', 0)
                ),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            await callback.answer()

        elif action == "del":
            await store.delete_temp_link(full_token, uid)
            await callback.answer(await gstr("temp_link_deleted_alert", callback))

            # Refresh list
            links = store.get_active_temp_links(uid)
            if not links:
                await callback.message.edit_text(
                    await gstr("active_links_none", callback),
                    parse_mode=ParseMode.HTML
                )
            else:
                buttons = []
                for link in links[:10]:
                    token = link['token']
                    short_token = token[:8] + "..."
                    buttons.append([
                        InlineKeyboardButton(f"🔗 {short_token}", callback_data=f"al:view:{token[:16]}"),
                        InlineKeyboardButton("🗑️", callback_data=f"al:del:{token[:16]}"),
                    ])
                buttons.append([InlineKeyboardButton("❌", callback_data="al:close")])

                await callback.message.edit_text(
                    (await gstr("active_links_list", callback)).format(count=len(links)),
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.HTML
                )
            logger.info(f"User {uid} deleted temp link: {token_prefix}...")

        elif action == "back":
            links = store.get_active_temp_links(uid)
            buttons = []
            for link in links[:10]:
                token = link['token']
                short_token = token[:8] + "..."
                buttons.append([
                    InlineKeyboardButton(f"🔗 {short_token}", callback_data=f"al:view:{token[:16]}"),
                    InlineKeyboardButton("🗑️", callback_data=f"al:del:{token[:16]}"),
                ])
            buttons.append([InlineKeyboardButton("❌", callback_data="al:close")])

            await callback.message.edit_text(
                (await gstr("active_links_list", callback)).format(count=len(links)),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.HTML
            )
            await callback.answer()
