"""Pakistan time for customer messages (PKT, UTC+5, no daylight saving): showing times and
reading the pickup times customers type."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

PKT = timezone(timedelta(hours=5), "PKT")

_MINUTES = re.compile(r"^(\d{1,3})\s*(m|min|mins|minute|minutes|minat|mint)?$")
_CLOCK = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|baje|bjy|bje)?$")


def format_time(at: datetime) -> str:
    """e.g. 8:42 PM"""
    return at.astimezone(PKT).strftime("%I:%M %p").lstrip("0")


def parse_pickup(text: str, now: datetime) -> datetime | None:
    """A typed pickup time: "30 min", "8:30 pm", "21:00" or "9 baje". A clock time without
    am/pm is the next time it comes round. None if the text isn't a time."""
    words = " ".join(text.casefold().replace("p.m.", "pm").replace("a.m.", "am").split())
    minutes = _MINUTES.match(words)
    if minutes and (minutes.group(2) or int(minutes.group(1)) > 24):
        return now + timedelta(minutes=int(minutes.group(1)))
    clock = _CLOCK.match(words)
    if not clock:
        return None
    hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
    suffix = clock.group(3)
    if minute > 59 or hour > 23 or (suffix in ("am", "pm") and not 1 <= hour <= 12):
        return None
    if suffix == "pm" and hour != 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    ambiguous = suffix not in ("am", "pm") and 1 <= hour <= 11
    local_now = now.astimezone(PKT)
    best: datetime | None = None
    for h in (hour, hour + 12) if ambiguous else (hour,):
        at = local_now.replace(hour=h, minute=minute, second=0, microsecond=0)
        if at <= local_now:
            at += timedelta(days=1)
        if best is None or at < best:
            best = at
    return None if best is None else best.astimezone(now.tzinfo)
