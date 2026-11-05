"""Spot events that appear, move or disappear in the calendar without going through the bot
(e.g. an interview invite accepted from an email).

Pure logic: feed it the current list of occurrences each poll and it returns what changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

BOT_UID_SUFFIX = "@telegram-calendar-bot"


@dataclass
class Change:
    kind: str                 # "new", "changed", "removed"
    occ: object               # the occurrence (new/changed: current, removed: last seen)
    before: object = None     # for "changed": the previous version
    repeating: bool = False


@dataclass
class Watcher:
    window_days: int = 60
    snapshot: dict = field(default_factory=dict)   # key -> occurrence
    seen_uids: set = field(default_factory=set)
    seeded: bool = False
    missing: dict = field(default_factory=dict)    # key -> polls in a row it's been missing
    confirm_polls: int = 2                          # missing this many polls = really removed

    @staticmethod
    def key(o) -> tuple:
        # one-off events are identified by UID alone, so a rescheduled interview is "changed";
        # each occurrence of a repeating event also needs its original start
        return (o.uid or o.url, str(o.rid) if o.recurring else "")

    def diff(self, occurrences, now: datetime, failed_calendars=frozenset()) -> list[Change]:
        """Compare with the previous poll. The first call only records what's there.

        `failed_calendars`: calendars that couldn't be read this time; their events are
        assumed unchanged rather than deleted."""
        current = {}
        for o in occurrences:
            if (o.uid or "").endswith(BOT_UID_SUFFIX):
                continue  # added through the bot: you already know about it
            if o.end <= now:
                continue
            current[self.key(o)] = o

        changes: list[Change] = []
        if self.seeded:
            horizon = now + timedelta(days=self.window_days - 1)
            reported_uids = set()
            for k, o in sorted(current.items(), key=lambda kv: kv[1].start):
                old = self.snapshot.get(k)
                uid = o.uid or o.url
                if old is None:
                    # a known series just rolling into the window isn't news
                    if uid in self.seen_uids or uid in reported_uids:
                        continue
                    changes.append(Change("new", o, repeating=o.recurring))
                    reported_uids.add(uid)
                elif (old.start, old.end, old.title) != (o.start, o.end, o.title):
                    changes.append(Change("changed", o, before=old, repeating=o.recurring))
            removed_uids = set()
            for k, old in self.snapshot.items():
                if k in current or old.start <= now or old.start > horizon:
                    self.missing.pop(k, None)
                    continue
                if old.calendar in failed_calendars:
                    current[k] = old  # couldn't read that calendar: keep it as it was
                    continue
                self.missing[k] = self.missing.get(k, 0) + 1
                if self.missing[k] < self.confirm_polls:
                    current[k] = old  # might be a blip; wait for the next poll to be sure
                    continue
                self.missing.pop(k, None)
                uid = old.uid or old.url
                if uid in removed_uids:
                    continue  # one message per series
                still_there = any((c.uid or c.url) == uid for c in current.values())
                changes.append(Change("removed", old, repeating=old.recurring and still_there))
                removed_uids.add(uid)

        self.snapshot = current
        self.seen_uids |= {o.uid or o.url for o in current.values()}
        self.seeded = True
        return changes
