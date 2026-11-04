import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_parser import ParseError, parse_event  # noqa: E402

NOW = datetime(2026, 11, 3, 9, 55)  # Tuesday morning


def p(text):
    return parse_event(text, NOW)


@pytest.mark.parametrize("text,title,start,end", [
    ("Football next tuesday 8-10pm", "Football", (2026, 11, 10, 20, 0), (2026, 11, 10, 22, 0)),
    ("football tue 8pm-10pm", "Football", (2026, 11, 3, 20, 0), (2026, 11, 3, 22, 0)),
    ("Dentist 12 nov 3pm", "Dentist", (2026, 11, 12, 15, 0), (2026, 11, 12, 16, 0)),
    ("Dinner with Sandra fri 7:30pm for 2h", "Dinner with Sandra", (2026, 11, 6, 19, 30), (2026, 11, 6, 21, 30)),
    ("lunch tmr 11-1pm", "Lunch", (2026, 11, 4, 11, 0), (2026, 11, 4, 13, 0)),
    ("Call with recruiter tomorrow at 4", "Call with recruiter", (2026, 11, 4, 16, 0), (2026, 11, 4, 17, 0)),
    ("Party sat 10pm-1am", "Party", (2026, 11, 7, 22, 0), (2026, 11, 8, 1, 0)),
    ("Standup 20/11 09:30-10:00", "Standup", (2026, 11, 20, 9, 30), (2026, 11, 20, 10, 0)),
    ("Meeting dec 5 2pm", "Meeting", (2026, 12, 5, 14, 0), (2026, 12, 5, 15, 0)),
    ("gym 7am", "Gym", (2026, 11, 4, 7, 0), (2026, 11, 4, 8, 0)),          # 7am passed -> tomorrow
    ("tue 8am catechism", "Catechism", (2026, 11, 10, 8, 0), (2026, 11, 10, 9, 0)),  # passed today -> next week
    ("Flight 2027-01-15 6:45am", "Flight", (2027, 1, 15, 6, 45), (2027, 1, 15, 7, 45)),
    ("Review next week wed 3-4pm", "Review", (2026, 11, 11, 15, 0), (2026, 11, 11, 16, 0)),
    ("Lunch with team 12 noon", "Lunch with team", (2026, 11, 3, 12, 0), (2026, 11, 3, 13, 0)),
])
def test_timed(text, title, start, end):
    ev = p(text)
    assert ev.title == title
    if start:
        assert not ev.all_day
        assert ev.start == datetime(*start)
        assert ev.end == datetime(*end)


def test_all_day_when_no_time():
    ev = p("Sandra's birthday 14/12")
    assert ev.all_day and ev.title == "Sandra's birthday"
    assert ev.start == datetime(2026, 12, 14)


def test_past_date_rolls_to_next_year():
    assert p("Anniversary 1 jan").start.year == 2027


def test_errors():
    with pytest.raises(ParseError):
        p("football")          # no date/time
    with pytest.raises(ParseError):
        p("next tuesday 8pm")  # no title
    with pytest.raises(ParseError):
        p("Thing 31/2 3pm")    # impossible date
