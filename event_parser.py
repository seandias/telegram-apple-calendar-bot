"""Turn short text like "Football next tuesday 8-10pm" into an event.

Pure Python (stdlib + dateutil) so it can be tested without Telegram or iCloud.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2, "wednes": 2, "wensday": 2, "wednesday's": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "thursady": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sats": 5,
    "sunday": 6, "sun": 6, "suns": 6,
}
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
FILLER = {"on", "at", "from", "this", "next", "the", "for", "@", "-", "–", "by"}

DEFAULT_DURATION = timedelta(hours=1)


class ParseError(ValueError):
    pass


@dataclass
class ParsedEvent:
    title: str
    start: datetime          # naive local time (or midnight for all-day)
    end: datetime
    all_day: bool = False
    free: bool = False       # shows as "Free" in Calendar, never counts as a clash


# ---------------------------------------------------------------- dates

_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MO = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("dmy", re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")),
    ("d_mon", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MO})\b\.?(?:\s+(\d{{4}}))?", re.I)),
    ("mon_d", re.compile(rf"\b({_MO})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)),
    ("rel", re.compile(r"\b(today|tonight|tomorrow|tmr|tmrw|tomoro|day after tomorrow)\b", re.I)),
    ("in_n", re.compile(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", re.I)),
    ("wday", re.compile(rf"\b(next\s+week\s+|next\s+|this\s+|coming\s+)?({_WD})\b\.?", re.I)),
]


def _roll_year(d_month: int, d_day: int, today: date) -> date:
    candidate = date(today.year, d_month, d_day)
    if candidate < today:
        candidate = date(today.year + 1, d_month, d_day)
    return candidate


def _extract_date(text: str, today: date):
    """Return (date or None, weekday_is_bare, text with the date removed)."""
    for kind, pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        try:
            if kind == "iso":
                return date(int(m[1]), int(m[2]), int(m[3])), False, rest
            if kind == "dmy":  # Singapore style: day/month
                day, month = int(m[1]), int(m[2])
                if m[3]:
                    year = int(m[3])
                    year += 2000 if year < 100 else 0
                    return date(year, month, day), False, rest
                return _roll_year(month, day, today), False, rest
            if kind in ("d_mon", "mon_d"):
                if kind == "d_mon":
                    day, month = int(m[1]), MONTHS[m[2].lower()]
                else:
                    month, day = MONTHS[m[1].lower()], int(m[2])
                if m[3]:
                    return date(int(m[3]), month, day), False, rest
                return _roll_year(month, day, today), False, rest
        except ValueError as exc:
            raise ParseError(f"That date doesn't exist ({m[0]}).") from exc
        if kind == "rel":
            word = m[1].lower()
            if word in ("today", "tonight"):
                return today, False, rest
            if word == "day after tomorrow":
                return today + timedelta(days=2), False, rest
            return today + timedelta(days=1), False, rest
        if kind == "in_n":
            n = int(m[1])
            days = n * 7 if m[2].lower().startswith("week") else n
            return today + timedelta(days=days), False, rest
        if kind == "wday":
            prefix = (m[1] or "").lower().strip()
            target = WEEKDAYS[m[2].lower()]
            ahead = (target - today.weekday()) % 7
            if prefix == "next week":
                # the given weekday in the following Mon-Sun week
                monday_next = today + timedelta(days=7 - today.weekday())
                return monday_next + timedelta(days=target), False, rest
            if prefix == "next":
                # first such day after today ("next tue" on a Tue = a week later)
                return today + timedelta(days=ahead or 7), False, rest
            # bare / this / coming: today counts, if the time hasn't passed
            return today + timedelta(days=ahead), ahead == 0, rest
    return None, False, text


# ---------------------------------------------------------------- date ranges (trips)

_SEP = r"\s*(?:-|–|—|to|till|until)\s*"
_DM = r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
_DMON = rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MO})\.?(?:\s+(\d{{4}}))?"
RANGE_PATTERNS = [
    ("dm_dm", re.compile(rf"\b{_DM}{_SEP}{_DM}\b", re.I)),                 # 21/12-7/1
    ("dmon_dmon", re.compile(rf"\b{_DMON}{_SEP}{_DMON}\b", re.I)),         # 21 dec - 7 jan
    ("d_d_mon", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?{_SEP}(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MO})\b\.?(?:\s+(\d{{4}}))?", re.I)),  # 16-21 dec
    ("mon_d_d", re.compile(rf"\b({_MO})\.?\s+(\d{{1,2}}){_SEP}(\d{{1,2}})\b(?!\s*(?:am|pm|:))", re.I)),  # dec 16-21
]


def _year(y: str | None) -> int | None:
    if not y:
        return None
    y = int(y)
    return y + 2000 if y < 100 else y


def _range_dates(d1, m1, y1, d2, m2, y2, today: date) -> tuple[date, date]:
    if y1:
        start = date(y1, m1, d1)
    else:
        start = _roll_year(m1, d1, today)
    end = date(y2 or start.year, m2, d2)
    if end < start and not y2:
        end = date(start.year + 1, m2, d2)  # 21 dec - 7 jan crosses new year
    if end < start:
        raise ParseError("The end date is before the start date.")
    return start, end


def _extract_range(text: str, today: date):
    """Return (first_day, last_day, rest) for multi-day spans, else (None, None, text)."""
    for kind, pat in RANGE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        g = m.groups()
        try:
            if kind == "dm_dm":
                a, b = _range_dates(int(g[0]), int(g[1]), _year(g[2]),
                                    int(g[3]), int(g[4]), _year(g[5]), today)
            elif kind == "dmon_dmon":
                a, b = _range_dates(int(g[0]), MONTHS[g[1].lower()], _year(g[2]),
                                    int(g[3]), MONTHS[g[4].lower()], _year(g[5]), today)
            elif kind == "d_d_mon":
                mo, y = MONTHS[g[2].lower()], _year(g[3])
                a, b = _range_dates(int(g[0]), mo, y, int(g[1]), mo, y, today)
            else:
                mo = MONTHS[g[0].lower()]
                a, b = _range_dates(int(g[1]), mo, None, int(g[2]), mo, None, today)
        except ValueError as exc:
            if isinstance(exc, ParseError):
                raise
            raise ParseError(f"That date doesn't exist ({m[0]}).") from exc
        return a, b, rest
    return None, None, text


# ---------------------------------------------------------------- times

_T = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
RANGE_RE = re.compile(rf"\b{_T}\s*(?:-|–|—|to|till|until)\s*{_T}(?![\w/])", re.I)
SINGLE_MERIDIEM_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)(?!\w)", re.I)
SINGLE_24H_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
AT_BARE_RE = re.compile(r"(?:\bat|@)\s*(\d{1,2})\b(?!\s*(?:/|\d))", re.I)
WORD_TIME_RE = re.compile(r"\b(?:12\s*)?(noon|midday|midnight)\b", re.I)
DURATION_RE = re.compile(
    r"\bfor\s+(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b", re.I)
ALL_DAY_RE = re.compile(r"\ball[\s-]?day\b", re.I)


def _mer(s: str | None) -> str | None:
    if not s:
        return None
    return "pm" if s.lower().startswith("p") else "am"


def _to_24(hour: int, mer: str | None) -> int:
    if hour > 24:
        raise ParseError(f"{hour} isn't a valid hour.")
    if mer == "am":
        return 0 if hour == 12 else hour
    if mer == "pm":
        return hour if hour == 12 else hour + 12
    return hour


def _guess_bare(hour: int) -> int:
    """No am/pm and not 24h-looking: 1-6 means afternoon/evening, 7-11 morning."""
    if 1 <= hour <= 6:
        return hour + 12
    return hour


def _mk(h: int, m: int) -> time:
    if h == 24:
        h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ParseError("That time doesn't look right.")
    return time(h, m)


def _extract_times(text: str):
    """Return (start_time, end_time or None, duration or None, rest)."""
    duration = None
    m = DURATION_RE.search(text)
    if m:
        qty = float(m[1])
        unit = m[2].lower()
        duration = timedelta(hours=qty) if unit.startswith("h") else timedelta(minutes=qty)
        text = (text[: m.start()] + " " + text[m.end():]).strip()

    m = RANGE_RE.search(text)
    if m:
        h1, m1, mer1 = int(m[1]), int(m[2] or 0), _mer(m[3])
        h2, m2, mer2 = int(m[4]), int(m[5] or 0), _mer(m[6])
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        if mer1 is None and mer2 is None:
            # "20:00-22:00" stays as is; "8-10" gets a guess
            if h1 <= 12 and not m[2]:
                h1 = _guess_bare(h1)
            if h2 <= 12 and not m[5]:
                h2 = _guess_bare(h2)
            if h2 < h1 and h2 + 12 <= 24 and h1 >= 12:
                h2 += 12
        else:
            if mer1 is None:  # "8-10pm": borrow the end's am/pm
                mer1 = mer2
                if _to_24(h1, mer1) >= _to_24(h2, mer2) and mer2 == "pm" and h1 != 12:
                    mer1 = "am"  # "11-1pm" means 11am-1pm
            if mer2 is None:
                mer2 = mer1
            h1, h2 = _to_24(h1, mer1), _to_24(h2, mer2)
        return _mk(h1, m1), _mk(h2, m2), duration, rest

    for pat, kind in ((SINGLE_MERIDIEM_RE, "mer"), (SINGLE_24H_RE, "24"),
                      (WORD_TIME_RE, "word"), (AT_BARE_RE, "bare")):
        m = pat.search(text)
        if not m:
            continue
        rest = (text[: m.start()] + " " + text[m.end():]).strip()
        if kind == "mer":
            t = _mk(_to_24(int(m[1]), _mer(m[3])), int(m[2] or 0))
        elif kind == "24":
            t = _mk(int(m[1]), int(m[2]))
        elif kind == "word":
            t = time(0, 0) if m[1].lower() == "midnight" else time(12, 0)
        else:
            t = _mk(_guess_bare(int(m[1])), 0)
        return t, None, duration, rest

    return None, None, duration, text


# ---------------------------------------------------------------- title

def _clean_title(text: str) -> str:
    words = text.split()
    while words and words[0].lower() in FILLER:
        words.pop(0)
    while words and words[-1].lower() in FILLER:
        words.pop()
    title = " ".join(words).strip(" ,.-")
    return title[:1].upper() + title[1:] if title else ""


# ---------------------------------------------------------------- main

def parse_event(text: str, now: datetime) -> ParsedEvent:
    """`now` is naive local time. Raises ParseError if nothing usable is found."""
    raw = " ".join(text.split())
    if not raw:
        raise ParseError("Send something like: Football next tuesday 8-10pm")

    all_day_flag = bool(ALL_DAY_RE.search(raw))
    raw = ALL_DAY_RE.sub(" ", raw)

    first, last, rest = _extract_range(raw, now.date())
    if first is not None:
        title = _clean_title(rest)
        if not title:
            raise ParseError("I couldn't find a name. Try: In Korea 21/12-7/1")
        start = datetime.combine(first, time(0, 0))
        end = datetime.combine(last + timedelta(days=1), time(0, 0))
        return ParsedEvent(title, start, end, all_day=True, free=True)

    the_date, weekday_bare, rest = _extract_date(raw, now.date())
    start_t, end_t, duration, rest = _extract_times(rest)
    title = _clean_title(rest)
    if not title:
        raise ParseError("I couldn't find a name for the event. Try: Dinner with Sandra fri 7pm")

    if start_t is None and the_date is None:
        raise ParseError("I couldn't find a date or time. Try: Dentist 12 nov 3pm")

    if start_t is None or all_day_flag:
        d = the_date or now.date()
        start = datetime.combine(d, time(0, 0))
        return ParsedEvent(title, start, start + timedelta(days=1), all_day=True, free=True)

    if the_date is None:
        the_date = now.date()
        if datetime.combine(the_date, start_t) <= now:
            the_date += timedelta(days=1)  # "gym 7am" said at 9am means tomorrow
    elif weekday_bare and datetime.combine(the_date, start_t) <= now:
        the_date += timedelta(days=7)  # "tue 8am" said on Tue afternoon

    start = datetime.combine(the_date, start_t)
    if end_t is not None:
        end = datetime.combine(the_date, end_t)
        if end <= start:
            end += timedelta(days=1)  # crosses midnight, e.g. 10pm-1am
    else:
        end = start + (duration or DEFAULT_DURATION)
    return ParsedEvent(title, start, end, all_day=False)
