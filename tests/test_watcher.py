import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watcher import Watcher  # noqa: E402

NOW = datetime(2026, 11, 4, 17, 0)


def occ(uid, title, start, hours=1, rid=None, recurring=False):
    return SimpleNamespace(uid=uid, url="u/" + uid, title=title, start=start,
                           end=start + timedelta(hours=hours), rid=rid or start,
                           recurring=recurring, all_day=False, calendar="Personal")


def weekly(uid, title, first, n):
    return [occ(uid, title, first + timedelta(weeks=i), recurring=True) for i in range(n)]


BASE = [occ("dinner", "Dinner FSC", datetime(2026, 11, 6, 18), 6)] + \
       weekly("footie", "Thurs footie", datetime(2026, 11, 5, 21), 6)


def test_first_poll_is_silent():
    w = Watcher()
    assert w.diff(BASE, NOW) == []


def test_new_interview_reported():
    w = Watcher(); w.diff(BASE, NOW)
    interview = occ("int1", "Interview with Stripe", datetime(2026, 11, 10, 14))
    ch = w.diff(BASE + [interview], NOW)
    assert [(c.kind, c.occ.title) for c in ch] == [("new", "Interview with Stripe")]


def test_bot_created_events_ignored():
    w = Watcher(); w.diff(BASE, NOW)
    mine = occ("abc@telegram-calendar-bot", "GEN ethics", datetime(2026, 11, 13, 11))
    assert w.diff(BASE + [mine], NOW) == []


def test_new_repeating_series_reported_once():
    w = Watcher(); w.diff(BASE, NOW)
    gym = weekly("gym", "Gym", datetime(2026, 11, 9, 7), 5)
    ch = w.diff(BASE + gym, NOW)
    assert len(ch) == 1 and ch[0].kind == "new" and ch[0].repeating


def test_series_rolling_into_window_is_not_news():
    w = Watcher(); w.diff(BASE, NOW)
    more = BASE + [occ("footie", "Thurs footie", datetime(2026, 12, 17, 21), recurring=True)]
    assert w.diff(more, NOW + timedelta(days=1)) == []


def test_moved_event_reported():
    w = Watcher(); w.diff(BASE, NOW)
    moved = [occ("dinner", "Dinner FSC", datetime(2026, 11, 7, 19), 4)] + BASE[1:]  # rid follows the new start
    ch = w.diff(moved, NOW)
    assert [c.kind for c in ch] == ["changed"] and ch[0].before.start.day == 6


def test_removed_event_reported():
    w = Watcher(); w.diff(BASE, NOW)
    ch = w.diff(BASE[1:], NOW)
    assert [(c.kind, c.occ.title) for c in ch] == [("removed", "Dinner FSC")]


def test_cancelled_series_one_message():
    w = Watcher(); w.diff(BASE, NOW)
    ch = w.diff(BASE[:1] + BASE[1:3], NOW)  # footie stopped after 2 weeks
    assert len(ch) == 1 and ch[0].kind == "removed" and ch[0].repeating


def test_past_events_dropping_off_are_not_removals():
    w = Watcher(); w.diff(BASE, NOW)
    later = NOW + timedelta(days=2)  # Thu footie on the 5th is now over
    assert w.diff([o for o in BASE if o.end > later], later) == []


def test_moved_single_occurrence_of_series():
    w = Watcher(); w.diff(BASE, NOW)
    footie = weekly("footie", "Thurs footie", datetime(2026, 11, 5, 21), 6)
    footie[2] = occ("footie", "Thurs footie", datetime(2026, 11, 20, 20), rid=datetime(2026, 11, 19, 21), recurring=True)
    ch = w.diff(BASE[:1] + footie, NOW)
    assert [c.kind for c in ch] == ["changed"] and ch[0].occ.start.day == 20
