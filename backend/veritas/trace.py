"""Agent trace — an ordered, timestamped log of every step taken to
produce one evidence record.

The trace is built by the code that runs alongside the deterministic
verdict engine — it never influences the verdict, only records what
happened and why. It's what makes the "LLM narrows, engine decides"
architecture visible to a judge watching a demo.

Step actions (a small, closed vocabulary — the frontend colour-codes them):

    normalise        LLM parsed free text into a structured claim
    select_profile   Agent picked PRESENCE / IDENTITY / TRANSACTION
    plan_evidence    Agent decided which signals to query
    gather           About to call a network API
    result           Response received from a network API
    skip             A planned signal was deliberately not queried
    history          Accumulated evidence loaded from storage
    geofence_check   VERITAS-computed geofence result (own haversine)
    verdict          decide_verdict() returned — pure function
    explain          LLM asked to write the human rationale
    complete         Record persisted; run finished
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


VALID_ACTIONS = frozenset({
    "normalise", "select_profile", "plan_evidence",
    "gather", "result", "skip",
    "history", "geofence_check",
    "verdict", "explain", "complete",
})


@dataclass
class TraceStep:
    step: int
    action: str
    detail: str
    timestamp: str
    data: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.data is None:
            d.pop("data", None)
        return d


@dataclass
class TraceLogger:
    steps: list[TraceStep] = field(default_factory=list)

    # -- generic ------------------------------------------------------

    def add(self, action: str, detail: str, data: Optional[dict] = None) -> TraceStep:
        # Not asserting here — an unknown action still records; the
        # frontend just defaults to a neutral colour for it.
        step = TraceStep(
            step=len(self.steps) + 1,
            action=action,
            detail=detail,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data,
        )
        self.steps.append(step)
        return step

    # -- typed shortcuts (make callers concise + consistent) ----------

    def normalise(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("normalise", detail, data)

    def select_profile(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("select_profile", detail, data)

    def plan_evidence(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("plan_evidence", detail, data)

    def gather(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("gather", detail, data)

    def result(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("result", detail, data)

    def skip(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("skip", detail, data)

    def history(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("history", detail, data)

    def geofence_check(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("geofence_check", detail, data)

    def verdict(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("verdict", detail, data)

    def explain(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("explain", detail, data)

    def complete(self, detail: str, data: Optional[dict] = None) -> TraceStep:
        return self.add("complete", detail, data)

    # -- serialization ------------------------------------------------

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.steps]


# A permissive no-op trace, so callers can accept `trace=None` without
# guarding every call site.
class NullTrace(TraceLogger):
    def add(self, action: str, detail: str, data: Optional[dict] = None) -> TraceStep:
        # Return a step so signatures still match, but don't store it.
        return TraceStep(
            step=0, action=action, detail=detail,
            timestamp=datetime.now(timezone.utc).isoformat(), data=data,
        )
