"""Small seam for routing an agent request through the local deterministic gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Generic, TypeVar

from .gate import GateDecision, check_eligibility

T = TypeVar("T")


@dataclass(frozen=True)
class InvocationResult(Generic[T]):
    decision: GateDecision
    value: T | None = None


async def invoke_if_eligible(
    database_path: str | Path,
    *,
    trigger_type: str,
    present: bool,
    invoke_agent: Callable[[], Awaitable[T]],
    now: datetime | None = None,
    cooldown_minutes: int = 45,
) -> InvocationResult[T]:
    """Only call the supplied agent function after the local gate allows it.

    Inject the Hermes/cloud call here in a future adapter. A denied decision never
    evaluates ``invoke_agent`` and therefore makes zero model/API calls.
    """
    decision = check_eligibility(
        database_path,
        trigger_type=trigger_type,
        present=present,
        now=now,
        cooldown_minutes=cooldown_minutes,
    )
    if not decision.allowed:
        return InvocationResult(decision=decision)
    return InvocationResult(decision=decision, value=await invoke_agent())
