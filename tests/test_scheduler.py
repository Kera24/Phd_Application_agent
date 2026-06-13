"""Scheduler window logic: Mon-Thu 08:00-09:00 local, never Fri/Sat/Sun, randomised minute."""
import datetime as dt
import random
from zoneinfo import ZoneInfo

from modules import scheduler


def _local(y, m, d, h, mi, zone="Europe/Berlin"):
    return dt.datetime(y, m, d, h, mi, tzinfo=ZoneInfo(zone))


def test_window_is_morning_hour():
    rng = random.Random(0)
    # Monday 2026-06-08, 06:00 local -> should schedule same day in window.
    now = _local(2026, 6, 8, 6, 0)
    when = scheduler.next_send_time(now, rng=rng)
    assert when.weekday() == 0  # Monday
    assert when.hour == 8
    assert 0 <= when.minute < 60


def test_passed_window_rolls_forward():
    rng = random.Random(1)
    # Monday 10:00 local -> window passed, go to Tuesday.
    now = _local(2026, 6, 8, 10, 0)
    when = scheduler.next_send_time(now, rng=rng)
    assert when.weekday() == 1  # Tuesday
    assert when.hour == 8


def test_friday_skips_to_monday():
    rng = random.Random(2)
    # Friday 2026-06-12 06:00 -> next allowed is Monday 2026-06-15.
    now = _local(2026, 6, 12, 6, 0)
    when = scheduler.next_send_time(now, rng=rng)
    assert when.weekday() == 0
    assert when.date() == dt.date(2026, 6, 15)


def test_saturday_and_sunday_never_chosen():
    rng = random.Random(3)
    for day in (13, 14):  # Sat, Sun
        now = _local(2026, 6, day, 6, 0)
        when = scheduler.next_send_time(now, rng=rng)
        assert when.weekday() in {0, 1, 2, 3}


def test_minute_randomised_within_hour():
    minutes = set()
    for seed in range(20):
        now = _local(2026, 6, 8, 6, 0)
        minutes.add(scheduler.next_send_time(now, rng=random.Random(seed)).minute)
    assert len(minutes) > 1  # not constant
