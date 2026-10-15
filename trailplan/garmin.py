"""Everything that talks to Garmin Connect."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from getpass import getpass
from typing import Any

from garminconnect import Garmin, exercises
from garminconnect.workout import (
    ExecutableStep,
    RunningWorkout,
    StrengthWorkout,
    WorkoutSegment,
    create_repeat_group,
    create_strength_set,
    create_strength_timed_set,
)

from .adapt import Activity, Wellness
from .model import Session, Step
from .store import Store

log = logging.getLogger(__name__)

TOKENSTORE = os.environ.get("GARMINTOKENS", "~/.garminconnect")
NAME_PREFIX = "TP "
WINDOW_DAYS = 7  # how many days ahead to keep on the watch
KEEP_PAST_DAYS = 7  # delete our workout templates once this old

STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "run": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1}
RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
STRENGTH = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}


# ---------------------------------------------------------------- login

def connect() -> Garmin:
    try:
        g = Garmin()
        g.login(TOKENSTORE)
        return g
    except Exception as e:  # no saved tokens, or they expired
        log.debug("token login failed: %s", e)
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass("Garmin password: ")
    g = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    g.login(TOKENSTORE)
    return g


# ---------------------------------------------------------------- workouts

def to_payload(s: Session, hr_targets: bool = True) -> dict[str, Any]:
    name = (NAME_PREFIX + s.title)[:60]
    description = s.description + (f" [Adjusted: {s.adjusted}]" if s.adjusted else "")
    if s.kind == "strength":
        order, steps = 1, []
        for ex in s.exercises:
            ref = exercises.resolve(ex.name)
            if ref is None:
                raise ValueError(f"Unknown Garmin exercise: {ex.name}")
            if ex.seconds:
                steps.append(create_strength_timed_set(
                    ref["category"], order, ex.sets, ex.seconds, ex.rest, exercise_name=ref["exercise"],
                    description=ex.note or None))
            else:
                steps.append(create_strength_set(
                    ref["category"], order, ex.sets, ex.reps or 10, ex.rest, exercise_name=ref["exercise"],
                    description=ex.note or None))
            order += 3
        workout = StrengthWorkout(
            workoutName=name, description=description, estimatedDurationInSecs=s.minutes * 60,
            workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=STRENGTH, workoutSteps=steps)],
        )
    else:
        counter = iter(range(1, 1000))
        workout = RunningWorkout(
            workoutName=name, description=description, estimatedDurationInSecs=s.minutes * 60,
            workoutSegments=[WorkoutSegment(
                segmentOrder=1, sportType=RUNNING, workoutSteps=_steps(s.steps, counter, hr_targets))],
        )
    return workout.to_dict()


def _steps(steps: list[Step], counter, hr_targets: bool) -> list:
    out = []
    for st in steps:
        order = next(counter)
        if st.kind == "repeat":
            out.append(create_repeat_group(st.reps, _steps(st.steps, counter, hr_targets), order))
            continue
        zoned = hr_targets and st.zone is not None
        out.append(ExecutableStep(
            stepOrder=order,
            stepType=STEP_TYPES[st.kind],
            endCondition=END_TIME if st.seconds is not None else END_LAP,
            endConditionValue=st.seconds,
            targetType=HR_ZONE if zoned else NO_TARGET,
            zoneNumber=st.zone if zoned else None,
            description=st.note or None,
        ))
    return out


def _remove_uploaded(g: Garmin, rec: dict) -> None:
    if rec.get("schedule_id"):
        try:
            g.unschedule_workout(rec["schedule_id"])
        except Exception as e:
            log.debug("unschedule %s: %s", rec["schedule_id"], e)
    try:
        g.delete_workout(rec["workout_id"])
    except Exception as e:
        log.debug("delete %s: %s", rec["workout_id"], e)


def push_window(g: Garmin | None, store: Store, today: date, hr_targets: bool, dry_run: bool) -> list[str]:
    """Make Garmin's calendar match the plan for the next WINDOW_DAYS days."""
    msgs = []
    end = today + timedelta(days=WINDOW_DAYS - 1)
    wanted = {s.id: s for s in store.between(today, end) if s.uploadable}

    for sid, rec in list(store.garmin.items()):
        rec_date = date.fromisoformat(rec["date"])
        stale_future = rec_date >= today and (sid not in wanted or wanted[sid].content_hash() != rec["hash"])
        too_old = rec_date < today - timedelta(days=KEEP_PAST_DAYS)
        if stale_future or too_old:
            if stale_future:
                msgs.append(f"Removing old version: {rec['name']} ({rec_date:%a %d %b})")
            if not dry_run:
                _remove_uploaded(g, rec)
                del store.garmin[sid]

    for sid, s in wanted.items():
        rec = store.garmin.get(sid)
        if rec and rec["hash"] == s.content_hash():
            continue
        msgs.append(f"Sending to watch: {s.date:%a %d %b}  {s.title}")
        if dry_run:
            continue
        resp = g.upload_workout(to_payload(s, hr_targets))
        workout_id = resp["workoutId"]
        sched = g.schedule_workout(workout_id, s.date.isoformat()) or {}
        store.garmin[sid] = {
            "workout_id": workout_id,
            "schedule_id": sched.get("workoutScheduleId") or sched.get("id"),
            "hash": s.content_hash(),
            "date": s.date.isoformat(),
            "name": s.title,
        }
        store.save()  # save as we go so an interrupted sync can resume cleanly
        time.sleep(0.5)
    return msgs


def remove_all(g: Garmin, store: Store, by_name: bool) -> int:
    count = 0
    for rec in store.garmin.values():
        _remove_uploaded(g, rec)
        count += 1
    store.garmin.clear()
    if by_name:
        start = 0
        while batch := g.get_workouts(start, 100):
            for wk in batch:
                if str(wk.get("workoutName", "")).startswith(NAME_PREFIX):
                    g.delete_workout(wk["workoutId"])
                    count += 1
            if len(batch) < 100:
                break
            start += 100
    return count


# ---------------------------------------------------------------- reading data

def _dig(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def fetch_wellness(g: Garmin, day: date) -> Wellness:
    iso = day.isoformat()
    wl = Wellness()
    try:
        tr = g.get_morning_training_readiness(iso) or {}
        wl.readiness = tr.get("score")
        wl.level = tr.get("level")
        wl.sleep = tr.get("sleepScore")
    except Exception as e:
        log.debug("training readiness: %s", e)
    if wl.sleep is None:
        try:
            wl.sleep = _dig(g.get_sleep_data(iso), "dailySleepDTO", "sleepScores", "overall", "value")
        except Exception as e:
            log.debug("sleep: %s", e)
    try:
        wl.hrv = _dig(g.get_hrv_data(iso), "hrvSummary", "status")
    except Exception as e:
        log.debug("hrv: %s", e)
    return wl


def fetch_activities(g: Garmin, start: date, end: date) -> list[Activity]:
    out = []
    for a in g.get_activities_by_date(start.isoformat(), end.isoformat()) or []:
        started = a.get("startTimeLocal") or ""
        try:
            day = datetime.fromisoformat(started.replace(" ", "T")).date()
        except ValueError:
            continue
        out.append(Activity(day, _dig(a, "activityType", "typeKey") or "", (a.get("duration") or 0) / 60))
    return out


def detect_fitness(g: Garmin, today: date) -> tuple[int, int]:
    """(weekly running minutes over the last 4 weeks, longest run in the last 6 weeks)."""
    acts = [a for a in fetch_activities(g, today - timedelta(days=42), today) if a.is_run]
    last4 = sum(a.minutes for a in acts if a.date > today - timedelta(days=28))
    by_day: dict[date, float] = {}
    for a in acts:
        by_day[a.date] = by_day.get(a.date, 0) + a.minutes
    return round(last4 / 4), round(max(by_day.values(), default=0))
