"""Deterministic, local-only gate. This module never makes cloud requests."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import connect, initialize_database, latest_proactive_timestamp, utc_timestamp


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str
    trigger_type: str
    cooldown_remaining_seconds: int = 0
    reserved: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _utc_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _decision_for_previous(
    previous_text: str | None,
    *,
    current: datetime,
    cooldown_minutes: int,
) -> GateDecision:
    if previous_text is None:
        return GateDecision(True, "eligible_proactive", "proactive")
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
        return GateDecision(False, "cooldown", "proactive", remaining)
    return GateDecision(True, "eligible_proactive", "proactive")


def check_eligibility(
    database_path: str | Path,
    *,
    trigger_type: str,
    present: bool,
    now: datetime | None = None,
    cooldown_minutes: int = 45,
) -> GateDecision:
    """Read-only presence and cooldown check; never calls a model or reserves state.

    Reactive user-initiated turns bypass both proactive cooldown and presence
    sensing: the user's direct interaction is the signal to respond. Proactive
    turns still require confirmed presence and remain subject to cooldown.
    """
    if trigger_type not in {"proactive", "reactive"}:
        raise ValueError("trigger_type must be 'proactive' or 'reactive'")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be non-negative")
    if trigger_type == "reactive":
        return GateDecision(True, "eligible_reactive", trigger_type)
    if not present:
        return GateDecision(False, "no_presence", trigger_type)
    return _decision_for_previous(
        latest_proactive_timestamp(database_path),
        current=_utc_now(now),
        cooldown_minutes=cooldown_minutes,
    )


def reserve_proactive_if_eligible(
    database_path: str | Path,
    *,
    present: bool,
    now: datetime | None = None,
    cooldown_minutes: int = 45,
    topic_category: str = "checkin_attempt",
) -> GateDecision:
    """Atomically check and reserve a proactive turn before inference.

    BEGIN IMMEDIATE serializes concurrent proactive triggers so only one can pass.
    The row records an attempted check-in, not proof of user engagement. If a call
    fails downstream, the cooldown still prevents repeated provider attempts.
    """
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be non-negative")
    if not topic_category.strip():
        raise ValueError("topic_category must not be empty")
    if not present:
        return GateDecision(False, "no_presence", "proactive")

    path = initialize_database(database_path)
    current = _utc_now(now)
    with closing(connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT timestamp FROM interactions WHERE trigger_type = 'proactive' "
            "ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone()
        decision = _decision_for_previous(
            str(row["timestamp"]) if row else None,
            current=current,
            cooldown_minutes=cooldown_minutes,
        )
        if not decision.allowed:
            return decision
        connection.execute(
            "INSERT INTO interactions(timestamp, trigger_type, topic_category, engagement_score) "
            "VALUES (?, 'proactive', ?, NULL)",
            (utc_timestamp(current), topic_category.strip()),
        )
    return GateDecision(True, "eligible_proactive", "proactive", reserved=True)
