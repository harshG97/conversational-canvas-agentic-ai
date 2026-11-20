"""Date-range helpers for deadline queries (all in US Eastern time).

Weeks run Monday to Sunday. The phrase table is injected into the system
prompt so the model copies a ready-made range instead of doing date math.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

DEFAULT_DAYS = 30   # range used when the user gives no time frame
MAX_DAYS = 150      # furthest end date allowed, counted from today


def now_et() -> datetime:
    return datetime.now(ET)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def resolve_range(
    start_date=None,
    end_date=None,
    *,
    days_ahead: int | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Turn tool inputs into an (inclusive) ET datetime window.

    - start_date / end_date are "YYYY-MM-DD" strings in Eastern time.
    - The window never starts before *now*: these tools return upcoming
      items only, so anything already past due stays out.
    - The end is 23:59:59 on end_date, capped at MAX_DAYS from today.
    - Missing or invalid values fall back to today .. today + 30 days.
      ``days_ahead`` is accepted as a fallback for end_date.
    """
    now = (now or now_et()).astimezone(ET)
    today = now.date()

    start = _parse_date(start_date) or today
    end = _parse_date(end_date)
    if end is None:
        days = days_ahead if isinstance(days_ahead, int) and days_ahead > 0 else DEFAULT_DAYS
        end = today + timedelta(days=days)

    end = min(end, today + timedelta(days=MAX_DAYS))
    if end < start:
        start, end = end, start

    start_dt = max(datetime.combine(start, time.min, tzinfo=ET), now)
    end_dt = datetime.combine(end, time.max, tzinfo=ET)
    return start_dt, end_dt


def _fmt(d: date) -> str:
    return d.isoformat()


def date_reference(now: datetime | None = None) -> str:
    """Return the phrase → date-range table for the system prompt."""
    now = (now or now_et()).astimezone(ET)
    t = now.date()
    sunday = t + timedelta(days=6 - t.weekday())          # end of this week
    next_monday = sunday + timedelta(days=1)
    next_sunday = next_monday + timedelta(days=6)
    saturday = sunday - timedelta(days=1)
    weekend_start = max(t, saturday)
    month_end = t.replace(day=calendar.monthrange(t.year, t.month)[1])
    nm_first = month_end + timedelta(days=1)
    nm_last = nm_first.replace(day=calendar.monthrange(nm_first.year, nm_first.month)[1])

    rows = [
        ("today", t, t),
        ("tomorrow", t + timedelta(days=1), t + timedelta(days=1)),
        ("this week / until next week / by end of week", t, sunday),
        ("this weekend", weekend_start, sunday),
        ("next week", next_monday, next_sunday),
        ("within 2 weeks / in two weeks / next 14 days", t, t + timedelta(days=14)),
        ("week after next", next_monday + timedelta(days=7), next_sunday + timedelta(days=7)),
        ("this month", t, month_end),
        ("next month", nm_first, nm_last),
        ("no time frame given", t, t + timedelta(days=DEFAULT_DAYS)),
    ]
    lines = [
        f"Today is {now:%A, %B %d, %Y} (US Eastern). Weeks run Monday to Sunday.",
        "Date ranges to pass as start_date / end_date (YYYY-MM-DD, inclusive):",
    ]
    for label, a, b in rows:
        lines.append(f"- {label}: {_fmt(a)} to {_fmt(b)} ({a:%a %b %d} to {b:%a %b %d})")
    lines.append(
        "For any other phrase (e.g. \"by Dec 10\", \"next 10 days\"), compute the "
        f"range from today's date. End dates beyond {MAX_DAYS} days are capped."
    )
    return "\n".join(lines)
