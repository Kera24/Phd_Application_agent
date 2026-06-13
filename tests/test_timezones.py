"""Timezone resolution: cities, US states, university mapping, DST boundaries, fallback."""
import datetime as dt
from zoneinfo import ZoneInfo

from modules import timezones


def test_city_match():
    r = timezones.resolve(city="Zurich")
    assert r.zone == "Europe/Zurich"
    assert r.flagged is False


def test_university_to_city():
    r = timezones.resolve(university="ETH Zurich")
    assert r.zone == "Europe/Zurich"
    assert not r.flagged


def test_us_state_mapping():
    assert timezones.resolve(state="CA").zone == "America/Los_Angeles"
    assert timezones.resolve(state="NY").zone == "America/New_York"
    assert timezones.resolve(state="TX").zone == "America/Chicago"


def test_us_city_over_state():
    r = timezones.resolve(university="Carnegie Mellon", city="Pittsburgh")
    assert r.zone == "America/New_York"


def test_country_fallback_is_flagged():
    r = timezones.resolve(country="Germany")
    assert r.zone == "Europe/Berlin"
    assert r.flagged is True


def test_total_fallback_utc():
    r = timezones.resolve(country="Atlantis")
    assert r.zone == "UTC"
    assert r.flagged is True


def test_dst_boundary_berlin():
    """Berlin shifts CET->CEST on the last Sunday of March."""
    tz = ZoneInfo("Europe/Berlin")
    # Berlin springs forward at 01:00 UTC (02:00 local -> 03:00 local).
    before = dt.datetime(2026, 3, 29, 0, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    after = dt.datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    assert before.utcoffset() == dt.timedelta(hours=1)   # CET
    assert after.utcoffset() == dt.timedelta(hours=2)    # CEST


def test_dst_boundary_us_eastern():
    tz = ZoneInfo("America/New_York")
    winter = dt.datetime(2026, 1, 15, 12, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    summer = dt.datetime(2026, 7, 15, 12, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    assert winter.utcoffset() == dt.timedelta(hours=-5)  # EST
    assert summer.utcoffset() == dt.timedelta(hours=-4)  # EDT
