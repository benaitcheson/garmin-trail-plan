"""Plan data model: steps, sessions and their JSON form."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date

RUN_KINDS = {"easy", "strides", "quality", "long", "b2b", "recovery", "shakeout"}


@dataclass
class Step:
    kind: str  # warmup | run | recovery | cooldown | repeat
    seconds: float | None = None  # None = finish the step with the LAP button
    zone: int | None = None  # Garmin heart-rate zone 1-5
    note: str = ""
    estimate: float = 0  # seconds, used for LAP-button steps
    reps: int = 0
    steps: list[Step] = field(default_factory=list)

    def est_seconds(self) -> float:
        if self.kind == "repeat":
            return self.reps * sum(s.est_seconds() for s in self.steps)
        return self.seconds if self.seconds is not None else self.estimate

    @classmethod
    def from_dict(cls, d: dict) -> Step:
        return cls(**{**d, "steps": [cls.from_dict(s) for s in d.get("steps", [])]})


@dataclass
class Exercise:
    name: str  # Garmin exercise display name, see garminconnect.exercises
    sets: int
    reps: int | None = None
    seconds: int | None = None
    rest: int = 60
    note: str = ""


@dataclass
class Session:
    date: date
    kind: str  # easy | strides | quality | long | b2b | recovery | shakeout | strength | race
    title: str
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    exercises: list[Exercise] = field(default_factory=list)
    week: int = 0
    phase: str = ""
    status: str = "planned"  # planned | done | partial | missed | dropped
    adjusted: str = ""  # why the session was changed from the original plan
    original_title: str = ""

    @property
    def id(self) -> str:
        return f"{self.date.isoformat()}-{'S' if self.kind == 'strength' else 'R'}"

    @property
    def is_run(self) -> bool:
        return self.kind in RUN_KINDS

    @property
    def uploadable(self) -> bool:
        return self.kind != "race" and self.status == "planned"

    @property
    def minutes(self) -> int:
        if self.exercises:
            secs = sum(e.sets * ((e.seconds or (e.reps or 0) * 4) + e.rest) for e in self.exercises)
        else:
            secs = sum(s.est_seconds() for s in self.steps)
        return round(secs / 60)

    def content_hash(self) -> str:
        body = {k: v for k, v in self.to_dict().items() if k in ("title", "description", "steps", "exercises")}
        return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        return cls(
            **{
                **d,
                "date": date.fromisoformat(d["date"]),
                "steps": [Step.from_dict(s) for s in d.get("steps", [])],
                "exercises": [Exercise(**e) for e in d.get("exercises", [])],
            }
        )


def fmt_min(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m}min" if m < 60 else f"{m // 60}h{m % 60:02d}"
