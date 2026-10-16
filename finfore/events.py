"""Calendar-anchored occurrences for saved and temporary what-if events."""
from __future__ import annotations

from datetime import date, timedelta
from math import isfinite

from .models import OneOffEvent
from .util import add_months

EVENT_REPEATS = {
    "once": "Once",
    "daily": "Daily",
    "weekly": "Weekly",
    "biweekly": "Every 2 weeks",
    "monthly": "Monthly",
    "quarterly": "Every 3 months",
    "semiannual": "Every 6 months",
    "annual": "Yearly",
}
_DAY_STEPS = {"daily": 1, "weekly": 7, "biweekly": 14}
_MONTH_STEPS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


def validate_event(event: OneOffEvent) -> None:
    """Validate before writing; past starts remain valid schedule anchors."""
    if not event.label.strip():
        raise ValueError("Enter a name for this event.")
    if not isfinite(event.amount) or abs(event.amount) < .01:
        raise ValueError("Enter a finite, non-zero event amount.")
    if event.repeat not in EVENT_REPEATS:
        raise ValueError("Choose a supported repeat interval.")
    if event.until is not None and event.until < event.when:
        raise ValueError("The end date must be on or after the start date.")
    if event.repeat == "once" and event.until is not None:
        raise ValueError("An end date is only used for a repeating event.")


def event_occurrences(event: OneOffEvent, start: date, end: date) -> list[date]:
    """Dates in the inclusive window, bounded by the optional inclusive end.

    Jump directly to the window, even for old starts. Calendar recurrences
    always use the original day rather than drifting after a short month.
    """
    if not event.active or start > end:
        return []
    if event.repeat not in EVENT_REPEATS:
        raise ValueError(f"Unsupported event repeat interval: {event.repeat}")
    last = min(end, event.until) if event.until is not None else end
    first = max(start, event.when)
    if first > last:
        return []
    if event.repeat == "once":
        return [event.when] if start <= event.when <= last else []
    if event.repeat in _DAY_STEPS:
        step = _DAY_STEPS[event.repeat]
        offset = max(0, ((first - event.when).days + step - 1) // step)
        count = ((last - event.when).days // step) - offset + 1
        return [event.when + timedelta(days=(offset + i) * step) for i in range(count)]
    step = _MONTH_STEPS[event.repeat]
    age = (first.year - event.when.year) * 12 + first.month - event.when.month
    offset = max(0, age // step)
    out = []
    while True:
        # Stop before constructing a date beyond year 9999.
        month_index = (event.when.year - 1) * 12 + event.when.month - 1 + offset * step
        if month_index > (last.year - 1) * 12 + last.month - 1:
            break
        when = add_months(event.when, offset * step)
        if when > last:
            break
        if when >= first:
            out.append(when)
        offset += 1
    return out
