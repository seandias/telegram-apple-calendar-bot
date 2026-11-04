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
    ("Interview next weds 2-3pm", "Interview", (2026, 11, 11, 14, 0), (2026, 11, 11, 15, 0)),
    ("Lecture thurs 10am", "Lecture", (2026, 11, 5, 10, 0), (2026, 11, 5, 11, 0)),
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


@pytest.mark.parametrize("text,title,first,last", [
    ("In Korea 21/12-7/1", "In Korea", (2026, 12, 21), (2027, 1, 7)),
    ("Taiwan trip 16 dec - 21 dec", "Taiwan trip", (2026, 12, 16), (2026, 12, 21)),
    ("Korea 21 dec to 7 jan", "Korea", (2026, 12, 21), (2027, 1, 7)),
    ("In Taiwan 16-21 dec", "In Taiwan", (2026, 12, 16), (2026, 12, 21)),
    ("Bali dec 1-4", "Bali", (2026, 12, 1), (2026, 12, 4)),
])
def test_trips(text, title, first, last):
    from datetime import timedelta
    ev = p(text)
    assert ev.title == title and ev.all_day and ev.free
    assert ev.start == datetime(*first)
    assert ev.end - timedelta(days=1) == datetime(*last)


def test_time_range_not_mistaken_for_trip():
    ev = p("Football next tuesday 8-10pm")
    assert not ev.all_day and ev.start.hour == 20


@pytest.mark.parametrize("now,text,expected", [
    ((2026, 11, 4, 16, 16), "GEN ethics next friday 11am-12pm", (2026, 11, 13)),  # Wed -> next week's Fri
    ((2026, 11, 4, 16, 16), "GEN ethics friday 11am-12pm", (2026, 11, 6)),        # plain fri = this week
    ((2026, 11, 4, 16, 16), "Interview next weds 2-3pm", (2026, 11, 11)),
    ((2026, 11, 6, 9, 0), "Lunch next monday 12pm", (2026, 11, 9)),              # Fri -> Mon 9
    ((2026, 11, 8, 9, 0), "Lunch next monday 12pm", (2026, 11, 16)),             # Sun -> not tomorrow
    ((2026, 11, 7, 9, 0), "Mass next sunday 10am", (2026, 11, 15)),              # Sat -> Sun 15
])
def test_next_weekday(now, text, expected):
    assert parse_event(text, datetime(*now)).start.date() == datetime(*expected).date()


@pytest.mark.parametrize("text,first,last", [
    ("TW 16dec-21dec", (2026, 12, 16), (2026, 12, 21)),
    ("TW 16dec to 21 dec", (2026, 12, 16), (2026, 12, 21)),
    ("TW 16 dec-21dec", (2026, 12, 16), (2026, 12, 21)),
    ("TW dec16-21", (2026, 12, 16), (2026, 12, 21)),
])
def test_trip_no_spaces(text, first, last):
    from datetime import timedelta
    ev = p(text)
    assert ev.title == "TW" and ev.all_day
    assert ev.start == datetime(*first) and ev.end - timedelta(days=1) == datetime(*last)


def test_single_date_no_space():
    ev = p("Dentist 12nov 3pm")
    assert ev.title == "Dentist" and ev.start == datetime(2026, 11, 12, 15, 0)


@pytest.mark.parametrize("text,title,first,last", [
    ("In taiwan 16 dec to 21 dec", "In taiwan", (2026, 12, 16), (2026, 12, 21)),
    ("In korea 21 dec to 7 jan", "In korea", (2026, 12, 21), (2027, 1, 7)),
    ("In taiwan dec 16 to dec 21", "In taiwan", (2026, 12, 16), (2026, 12, 21)),
    ("In korea from 21st dec to 7th jan", "In korea", (2026, 12, 21), (2027, 1, 7)),
    ("In korea 21/12 to 7/1", "In korea", (2026, 12, 21), (2027, 1, 7)),
])
def test_trip_phrasings(text, title, first, last):
    from datetime import timedelta
    ev = p(text)
    assert ev.title == title and ev.all_day and ev.free
    assert ev.start == datetime(*first) and ev.end - timedelta(days=1) == datetime(*last)
