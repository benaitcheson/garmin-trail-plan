"""Local state: the plan, what's on Garmin, and the adaptation log."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .model import Session


@dataclass
class Store:
    path: Path
    sessions: dict[str, Session] = field(default_factory=dict)
    weeks: dict[str, dict] = field(default_factory=dict)  # monday iso -> {number, phase, race}
    notes: list[str] = field(default_factory=list)
    garmin: dict[str, dict] = field(default_factory=dict)  # session id -> uploaded workout info
    decisions: dict[str, dict] = field(default_factory=dict)  # date iso -> readiness decision
    log: list[dict] = field(default_factory=list)
    reconciled_until: str | None = None

    @classmethod
    def load(cls, path: Path) -> Store:
        if not path.exists():
            return cls(path)
        raw = json.loads(path.read_text())
        return cls(
            path,
            sessions={k: Session.from_dict(v) for k, v in raw.get("sessions", {}).items()},
            weeks=raw.get("weeks", {}),
            notes=raw.get("notes", []),
            garmin=raw.get("garmin", {}),
            decisions=raw.get("decisions", {}),
            log=raw.get("log", []),
            reconciled_until=raw.get("reconciled_until"),
        )

    def save(self) -> None:
        data = {
            "sessions": {k: s.to_dict() for k, s in sorted(self.sessions.items())},
            "weeks": self.weeks,
            "notes": self.notes,
            "garmin": self.garmin,
            "decisions": self.decisions,
            "log": self.log[-500:],
            "reconciled_until": self.reconciled_until,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.path)

    @property
    def has_plan(self) -> bool:
        return bool(self.sessions)

    def on(self, d: date) -> list[Session]:
        return [s for s in self.sessions.values() if s.date == d]

    def run_on(self, d: date) -> Session | None:
        return next((s for s in self.on(d) if s.is_run or s.kind == "race"), None)

    def put(self, s: Session) -> None:
        self.sessions[s.id] = s

    def remove(self, s: Session) -> None:
        self.sessions.pop(s.id, None)

    def between(self, start: date, end: date) -> list[Session]:
        return sorted((s for s in self.sessions.values() if start <= s.date <= end), key=lambda s: (s.date, s.id))

    def note(self, d: date, msg: str) -> None:
        self.log.append({"date": d.isoformat(), "msg": msg})
