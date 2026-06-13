"""Timezone-aware send queue.

Window: Mon-Thu, 08:00-09:00 professor-local, randomised minute. Never Fri/Sat/Sun.
Persistent APScheduler job store so queued sends survive restarts. Daily cap
enforced from config.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from db.models import Email, Opportunity, Professor
from modules import config_loader, timezones, tracker

_DAY_NAME_TO_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _allowed_weekdays() -> set[int]:
    days = config_loader.config().get("send_window", {}).get("days", ["Mon", "Tue", "Wed", "Thu"])
    return {_DAY_NAME_TO_IDX[d[:3].lower()] for d in days}


def next_send_time(now_local: dt.datetime, *, rng: Optional[random.Random] = None) -> dt.datetime:
    """Compute the next valid local send datetime from `now_local` (tz-aware)."""
    rng = rng or random
    win = config_loader.config().get("send_window", {})
    start_h = win.get("start_hour", 8)
    end_h = win.get("end_hour", 9)
    allowed = _allowed_weekdays()

    minute = rng.randint(0, max(0, (end_h - start_h) * 60 - 1))
    candidate = now_local.replace(hour=start_h, minute=0, second=0, microsecond=0) + dt.timedelta(minutes=minute)

    # If today's window already passed (or today is not allowed), roll forward.
    for add_days in range(0, 8):
        day = candidate + dt.timedelta(days=add_days)
        if day.weekday() not in allowed:
            continue
        if add_days == 0 and now_local >= candidate:
            continue  # window passed today
        return day
    # Fallback (shouldn't hit): next Monday.
    return candidate + dt.timedelta(days=7)


def resolve_send_at_utc(opp: Opportunity, prof: Professor,
                        *, now_utc: Optional[dt.datetime] = None,
                        rng: Optional[random.Random] = None) -> tuple[dt.datetime, bool, str]:
    """Return (utc_send_time, tz_flagged, tz_basis)."""
    tzres = timezones.resolve(
        university=prof.university or (opp.university if opp else "") or "",
        city=opp.city if opp else "",
        country=opp.country if opp else "",
    )
    zone = ZoneInfo(tzres.zone)
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    now_local = now_utc.astimezone(zone)
    send_local = next_send_time(now_local, rng=rng)
    return send_local.astimezone(dt.timezone.utc), tzres.flagged, tzres.basis


# --- Daily cap ---------------------------------------------------------------

def scheduled_count_for_day(session: Session, day_utc: dt.date) -> int:
    start = dt.datetime(day_utc.year, day_utc.month, day_utc.day, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return (
        session.query(Email)
        .filter(Email.status.in_(("scheduled", "sent")),
                Email.scheduled_send_at_utc >= start,
                Email.scheduled_send_at_utc < end)
        .count()
    )


def cap_reached(session: Session, day_utc: dt.date) -> bool:
    cap = config_loader.config().get("daily_send_cap", 10)
    return scheduled_count_for_day(session, day_utc) >= cap


# --- APScheduler integration -------------------------------------------------

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        jobstore_path = config_loader.abspath("scheduler_jobstore")
        jobstore_path.parent.mkdir(parents=True, exist_ok=True)
        _scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{jobstore_path}")}
        )
        _scheduler.start()
    return _scheduler


def register_followup_scan() -> bool:
    """Register the recurring Phase-3 reply-scan / follow-up job (idempotent).

    Returns True if the job is active, False if follow-ups are disabled in config.
    """
    cfg = config_loader.config().get("followup", {})
    if not cfg.get("enabled", True):
        return False
    scheduler = get_scheduler()
    interval = int(cfg.get("scan_interval_minutes", 60))
    scheduler.add_job(
        "modules.followups:scan_job",
        trigger="interval",
        minutes=interval,
        id="followup_scan",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return True


def schedule_send(session: Session, email: Email,
                  *, rng: Optional[random.Random] = None) -> dt.datetime:
    """Queue an approved email for its next valid window, honouring the daily cap."""
    if email.status != "approved":
        raise ValueError("Only 'approved' emails can be scheduled.")
    prof = email.professor
    opp = email.opportunity
    send_utc, flagged, basis = resolve_send_at_utc(opp, prof, rng=rng)

    # Respect the daily cap; roll to the next non-full allowed day.
    for _ in range(14):
        if not cap_reached(session, send_utc.date()):
            break
        send_utc = send_utc + dt.timedelta(days=1)
        # Re-snap to a valid weekday window.
        local = send_utc.astimezone(timezones.zoneinfo_for(country=opp.country or ""))
        send_utc = next_send_time(local, rng=rng).astimezone(dt.timezone.utc)

    email.scheduled_send_at_utc = send_utc
    tracker.transition(session, email, "scheduled",
                       {"send_at_utc": send_utc.isoformat(), "tz_flagged": flagged,
                        "tz_basis": basis})
    session.flush()
    return send_utc
