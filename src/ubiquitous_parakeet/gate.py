"""Deterministic, local-only gate. This module never makes cloud requests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import latest_proactive_timestamp


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str
    trigger_type: str
    cooldown_remaining_seconds: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_eligibility(
    database_path: str | Path,
    *,
    trigger_type: str,
    present: bool,
    now: datetime | None = None,
    cooldown_minutes: int = 45,
) -> GateDecision:
    """Check local presence and cooldown constraints before any agent invocation.

    An explicit reactive user interaction bypasses the proactive cooldown, but never
    the local presence check. An absent or unknown person is always a zero-call state.
    """
    if trigger_type not in {"proactive", "reactive"}:
        raise ValueError("trigger_type must be 'proactive' or 'reactive'")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be non-negative")
    if not present:
        return GateDecision(False, "no_presence", trigger_type)

    if trigger_type == "reactive":
        return GateDecision(True, "eligible_reactive", trigger_type)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    previous_text = latest_proactive_timestamp(database_path)
    if previous_text is None:
        return GateDecision(True, "eligible_proactive", trigger_type)

    try:
        previous = datetime.fromisoformat(previous_text)
    except ValueError as error:
        raise ValueError(f"Invalid interaction timestamp in database: {previous_text!r}") from error
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    previous = previous.astimezone(timezone.utc)
    next_allowed = previous + timedelta(minutes=cooldown_minutes)
    remaining = max(0, int((next_allowed - current).total_seconds() + 0.999))
    if remaining > 0:
        return GateDecision(False, "cooldown", trigger_type, remaining)
    return GateDecision(True, "eligible_proactive", trigger_type)
