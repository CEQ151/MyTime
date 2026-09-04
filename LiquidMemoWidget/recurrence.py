"""Pure recurrence helpers for repeating todo items.

Kept free of PySide6 and state_store imports so both state_store.py (persistence
validation) and app.py (completion handling) can depend on it without cycles.
`apply_recurrence_on_complete` does a *local* import of state_store.parse_ddl
inside the function body to dodge the reverse edge.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta

RECUR_NONE = "none"
RECUR_DAILY = "daily"
RECUR_WEEKLY = "weekly"
RECUR_WEEKDAYS = "weekdays"
RECUR_MONTHLY = "monthly"
RECUR_YEARLY = "yearly"

# Custom intervals: "every:Nd" (every N days) or "every:Nw" (every N weeks).
RECUR_EVERY_ND_PREFIX = "every:"
RECUR_MAX_INTERVAL = 365

RECUR_CHOICES = [
    (RECUR_NONE, "不重复"),
    (RECUR_DAILY, "每天"),
    (RECUR_WEEKLY, "每周"),
    (RECUR_WEEKDAYS, "工作日"),
    (RECUR_MONTHLY, "每月"),
    (RECUR_YEARLY, "每年"),
]

_RECUR_SIMPLE = {RECUR_NONE, RECUR_DAILY, RECUR_WEEKLY, RECUR_WEEKDAYS, RECUR_MONTHLY, RECUR_YEARLY}


def parse_recur(raw: str | None) -> str | None:
    """Return the recurrence token if valid, else None.

    Accepts the fixed choices or "every:Nd"/"every:Nw" with 1 <= N <= 365."""
    token = (raw or "").strip()
    if not token:
        return None
    if token in _RECUR_SIMPLE:
        return token
    if token.startswith(RECUR_EVERY_ND_PREFIX):
        spec = token[len(RECUR_EVERY_ND_PREFIX):]
        if len(spec) >= 2 and spec[-1] in {"d", "w"} and spec[:-1].isdigit():
            count = int(spec[:-1])
            if 1 <= count <= RECUR_MAX_INTERVAL:
                return f"{RECUR_EVERY_ND_PREFIX}{count}{spec[-1]}"
    return None


def recur_label(raw: str) -> str:
    """Human label shown on a row hover. Custom intervals render as 每3天 / 每2周."""
    token = parse_recur(raw) or RECUR_NONE
    if token.startswith(RECUR_EVERY_ND_PREFIX):
        spec = token[len(RECUR_EVERY_ND_PREFIX):]
        count, unit = int(spec[:-1]), spec[-1]
        return f"每{count}天" if unit == "d" else f"每{count}周"
    return dict(RECUR_CHOICES).get(token, "不重复")


def _add_months(base: datetime, months: int) -> datetime:
    """Add months keeping the day-of-month, clamping to the target month's last day
    (Jan 31 + 1 month -> Feb 28/29; Feb 29 + 1 year -> Feb 28)."""
    total = base.year * 12 + (base.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(base.day, monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def _advance(recur: str, current: datetime, now: datetime) -> datetime:
    """One step of the fixed-frequency series from `current`."""
    if recur == RECUR_DAILY:
        return current + timedelta(days=1)
    if recur == RECUR_WEEKLY:
        # +7 days keeps the same weekday as the base occurrence.
        return current + timedelta(days=7)
    if recur == RECUR_WEEKDAYS:
        candidate = current + timedelta(days=1)
        while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
            candidate += timedelta(days=1)
        return candidate
    if recur == RECUR_MONTHLY:
        return _add_months(current, 1)
    if recur == RECUR_YEARLY:
        return _add_months(current, 12)
    return current


def next_occurrence(
    recur: str,
    base: datetime | None,
    anchor: str | None,
    now: datetime,
) -> datetime | None:
    """The next occurrence of `recur` strictly after `now`, or None for no recurrence.

    `base` is the caller-parsed current ddl (naive local); None falls back to `now`.
    Fixed frequencies advance from `base`, skipping missed occurrences until past `now`.
    Custom "every:Nd/Nw" intervals roll from the anchor timestamp instead, so each
    completion starts a fresh interval."""
    token = parse_recur(recur)
    if token is None or token == RECUR_NONE:
        return None
    if token.startswith(RECUR_EVERY_ND_PREFIX):
        spec = token[len(RECUR_EVERY_ND_PREFIX):]
        count = int(spec[:-1])
        step = timedelta(days=count) if spec[-1] == "d" else timedelta(weeks=count)
        if anchor:
            try:
                start = datetime.fromisoformat(anchor)
            except ValueError:
                start = None
            if start is not None and start.tzinfo is not None:
                start = start.replace(tzinfo=None)
        else:
            start = None
        current = start or now
        result = current + step
        while result <= now:
            result += step
        return result
    current = base or now
    result = _advance(token, current, now)
    while result <= now:
        result = _advance(token, result, now)
    return result


def apply_recurrence_on_complete(todo, now_local: datetime) -> bool:
    """Advance a completed recurring TodoItem-like object to its next occurrence.

    Duck-typed: the object needs recur/ddl/recurAnchor/lastDoneAt/done/completedAt
    (and optionally subtasks). Returns True when the todo was rolled forward (the
    caller should keep it instead of archiving); False when there is no next
    occurrence and normal archive handling should proceed."""
    try:
        from state_store import parse_ddl  # local import: state_store imports this module
    except ImportError:  # standalone/test use without state_store on the path
        def parse_ddl(text: str, now: datetime | None = None) -> datetime | None:
            return None

    base = parse_ddl(getattr(todo, "ddl", "") or "", now_local)
    next_dt = next_occurrence(getattr(todo, "recur", "none"), base, getattr(todo, "recurAnchor", None), now_local)
    if next_dt is None:
        return False
    stamp = utc_now()
    todo.ddl = next_dt.strftime("%Y-%m-%d %H:%M")
    todo.recurAnchor = stamp
    todo.lastDoneAt = stamp
    todo.done = False
    todo.completedAt = None
    for sub in getattr(todo, "subtasks", []) or []:
        sub.done = False
    return True


def utc_now() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()