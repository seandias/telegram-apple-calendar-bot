"""Telegram bot that adds events to Apple (iCloud) Calendar from plain text.

Type: Football next tuesday 8-10pm
Bot:  shows what it understood -> tap Once / Weekly / ... -> checks clashes -> saves.
"""
from __future__ import annotations

import asyncio
import html
import re
import logging
import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from event_parser import ParseError, _extract_date, _extract_range, parse_event
from icloud_calendar import REPEAT_RULES, ICloudCalendar

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("calbot")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Singapore"))
ALLOWED_IDS = {int(x) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").replace(" ", "").split(",") if x}
ALLOWED_USERNAMES = {u.strip().lstrip("@").lower()
                     for u in os.getenv("ALLOWED_TELEGRAM_USERNAMES", "").split(",") if u.strip()}
INCLUDE_ALL_DAY = os.getenv("ALL_DAY_COUNTS_AS_CONFLICT", "false").lower() == "true"

REPEAT_LABELS = {
    "none": "Doesn't repeat",
    "daily": "Every day",
    "weekly": "Every week",
    "fortnightly": "Every 2 weeks",
    "monthly": "Every month",
}

calendar: ICloudCalendar | None = None


def get_calendar() -> ICloudCalendar:
    global calendar
    if calendar is None:
        check = os.getenv("CHECK_CALENDARS")
        calendar = ICloudCalendar(
            apple_id=os.environ["APPLE_ID"],
            app_password=os.environ["APPLE_APP_PASSWORD"],
            calendar_name=os.getenv("CALENDAR_NAME") or None,
            tz=TZ,
            check_calendars=[c for c in check.split(",") if c.strip()] if check else None,
        )
        log.info("Connected to iCloud. Adding to: %s", calendar.target.name)
    return calendar


def allowed(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not ALLOWED_IDS and not ALLOWED_USERNAMES:
        return True
    return user.id in ALLOWED_IDS or (user.username or "").lower() in ALLOWED_USERNAMES


# ------------------------------------------------------------ formatting

def fmt_when(start: datetime, end: datetime, all_day: bool) -> str:
    if all_day:
        last = end.date() - timedelta(days=1)
        if last <= start.date():
            return start.strftime("%a %-d %b %Y") + " (all day)"
        days = (last - start.date()).days + 1
        return f"{start:%a %-d %b} to {last:%a %-d %b %Y} (all day, {days} days)"
    same_day = start.date() == end.date()
    s = start.strftime("%a %-d %b, %-I:%M%p").replace(":00", "").lower()
    e = end.strftime("%-I:%M%p" if same_day else "%a %-d %b, %-I:%M%p").replace(":00", "").lower()
    return f"{s[0].upper()}{s[1:]} to {e}"


def repeat_keyboard(key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Doesn't repeat", callback_data=f"r|{key}|none")],
        [InlineKeyboardButton("Weekly", callback_data=f"r|{key}|weekly"),
         InlineKeyboardButton("Every 2 weeks", callback_data=f"r|{key}|fortnightly")],
        [InlineKeyboardButton("Daily", callback_data=f"r|{key}|daily"),
         InlineKeyboardButton("Monthly", callback_data=f"r|{key}|monthly")],
        [InlineKeyboardButton("Cancel", callback_data=f"x|{key}")],
    ]
    return InlineKeyboardMarkup(rows)


# ------------------------------------------------------------ handlers

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(f"Not authorised. Your Telegram ID is {update.effective_user.id}.")
        return
    await update.message.reply_text(
        "Send me an event in plain text, for example:\n"
        "• Football next tuesday 8-10pm\n"
        "• Dentist 12 nov 3pm\n"
        "• Dinner with Sandra fri 7:30pm for 2h\n"
        "• Sandra's birthday 14/12 (no time = all day)\n"
        "• In Korea 21/12-7/1 (trip banner, never a clash)\n\n"
        "To delete: send “delete footie” (or delete + part of the title, optionally a date). "
        "I'll show matches and ask before deleting anything.\n\n"
        "/week shows the next 7 days, /undo removes the last event I added."
    )


DELETE_RE = re.compile(r"^\s*(?:delete|remove|del|cancel)\b\s*(.*)$", re.I | re.S)
DELETE_WINDOW_DAYS = 180


def fmt_occ_short(o) -> str:
    when = "all day" if o.all_day else f"{o.start:%-I:%M%p}".replace(":00", "").lower()
    return f"{o.start:%a %-d %b} {when} · {o.title}" + (" 🔁" if o.recurring else "")


async def delete_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, q: str):
    """Find events matching `q` and offer them as buttons."""
    now = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = q.strip()
    try:
        cal = await asyncio.to_thread(get_calendar)
        if not q:
            matches = await asyncio.to_thread(cal.find_occurrences, now, today + timedelta(days=14))
        else:
            matches = await asyncio.to_thread(cal.find_occurrences, today,
                                              today + timedelta(days=DELETE_WINDOW_DAYS), q)
            if not matches:  # maybe they included a date: "delete football tue" / "delete tw 16-21 dec"
                try:
                    first, last, rest = _extract_range(q, today.date())
                    if first is None:
                        first, _, rest = _extract_date(q, today.date())
                        last = first
                except ParseError:
                    first = None
                if first is not None:
                    d0 = datetime.combine(first, datetime.min.time(), tzinfo=TZ)
                    d1 = datetime.combine(last, datetime.min.time(), tzinfo=TZ) + timedelta(days=1)
                    matches = await asyncio.to_thread(cal.find_occurrences, d0, d1,
                                                      rest.strip() or None)
    except Exception as exc:
        log.exception("delete search failed")
        await update.message.reply_text(f"Couldn't read iCloud: {exc}")
        return

    if not matches:
        what = f"“{q}”" if q else "anything"
        await update.message.reply_text(
            f"Couldn't find {what} coming up. Try part of the title, e.g. delete footie")
        return

    store = context.user_data.setdefault("del", {})
    rows = []
    for o in matches[:8]:
        k = secrets.token_hex(4)
        store[k] = o
        rows.append([InlineKeyboardButton(fmt_occ_short(o)[:60], callback_data=f"dp|{k}")])
    rows.append([InlineKeyboardButton("Never mind", callback_data="dx|-")])
    more = f"\n(+{len(matches) - 8} more, add a date to narrow it down)" if len(matches) > 8 else ""
    await update.message.reply_text("Which one do you want to delete?" + more,
                                    reply_markup=InlineKeyboardMarkup(rows))


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await delete_flow(update, context, " ".join(context.args or []))


async def on_delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]):
    query = update.callback_query
    action = parts[0]
    if action == "dx":
        await query.edit_message_text("OK, nothing deleted.")
        return
    store = context.user_data.get("del", {})
    occ = store.get(parts[1])
    if occ is None:
        await query.edit_message_text("That list expired. Send delete again.")
        return
    k = parts[1]
    header = (f"🗑 <b>{html.escape(occ.title)}</b>\n{fmt_when(occ.start, occ.end, occ.all_day)}"
              + ("\n🔁 Repeating event" if occ.recurring else ""))

    if action == "dp":  # picked an event: ask to confirm
        if occ.recurring:
            text = header + "\n\nWhat do you want to delete?"
            rows = [
                [InlineKeyboardButton(f"Only {occ.start:%a %-d %b}", callback_data=f"dd|{k}|one")],
                [InlineKeyboardButton("Stop repeating from here", callback_data=f"dd|{k}|future")],
                [InlineKeyboardButton("Keep it", callback_data="dx|-")],
            ]
        else:
            text = header + "\n\nDelete this event?"
            rows = [[InlineKeyboardButton("Yes, delete", callback_data=f"dd|{k}|all"),
                     InlineKeyboardButton("Keep it", callback_data="dx|-")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "dd":  # confirmed
        mode = parts[2]
        await query.edit_message_text(header + "\n\nDeleting…", parse_mode="HTML")
        try:
            cal = await asyncio.to_thread(get_calendar)
            result = await asyncio.to_thread(cal.delete_occurrence, occ, mode)
        except Exception as exc:
            log.exception("delete failed")
            await query.edit_message_text(header + f"\n\n⚠️ Couldn't delete: {html.escape(str(exc))}",
                                          parse_mode="HTML")
            return
        store.clear()  # occurrences may have changed; force a fresh search next time
        msg = {
            "deleted": "Deleted." if not occ.recurring else "Deleted the whole series.",
            "skipped": f"Removed just {occ.start:%a %-d %b}. The rest of the series stays.",
            "stopped": f"Stopped repeating. {occ.start:%a %-d %b} and every one after it are gone, earlier ones stay.",
        }[result]
        await query.edit_message_text(header + "\n\n✅ " + msg, parse_mode="HTML")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    m = DELETE_RE.match(update.message.text)
    if m:
        await delete_flow(update, context, m[1])
        return
    now = datetime.now(TZ).replace(tzinfo=None)
    try:
        ev = parse_event(update.message.text, now)
    except ParseError as exc:
        await update.message.reply_text(str(exc))
        return

    key = secrets.token_hex(4)
    context.user_data.setdefault("pending", {})[key] = ev
    multi_day = ev.all_day and (ev.end - ev.start).days > 1
    await update.message.reply_text(
        f"📅 <b>{html.escape(ev.title)}</b>\n{fmt_when(ev.start, ev.end, ev.all_day)}\n\n" + ("Add it?" if multi_day else "Repeat?"),
        parse_mode="HTML",
        reply_markup=(InlineKeyboardMarkup([[
            InlineKeyboardButton("Add", callback_data=f"r|{key}|none"),
            InlineKeyboardButton("Cancel", callback_data=f"x|{key}"),
        ]]) if multi_day else repeat_keyboard(key)),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not allowed(update):
        await query.answer("Not authorised")
        return
    await query.answer()
    parts = query.data.split("|")
    if parts[0] in ("dp", "dd", "dx"):
        await on_delete_button(update, context, parts)
        return
    action, key = parts[0], parts[1]
    pending = context.user_data.get("pending", {})
    ev = pending.get(key)
    if ev is None:
        await query.edit_message_text("That one expired. Just send it again.")
        return

    if action == "x":
        pending.pop(key, None)
        await query.edit_message_text(f"Cancelled: {ev.title}")
        return

    repeat = parts[2]
    rrule = REPEAT_RULES[repeat]
    header = f"📅 <b>{html.escape(ev.title)}</b>\n{fmt_when(ev.start, ev.end, ev.all_day)}\n🔁 {REPEAT_LABELS[repeat]}"

    if action == "r":  # repeat chosen: check clashes first
        await query.edit_message_text(header + "\n\nChecking your calendar…", parse_mode="HTML")
        start, end = ev.start.replace(tzinfo=TZ), ev.end.replace(tzinfo=TZ)
        try:
            cal = await asyncio.to_thread(get_calendar)
            clashes = [] if ev.all_day else await asyncio.to_thread(
                cal.find_conflicts, start, end, rrule, include_all_day=INCLUDE_ALL_DAY)
        except Exception as exc:
            log.exception("conflict check failed")
            await query.edit_message_text(header + f"\n\n⚠️ Couldn't read iCloud: {html.escape(str(exc))}",
                                          parse_mode="HTML")
            return

        if clashes:
            lines = []
            for occ, b in clashes[:8]:
                when = "all day" if b.all_day else (
                    f"{b.start:%-I:%M%p}–{b.end:%-I:%M%p}".replace(":00", "").lower())
                lines.append(f"• {occ:%a %-d %b}: {html.escape(b.title)} ({when})")
            more = f"\n…and {len(clashes) - 8} more" if len(clashes) > 8 else ""
            scope = " in the next 3 months" if rrule else ""
            await query.edit_message_text(
                header + f"\n\n⚠️ Clashes{scope}:\n" + "\n".join(lines) + more + "\n\nAdd anyway?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Add anyway", callback_data=f"f|{key}|{repeat}"),
                    InlineKeyboardButton("Cancel", callback_data=f"x|{key}"),
                ]]),
            )
            return
        note = "No clashes ✅"
    else:  # "f": user confirmed despite clashes
        note = "Added despite clashes"

    try:
        cal = await asyncio.to_thread(get_calendar)
        url = await asyncio.to_thread(cal.add_event, ev.title, ev.start.replace(tzinfo=TZ),
                                      ev.end.replace(tzinfo=TZ), ev.all_day, rrule, ev.free)
    except Exception as exc:
        log.exception("save failed")
        await query.edit_message_text(header + f"\n\n⚠️ Couldn't save: {html.escape(str(exc))}", parse_mode="HTML")
        return

    pending.pop(key, None)
    context.user_data["last_added"] = (url, ev.title)
    await query.edit_message_text(header + f"\n\n{note}. Saved to {html.escape(cal.target.name or 'calendar')}.",
                                  parse_mode="HTML")


def running_version() -> str:
    sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:7] or "local"
    msg = os.getenv("RAILWAY_GIT_COMMIT_MESSAGE", "").splitlines()[0:1]
    return f"{sha} {msg[0] if msg else ''}".strip()


async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(f"Running: {running_version()}")


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    last = context.user_data.get("last_added")
    if not last:
        await update.message.reply_text("Nothing to undo.")
        return
    url, title = last
    try:
        cal = await asyncio.to_thread(get_calendar)
        await asyncio.to_thread(cal.delete_event, url)
    except Exception as exc:
        await update.message.reply_text(f"Couldn't remove it: {exc}")
        return
    context.user_data.pop("last_added", None)
    await update.message.reply_text(f"Removed: {title}")


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    now = datetime.now(TZ)
    try:
        cal = await asyncio.to_thread(get_calendar)
        events = await asyncio.to_thread(cal.events_between, now, now + timedelta(days=7))
    except Exception as exc:
        await update.message.reply_text(f"Couldn't read iCloud: {exc}")
        return
    if not events:
        await update.message.reply_text("Nothing in the next 7 days.")
        return
    lines, last_day = [], None
    for e in events:
        day = e.start.strftime("%a %-d %b")
        if day != last_day:
            lines.append(f"\n<b>{day}</b>")
            last_day = day
        when = "all day" if e.all_day else f"{e.start:%-I:%M%p}".replace(":00", "").lower()
        lines.append(f"• {when}  {html.escape(e.title)}")
    await update.message.reply_text("\n".join(lines).strip(), parse_mode="HTML")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    if not ALLOWED_IDS and not ALLOWED_USERNAMES:
        log.warning("No ALLOWED_TELEGRAM_IDS or ALLOWED_TELEGRAM_USERNAMES set: anyone who finds the bot can add events!")
    get_calendar()  # fail fast if the Apple login is wrong
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("undo", undo_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("version", version_cmd))
    app.add_handler(CommandHandler(["delete", "remove"], delete_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot running (%s). Ctrl+C to stop.", running_version())
    app.run_polling()


if __name__ == "__main__":
    main()
