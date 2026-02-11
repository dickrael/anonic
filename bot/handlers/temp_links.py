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


def build_main_menu(expiry_days: int = 0, max_uses: int = 0) -> InlineKeyboardMarkup:
    """Build main temp_link menu showing current selections + Create button."""
    expiry_label = f"⏱️ {expiry_days} days" if expiry_days > 0 else "⏱️ No expiration"
    uses_label = f"🔢 {max_uses} uses" if max_uses > 0 else "🔢 Unlimited"
    # Encode both settings: tl:menu:expiry:EXP:USES / tl:menu:uses:EXP:USES
    s = f"{expiry_days}:{max_uses}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(expiry_label, callback_data=f"tl:menu:expiry:{s}")],
        [InlineKeyboardButton(uses_label, callback_data=f"tl:menu:uses:{s}")],
        [InlineKeyboardButton("🔗 Create", callback_data=f"tl:create:{s}")],
        [InlineKeyboardButton("❌", callback_data="tl:close")],
    ])


def build_expiry_menu(expiry_days: int = 0, max_uses: int = 0) -> InlineKeyboardMarkup:
    """Build expiration selection submenu."""
    s_uses = str(max_uses)

    def btn(days):
        check = " ✅" if days == expiry_days else ""
        return InlineKeyboardButton(
            f"{days} day{'s' if days > 1 else ''}{check}",
            callback_data=f"tl:expiry:{days}:{s_uses}",
        )

    return InlineKeyboardMarkup([
        [btn(1), btn(3)],
        [btn(7), btn(30)],
        [InlineKeyboardButton("♾️ No expiration" + (" ✅" if expiry_days == 0 else ""),
                              callback_data=f"tl:expiry:0:{s_uses}")],
        [InlineKeyboardButton("◀️ Back", callback_data=f"tl:menu:main:{expiry_days}:{s_uses}")],
    ])


def build_uses_menu(expiry_days: int = 0, max_uses: int = 0) -> InlineKeyboardMarkup:
    """Build usage limit selection submenu."""
    s_exp = str(expiry_days)

    def btn(n):
        check = " ✅" if n == max_uses else ""
        label = f"{n} use{'s' if n > 1 else ''}{check}"
        return InlineKeyboardButton(label, callback_data=f"tl:uses:{s_exp}:{n}")

    return InlineKeyboardMarkup([
        [btn(1), btn(5)],
        [btn(10), btn(50)],
        [InlineKeyboardButton("♾️ Unlimited" + (" ✅" if max_uses == 0 else ""),
                              callback_data=f"tl:uses:{s_exp}:0")],
        [InlineKeyboardButton("◀️ Back", callback_data=f"tl:menu:main:{s_exp}:{max_uses}")],
    ])


def build_active_links_buttons(links: list) -> list:
    """Build inline buttons for active links list."""
    buttons = []
    for i, link in enumerate(links[:10], start=1):
        token = link['token']
        info = format_expiry(link)
        buttons.append([
            InlineKeyboardButton(f"🔗 Link {i} ({info})", callback_data=f"al:view:{token[:16]}"),
            InlineKeyboardButton("🗑️", callback_data=f"al:del:{token[:16]}"),
        ])
    buttons.append([InlineKeyboardButton("❌", callback_data="al:close")])
    return buttons


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
        buttons = build_active_links_buttons(links)
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

        # Parse saved settings from callback data: tl:action:expiry:uses
        saved_expiry = int(parts[3]) if len(parts) > 3 else 0
        saved_uses = int(parts[4]) if len(parts) > 4 else 0

        # Main menu navigation
        if action == "menu":
            submenu = parts[2]
            if submenu == "main":
                keyboard = build_main_menu(saved_expiry, saved_uses)
                await callback.message.edit_text(
                    await gstr("temp_link_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            elif submenu == "expiry":
                keyboard = build_expiry_menu(saved_expiry, saved_uses)
                await callback.message.edit_text(
                    await gstr("temp_link_expiry_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            elif submenu == "uses":
                keyboard = build_uses_menu(saved_expiry, saved_uses)
                await callback.message.edit_text(
                    await gstr("temp_link_uses_menu", callback),
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            await callback.answer()

        # Select expiry days → back to main with selection saved
        elif action == "expiry":
            expiry_days = int(parts[2])
            uses = int(parts[3]) if len(parts) > 3 else 0
            keyboard = build_main_menu(expiry_days, uses)
            await callback.message.edit_text(
                await gstr("temp_link_menu", callback),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            await callback.answer()

        # Select usage limit → back to main with selection saved
        elif action == "uses":
            expiry = int(parts[2]) if len(parts) > 2 else 0
            max_uses = int(parts[3]) if len(parts) > 3 else 0
            keyboard = build_main_menu(expiry, max_uses)
            await callback.message.edit_text(
                await gstr("temp_link_menu", callback),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            await callback.answer()

        # Create link with current settings
        elif action == "create":
            expiry_days = int(parts[2]) if len(parts) > 2 else 0
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

        if action == "back":
            links = store.get_active_temp_links(uid)
            if not links:
                await callback.message.edit_text(
                    await gstr("active_links_none", callback),
                    parse_mode=ParseMode.HTML
                )
            else:
                buttons = build_active_links_buttons(links)
                await callback.message.edit_text(
                    (await gstr("active_links_list", callback)).format(count=len(links)),
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.HTML
                )
            await callback.answer()
            return

        if len(parts) < 3:
            await callback.answer("Invalid action", show_alert=True)
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
                buttons = build_active_links_buttons(links)
                await callback.message.edit_text(
                    (await gstr("active_links_list", callback)).format(count=len(links)),
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.HTML
                )
            logger.info(f"User {uid} deleted temp link: {token_prefix}...")
