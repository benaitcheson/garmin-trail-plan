"""Daily adaptation rules. Pure logic - no Garmin calls.

Each morning:
  1. Reconcile past days against what you actually ran (done / partial / missed).
  2. Classify today from Training Readiness, sleep score and HRV status.
  3. Adjust today (and sometimes the next few days) to match.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import workouts as w
from .model import Session
from .store import Store

SEVERITY = {"good": 0, "low": 1, "poor": 2}
MOVABLE_ONTO = {"easy", "strides", "recovery"}


@dataclass
class Wellness:
    readiness: int | None = None  # Garmin Training Readiness 0-100
    level: str | None = None  # PRIME / HIGH / MODERATE / LOW / POOR
    sleep: int | None = None  # Garmin sleep score 0-100
    hrv: str | None = None  # BALANCED / UNBALANCED / LOW / POOR

    def classify(self) -> str:
        if self.readiness is None and self.sleep is None:
            return "unknown"
        status = "good"
        if self.readiness is not None:
            status = "poor" if self.readiness < 25 else "low" if self.readiness < 50 else "good"
        if self.sleep is not None:
            sleep_status = "poor" if self.sleep < 40 else "low" if self.sleep < 60 else "good"
            status = max(status, sleep_status, key=SEVERITY.get)
        if self.hrv in ("LOW", "POOR") and status == "good":
            status = "low"
        return status

    def summary(self) -> str:
        parts = []
        if self.readiness is not None:
            parts.append(f"readiness {self.readiness}" + (f" ({self.level.title()})" if self.level else ""))
        if self.sleep is not None:
            parts.append(f"sleep {self.sleep}")
        if self.hrv:
            parts.append(f"HRV {self.hrv.title()}")
        return ", ".join(parts) or "no data"


@dataclass
class Activity:
    date: date
    kind: str  # Garmin activity typeKey, e.g. trail_running
    minutes: float

    @property
    def is_run(self) -> bool:
        return "run" in self.kind or self.kind in ("hiking", "mountaineering")

    @property
    def is_strength(self) -> bool:
        return "strength" in self.kind


def reconcile(store: Store, activities: list[Activity], start: date, end: date) -> list[str]:
    """Mark planned sessions between start and end (inclusive) as done/partial/missed."""
    msgs = []
    for s in store.between(start, end):
        if s.status != "planned":
            continue
        day = [a for a in activities if a.date == s.date]
        if s.kind == "strength":
            s.status = "done" if any(a.is_strength for a in day) else "missed"
            continue
        ran = sum(a.minutes for a in day if a.is_run)
        if s.kind == "race":
            s.status = "done" if ran else "missed"
            continue
        ratio = ran / s.minutes if s.minutes else 1
        s.status = "done" if ratio >= 0.6 else "partial" if ratio >= 0.25 else "missed"
        if s.status != "done":
            msgs.append(f"{s.date:%a %d %b}: {s.title} {s.status} ({round(ran)} of {s.minutes} min).")
    if end >= start:
        store.reconciled_until = end.isoformat()
    return msgs


def adapt_today(store: Store, today: date, wellness: Wellness, trained_today: bool, force: bool = False) -> list[str]:
    key = today.isoformat()
    if key in store.decisions and not force:
        return [f"Today's plan was already set this morning ({store.decisions[key]['status']})."]
    if trained_today:
        return ["You've already trained today - leaving today alone."]
    status = wellness.classify()
    if status == "unknown":
        return ["No readiness or sleep data for today yet - sync your watch and run this again."]

    msgs = [f"Today: {wellness.summary()} -> {status.upper()}."]
    msgs += _handle_missed_long(store, today, status)

    run = store.run_on(today)
    if run and run.status == "planned" and run.kind != "race":
        if status == "low":
            msgs += _downgrade(store, run, today)
        elif status == "poor":
            msgs += _poor_day(store, run, today)
    if status == "poor":
        for s in store.on(today):
            if s.kind == "strength" and s.status == "planned":
                store.remove(s)
                msgs.append("Strength session dropped - rest is the priority today.")

    store.decisions[key] = {
        "status": status,
        "readiness": wellness.readiness,
        "sleep": wellness.sleep,
        "hrv": wellness.hrv,
    }
    recent = [store.decisions.get((today - timedelta(days=i)).isoformat(), {}).get("status") for i in range(3)]
    if all(r in ("low", "poor") for r in recent):
        msgs += _deload(store, today + timedelta(days=1), today + timedelta(days=3))

    if len(msgs) == 1:
        msgs.append(f"No changes - {run.title if run else 'rest day'} as planned." if status == "good" else "Nothing to change today.")
    for m in msgs:
        store.note(today, m)
    return msgs


def _copy(s: Session, day: date) -> Session:
    c = Session.from_dict(s.to_dict())
    c.date = day
    return c


def _replace(store: Store, old: Session, new: Session, reason: str) -> Session:
    new.week, new.phase = old.week, old.phase
    new.original_title = old.original_title or old.title
    new.adjusted = reason
    store.remove(old)
    store.put(new)
    return new


def _downgrade(store: Store, run: Session, today: date) -> list[str]:
    if run.kind == "quality":
        moved = _move(store, run, today, days_ahead=2)
        _replace(store, run, w.easy(today, min(run.minutes, 50)), "low readiness: hard session swapped for easy")
        return [f"{run.title} -> easy run today. " + (f"Moved to {moved:%a}." if moved else "Dropped this week.")]
    if run.kind == "long":
        new = w.long_run(today, run.minutes * 0.75)
        new.description = run.description
        _replace(store, run, new, "low readiness: shortened 25%, all Zone 2")
        return [f"{run.title} shortened to {new.title} (all Zone 2)."]
    if run.kind == "b2b":
        new = _replace(store, run, w.back_to_back(today, run.minutes * 0.7), "low readiness: shortened 30%")
        return [f"{run.title} shortened to {new.title}."]
    if run.kind in ("easy", "strides"):
        new = _replace(store, run, w.easy(today, run.minutes * 0.85), "low readiness: shorter and easy")
        return [f"{run.title} -> {new.title}."]
    return []


def _poor_day(store: Store, run: Session, today: date) -> list[str]:
    if run.kind == "long" and run.phase != "race":
        moved = _move(store, run, today, days_ahead=1)
        if moved:
            store.remove(run)
            return [f"{run.title} moved to tomorrow. Rest today."]
        new = w.long_run(today, run.minutes * 0.5)
        _replace(store, run, new, "poor readiness: halved")
        return [f"{run.title} halved to {new.title}, very easy."]
    if run.kind in ("recovery", "shakeout"):
        return []
    _replace(store, run, w.recovery(today, 30), "poor readiness: recovery only")
    return [f"{run.title} -> 30 min recovery run (or take a rest day)."]


def _move(store: Store, s: Session, today: date, days_ahead: int) -> date | None:
    """Move s to the next suitable day in the same week, replacing an easy run there."""
    for i in range(1, days_ahead + 1):
        d = today + timedelta(days=i)
        if d.weekday() < today.weekday():  # don't cross into next week
            break
        target = store.run_on(d)
        after = store.run_on(d + timedelta(days=1))
        before = store.run_on(d - timedelta(days=1)) if i > 1 else None
        ok_target = target is None or (target.kind in MOVABLE_ONTO | ({"b2b"} if s.kind == "long" else set()))
        ok_after = s.kind == "long" or after is None or after.kind not in ("long", "quality", "race")
        ok_before = before is None or before.kind not in ("quality", "long")
        if ok_target and ok_after and ok_before and (target is None or target.phase != "race"):
            if target:
                store.remove(target)
            moved = _copy(s, d)
            moved.original_title = s.original_title or s.title
            moved.adjusted = f"moved from {today:%a %d %b}"
            store.put(moved)
            return d
    return None


def _handle_missed_long(store: Store, today: date, status: str) -> list[str]:
    y = store.run_on(today - timedelta(days=1))
    if not y or y.kind != "long" or y.status != "missed" or y.adjusted.startswith("made up"):
        return []
    today_run = store.run_on(today)
    if status == "poor" or (today_run and today_run.kind not in MOVABLE_ONTO | {"b2b"}) or y.phase == "race":
        return [f"Missed yesterday's {y.title} - not making it up today."]
    if today_run:
        store.remove(today_run)
    made_up = _copy(y, today)
    made_up.status = "planned"
    made_up.original_title = y.title
    made_up.adjusted = "made up from yesterday"
    if status == "low":
        made_up = w.long_run(today, y.minutes * 0.75)
        made_up.week, made_up.phase = y.week, y.phase
        made_up.original_title, made_up.adjusted = y.title, "made up from yesterday, shortened"
    store.put(made_up)
    y.adjusted = "made up on " + today.isoformat()
    return [f"Missed yesterday's {y.title} - moved to today as {made_up.title}."]


def _deload(store: Store, start: date, end: date) -> list[str]:
    changed = []
    for s in store.between(start, end):
        if s.status != "planned" or s.adjusted or s.phase == "race":
            continue
        if s.kind == "quality":
            _replace(store, s, w.easy(s.date, min(s.minutes, 45)), "3 low days in a row: deload")
            changed.append(f"{s.date:%a} {s.title} -> easy")
        elif s.kind in ("long", "b2b"):
            new = w.long_run(s.date, s.minutes * 0.8) if s.kind == "long" else w.back_to_back(s.date, s.minutes * 0.8)
            new.description = s.description
            _replace(store, s, new, "3 low days in a row: deload")
            changed.append(f"{s.date:%a} {s.title} -> {new.title}")
    if not changed:
        return []
    return ["3 low days in a row - lightening the next few days: " + "; ".join(changed) + "."]
