"""iCloud Calendar access over CalDAV (the same protocol the Calendar app uses)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import caldav
import recurring_ical_events
from dateutil.rrule import rrulestr
from icalendar import Calendar, Event, vRecur

ICLOUD_URL = "https://caldav.icloud.com"

# label shown on the button -> iCalendar RRULE (None = one-off)
REPEAT_RULES = {
    "none": None,
    "daily": "FREQ=DAILY",
    "weekly": "FREQ=WEEKLY",
    "fortnightly": "FREQ=WEEKLY;INTERVAL=2",
    "monthly": "FREQ=MONTHLY",
}


@dataclass
class BusyEvent:
    title: str
    start: datetime
    end: datetime
    calendar: str
    all_day: bool


@dataclass
class Occurrence:
    """One occurrence of an event, with what's needed to delete it later."""
    title: str
    start: datetime          # local, aware
    end: datetime
    all_day: bool
    recurring: bool
    url: str
    calendar: str
    rid: date | datetime     # original start of this occurrence (RECURRENCE-ID)


def _like(value, ref):
    """Convert date/datetime `value` to the same kind as `ref` (date, naive or aware dt)."""
    if not isinstance(ref, datetime):
        return value.date() if isinstance(value, datetime) else value
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if ref.tzinfo is not None:
        return value.replace(tzinfo=ref.tzinfo) if value.tzinfo is None else value.astimezone(ref.tzinfo)
    return value.replace(tzinfo=None) if value.tzinfo is None else value.replace(tzinfo=None)


def _to_local(value, tz: ZoneInfo) -> tuple[datetime, bool]:
    """icalendar values can be date or datetime (naive or aware). Return local aware dt."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz), True
    raise TypeError(f"Unexpected date value {value!r}")


class ICloudCalendar:
    def __init__(self, apple_id: str, app_password: str, calendar_name: str | None,
                 tz: ZoneInfo, check_calendars: list[str] | None = None):
        self.tz = tz
        self.client = caldav.DAVClient(url=ICLOUD_URL, username=apple_id, password=app_password)
        self.principal = self.client.principal()
        self.calendars = self.principal.calendars()
        if not self.calendars:
            raise RuntimeError("No calendars found on this iCloud account.")

        self.target = None
        if calendar_name:
            for cal in self.calendars:
                if (cal.name or "").strip().lower() == calendar_name.strip().lower():
                    self.target = cal
                    break
            if self.target is None:
                names = ", ".join(c.name or "?" for c in self.calendars)
                raise RuntimeError(f"Calendar '{calendar_name}' not found. You have: {names}")
        else:
            self.target = self.calendars[0]

        if check_calendars:
            wanted = {n.strip().lower() for n in check_calendars}
            self.check = [c for c in self.calendars if (c.name or "").lower() in wanted]
        else:
            self.check = self.calendars

    # ------------------------------------------------------------ reading

    def events_between(self, start: datetime, end: datetime) -> list[BusyEvent]:
        """All event occurrences (recurring ones expanded) overlapping [start, end)."""
        found: list[BusyEvent] = []
        for cal in self.check:
            try:
                results = cal.search(start=start, end=end, event=True, expand=True)
            except Exception:
                # Reminders lists and some shared calendars reject event searches
                continue
            for obj in results:
                ical = obj.icalendar_instance
                for comp in ical.walk("VEVENT"):
                    if str(comp.get("STATUS", "")).upper() == "CANCELLED":
                        continue
                    if str(comp.get("TRANSP", "")).upper() == "TRANSPARENT":
                        continue  # marked "free" in Calendar
                    s, all_day = _to_local(comp.decoded("DTSTART"), self.tz)
                    if comp.get("DTEND") is not None:
                        e, _ = _to_local(comp.decoded("DTEND"), self.tz)
                    elif comp.get("DURATION") is not None:
                        e = s + comp.decoded("DURATION")
                    else:
                        e = s + (timedelta(days=1) if all_day else timedelta(0))
                    if s < end and e > start:
                        found.append(BusyEvent(str(comp.get("SUMMARY", "(no title)")),
                                               s, e, cal.name or "", all_day))
        found.sort(key=lambda b: b.start)
        return found

    def find_occurrences(self, start: datetime, end: datetime,
                         text: str | None = None) -> list[Occurrence]:
        """Event occurrences in [start, end), optionally only titles containing `text`."""
        found: dict[tuple, Occurrence] = {}
        needle = (text or "").strip().lower()
        for cal in self.calendars:
            try:
                objects = cal.search(start=start, end=end, event=True)
            except Exception:
                continue
            for obj in objects:
                try:
                    ical = obj.icalendar_instance
                    occurrences = recurring_ical_events.of(ical).between(start, end)
                except Exception:
                    continue
                recurring = any(c.get("RRULE") is not None for c in ical.walk("VEVENT"))
                for occ in occurrences:
                    if str(occ.get("STATUS", "")).upper() == "CANCELLED":
                        continue
                    title = str(occ.get("SUMMARY", "(no title)"))
                    if needle and needle not in title.lower():
                        continue
                    s, all_day = _to_local(occ.decoded("DTSTART"), self.tz)
                    if occ.get("DTEND") is not None:
                        e, _ = _to_local(occ.decoded("DTEND"), self.tz)
                    elif occ.get("DURATION") is not None:
                        e = s + occ.decoded("DURATION")
                    else:
                        e = s + (timedelta(days=1) if all_day else timedelta(0))
                    rid = (occ.decoded("RECURRENCE-ID") if occ.get("RECURRENCE-ID") is not None
                           else occ.decoded("DTSTART"))
                    url = str(obj.url)
                    found[(url, s)] = Occurrence(title, s, e, all_day, recurring, url,
                                                 cal.name or "", rid)
        return sorted(found.values(), key=lambda o: o.start)

    def delete_occurrence(self, occ: Occurrence, mode: str) -> str:
        """mode: "all" (whole event/series), "one" (just this occurrence),
        "future" (this occurrence and every later one). Returns what happened."""
        obj = caldav.Event(client=self.client, url=occ.url)
        obj.load()
        ical = obj.icalendar_instance
        masters = [c for c in ical.walk("VEVENT") if c.get("RECURRENCE-ID") is None]
        master = masters[0] if masters else None

        if mode == "all" or master is None or master.get("RRULE") is None:
            obj.delete()
            return "deleted"

        mstart = master.decoded("DTSTART")
        rid = _like(occ.rid, mstart)

        def overrides(at_or_after: bool):
            for comp in list(ical.subcomponents):
                if comp.name != "VEVENT" or comp.get("RECURRENCE-ID") is None:
                    continue
                other = _like(comp.decoded("RECURRENCE-ID"), mstart)
                if other == rid or (at_or_after and other > rid):
                    yield comp

        if mode == "future":
            if rid <= mstart:
                obj.delete()  # stopping from the first one = the whole series
                return "deleted"
            rule = master["RRULE"]
            rule = rule[0] if isinstance(rule, list) else rule
            rule.pop("COUNT", None)
            if isinstance(mstart, datetime):
                until = rid - timedelta(seconds=1)
                if until.tzinfo is not None:
                    until = until.astimezone(timezone.utc)  # RFC 5545: UTC when DTSTART has a zone
            else:
                until = rid - timedelta(days=1)
            rule["UNTIL"] = [until]
            for comp in list(overrides(at_or_after=True)):
                ical.subcomponents.remove(comp)
            result = "stopped"
        elif mode == "one":
            for comp in list(overrides(at_or_after=False)):
                ical.subcomponents.remove(comp)
            master.add("exdate", rid)
            result = "skipped"
        else:
            raise ValueError(f"Unknown delete mode {mode!r}")

        obj.data = ical.to_ical().decode()
        obj.save()
        return result

    def find_conflicts(self, start: datetime, end: datetime, rrule: str | None,
                       horizon_days: int = 90, max_occurrences: int = 26,
                       include_all_day: bool = False) -> list[tuple[datetime, BusyEvent]]:
        """Return (occurrence_start, clashing_event) pairs for the new event."""
        duration = end - start
        if rrule:
            rule = rrulestr(rrule, dtstart=start)
            limit = start + timedelta(days=horizon_days)
            occurrences = []
            for occ in rule:
                if occ > limit or len(occurrences) >= max_occurrences:
                    break
                occurrences.append(occ)
        else:
            occurrences = [start]

        busy = self.events_between(occurrences[0], occurrences[-1] + duration)
        clashes = []
        for occ in occurrences:
            occ_end = occ + duration
            for b in busy:
                if b.all_day and not include_all_day:
                    continue
                if b.start < occ_end and b.end > occ:
                    clashes.append((occ, b))
        return clashes

    # ------------------------------------------------------------ writing

    def add_event(self, title: str, start: datetime, end: datetime,
                  all_day: bool, rrule: str | None, free: bool = False) -> str:
        """Create the event. Returns its URL (used by /undo)."""
        cal = Calendar()
        cal.add("prodid", "-//telegram-calendar-bot//EN")
        cal.add("version", "2.0")
        ev = Event()
        ev.add("uid", f"{uuid.uuid4()}@telegram-calendar-bot")
        ev.add("summary", title)
        ev.add("dtstamp", datetime.now(tz=ZoneInfo("UTC")))
        if all_day:
            ev.add("dtstart", start.date())
            ev.add("dtend", end.date())
        else:
            ev.add("dtstart", start)
            ev.add("dtend", end)
        if free:
            ev.add("transp", "TRANSPARENT")  # "Free" in Calendar
        if rrule:
            ev.add("rrule", vRecur.from_ical(rrule))
        cal.add_component(ev)
        try:
            cal.add_missing_timezones()  # embeds the VTIMEZONE so Apple reads the time right
        except AttributeError:
            pass  # older icalendar; iCloud still understands TZID=Asia/Singapore
        created = self.target.save_event(cal.to_ical().decode())
        return str(created.url)

    def delete_event(self, url: str) -> None:
        caldav.Event(client=self.client, url=url).delete()
