"""University -> city -> IANA timezone resolution.

US universities map via city/state. If unresolvable, fall back to the country
capital's timezone and flag it for review.
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

# Country -> capital/representative IANA zone (fallback).
COUNTRY_TZ = {
    "germany": "Europe/Berlin",
    "switzerland": "Europe/Zurich",
    "netherlands": "Europe/Amsterdam",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "denmark": "Europe/Copenhagen",
    "finland": "Europe/Helsinki",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "france": "Europe/Paris",
    "italy": "Europe/Rome",
    "spain": "Europe/Madrid",
    "austria": "Europe/Vienna",
    "belgium": "Europe/Brussels",
    "ireland": "Europe/Dublin",
    "united states": "America/New_York",
    "usa": "America/New_York",
    "us": "America/New_York",
    "australia": "Australia/Sydney",
    "canada": "America/Toronto",
}

# City -> IANA zone (covers common university cities; extend as needed).
CITY_TZ = {
    # Germany / DACH
    "berlin": "Europe/Berlin", "munich": "Europe/Berlin", "münchen": "Europe/Berlin",
    "tübingen": "Europe/Berlin", "tubingen": "Europe/Berlin", "heidelberg": "Europe/Berlin",
    "aachen": "Europe/Berlin", "freiburg": "Europe/Berlin", "darmstadt": "Europe/Berlin",
    "karlsruhe": "Europe/Berlin", "stuttgart": "Europe/Berlin", "bonn": "Europe/Berlin",
    "zurich": "Europe/Zurich", "zürich": "Europe/Zurich", "lausanne": "Europe/Zurich",
    "geneva": "Europe/Zurich", "basel": "Europe/Zurich",
    "vienna": "Europe/Vienna", "graz": "Europe/Vienna",
    # Netherlands / Scandinavia
    "amsterdam": "Europe/Amsterdam", "delft": "Europe/Amsterdam", "eindhoven": "Europe/Amsterdam",
    "utrecht": "Europe/Amsterdam", "leiden": "Europe/Amsterdam", "groningen": "Europe/Amsterdam",
    "stockholm": "Europe/Stockholm", "lund": "Europe/Stockholm", "gothenburg": "Europe/Stockholm",
    "oslo": "Europe/Oslo", "copenhagen": "Europe/Copenhagen", "helsinki": "Europe/Helsinki",
    # UK
    "london": "Europe/London", "oxford": "Europe/London", "cambridge": "Europe/London",
    "edinburgh": "Europe/London", "manchester": "Europe/London", "glasgow": "Europe/London",
    "bristol": "Europe/London", "birmingham": "Europe/London", "leeds": "Europe/London",
    # US (city handles state implicitly for these)
    "new york": "America/New_York", "boston": "America/New_York", "cambridge ma": "America/New_York",
    "pittsburgh": "America/New_York", "philadelphia": "America/New_York", "atlanta": "America/New_York",
    "chicago": "America/Chicago", "austin": "America/Chicago", "ann arbor": "America/Detroit",
    "los angeles": "America/Los_Angeles", "berkeley": "America/Los_Angeles",
    "stanford": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "pasadena": "America/Los_Angeles",
    "denver": "America/Denver", "boulder": "America/Denver", "salt lake city": "America/Denver",
    "phoenix": "America/Phoenix",
    # Australia
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane", "perth": "Australia/Perth", "adelaide": "Australia/Adelaide",
    "canberra": "Australia/Sydney",
}

# US state -> representative IANA zone (used when only state is known).
US_STATE_TZ = {
    "ny": "America/New_York", "ma": "America/New_York", "pa": "America/New_York",
    "ga": "America/New_York", "nc": "America/New_York", "fl": "America/New_York",
    "il": "America/Chicago", "tx": "America/Chicago", "mn": "America/Chicago",
    "mi": "America/Detroit", "co": "America/Denver", "ut": "America/Denver",
    "az": "America/Phoenix", "ca": "America/Los_Angeles", "wa": "America/Los_Angeles",
    "or": "America/Los_Angeles",
}

# A few high-profile universities -> city (helps when city field is missing).
UNIVERSITY_CITY = {
    "eth zurich": "zurich", "epfl": "lausanne",
    "tu munich": "munich", "technical university of munich": "munich",
    "university of tübingen": "tübingen", "max planck": "tübingen",
    "tu delft": "delft", "delft university": "delft",
    "university of amsterdam": "amsterdam", "kth": "stockholm",
    "mit": "cambridge ma", "harvard": "cambridge ma", "stanford university": "stanford",
    "uc berkeley": "berkeley", "carnegie mellon": "pittsburgh", "cmu": "pittsburgh",
    "caltech": "pasadena", "university of oxford": "oxford", "university of cambridge": "cambridge",
    "imperial college": "london", "ucl": "london",
    "university of sydney": "sydney", "unsw": "sydney", "university of melbourne": "melbourne",
}


@dataclass
class TZResult:
    zone: str
    flagged: bool          # True when we had to fall back
    basis: str             # how it was resolved


def resolve(university: str = "", city: str = "", state: str = "",
            country: str = "") -> TZResult:
    uni = (university or "").strip().lower()
    cty = (city or "").strip().lower()
    st = (state or "").strip().lower()
    ctry = (country or "").strip().lower()

    # 1. Direct city match.
    if cty and cty in CITY_TZ:
        return TZResult(CITY_TZ[cty], False, f"city:{cty}")

    # 2. University -> city -> zone.
    if uni:
        for key, mapped_city in UNIVERSITY_CITY.items():
            if key in uni:
                return TZResult(CITY_TZ[mapped_city], False, f"university:{key}")

    # 3. US state.
    if st in US_STATE_TZ:
        return TZResult(US_STATE_TZ[st], False, f"us_state:{st}")

    # 4. Country capital fallback (flagged).
    if ctry in COUNTRY_TZ:
        return TZResult(COUNTRY_TZ[ctry], True, f"country_fallback:{ctry}")

    # 5. Total fallback: UTC, flagged.
    return TZResult("UTC", True, "unresolved")


def zoneinfo_for(university="", city="", state="", country="") -> ZoneInfo:
    return ZoneInfo(resolve(university, city, state, country).zone)
