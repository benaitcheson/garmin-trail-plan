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
from .config import Athlete
from .model import Session, Step
from .store import Store
from .workouts import FUEL_KINDS, add_fuel

log = logging.getLogger(__name__)

TOKENSTORE = os.environ.get("GARMINTOKENS", "~/.garminconnect")
NAME_PREFIX = "TP "
MANUAL_PREFIX = "manual:"  # store.garmin keys for workouts pushed by hand, e.g. road-race
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
END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
PACE_ZONE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 1}
PACE_TOLERANCE = 0.03  # target window: +/-3% around the pace
# Garmin personal-record type ids -> distance keys, and race prediction keys.
PR_TYPES = {3: "5k", 4: "10k", 5: "hm", 6: "m"}
PREDICTION_KEYS = {"5k": "time5K", "10k": "time10K", "hm": "timeHalfMarathon", "m": "timeMarathon"}
RACE_KM = {"5k": 5.0, "10k": 10.0, "hm": 21.0975, "m": 42.195}
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

def to_payload(
    s: Session, hr_targets: bool = True, fuel_every_min: int = 0, paces: dict[str, float] | None = None,
    pace_tolerance: float = PACE_TOLERANCE,
) -> dict[str, Any]:
    name = (NAME_PREFIX + s.title)[:60]
    description = s.description + (f" [Adjusted: {s.adjusted}]" if s.adjusted else "")
    run_steps = s.steps
    if s.kind in FUEL_KINDS and fuel_every_min > 0:
        run_steps = add_fuel(s.steps, fuel_every_min)
        description += f" Watch prompts you to fuel every {fuel_every_min} min." 
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
                segmentOrder=1, sportType=RUNNING, workoutSteps=_steps(run_steps, counter, hr_targets, paces or {}, pace_tolerance))],
        )
    return workout.to_dict()


def _steps(
    steps: list[Step], counter, hr_targets: bool, paces: dict[str, float], tolerance: float = PACE_TOLERANCE
) -> list:
    out = []
    for st in steps:
        order = next(counter)
        if st.kind == "repeat":
            out.append(create_repeat_group(st.reps, _steps(st.steps, counter, hr_targets, paces, tolerance), order))
            continue
        if st.meters is not None:
            end, end_value = END_DISTANCE, st.meters
        elif st.seconds is not None:
            end, end_value = END_TIME, st.seconds
        else:
            end, end_value = END_LAP, None
        target = dict(targetType=NO_TARGET)
        if st.pace and st.pace in paces:
            secs_per_km = paces[st.pace]
            # Garmin pace targets are speeds in m/s; the slower pace is the lower value.
            target = dict(
                targetType=PACE_ZONE,
                targetValueOne=1000 / (secs_per_km * (1 + tolerance)),
                targetValueTwo=1000 / (secs_per_km * (1 - tolerance)),
            )
        elif hr_targets and st.zone is not None:
            target = dict(targetType=HR_ZONE, zoneNumber=st.zone)
        note = st.note
        if st.pace and st.pace in paces:
            note = f"{note} ({fmt_pace(paces[st.pace])}/km)"
        out.append(ExecutableStep(
            stepOrder=order,
            stepType=STEP_TYPES[st.kind],
            endCondition=end,
            endConditionValue=end_value,
            description=note or None,
            **target,
        ))
    return out


def fmt_pace(secs_per_km: float) -> str:
    s = round(secs_per_km)
    return f"{s // 60}:{s % 60:02d}"


def training_paces(fitness: dict, marathon_goal_min: int | None = None) -> dict[str, float]:
    """Pace (sec/km) for each road pace role, from Garmin's current race predictions."""
    pred = fitness.get("predictions") or {}
    per_km = {k: pred[k] / RACE_KM[k] for k in RACE_KM if pred.get(k)}
    if not per_km:
        return {}
    paces = {}
    if "5k" in per_km:
        paces["5k"] = per_km["5k"]
    if "10k" in per_km:
        paces["10k"] = per_km["10k"]
    if "hm" in per_km:
        paces["hm"] = per_km["hm"]
    if "10k" in per_km and "hm" in per_km:
        paces["thr"] = (per_km["10k"] + per_km["hm"]) / 2
    # Marathon pace: your goal if you've set one (and it's not slower than predicted), else the prediction.
    mp = [v for v in (marathon_goal_min and marathon_goal_min * 60 / RACE_KM["m"], per_km.get("m")) if v]
    if mp:
        paces["mp"] = min(mp)
    return paces


def fetch_fitness(g: Garmin) -> dict:
    """Current road PBs (seconds, date) and Garmin race predictions (seconds)."""
    out: dict = {"prs": {}, "predictions": {}}
    try:
        for p in g.get_personal_record() or []:
            key = PR_TYPES.get(p.get("typeId"))
            if key and p.get("value"):
                when = (p.get("prStartTimeGmtFormatted") or p.get("actStartDateTimeInGMTFormatted") or "")[:10]
                out["prs"][key] = {"secs": round(p["value"]), "date": when}
    except Exception as e:
        log.debug("personal records: %s", e)
    try:
        pred = g.get_race_predictions() or {}
        if isinstance(pred, list):
            pred = pred[0] if pred else {}
        out["predictions"] = {k: pred[v] for k, v in PREDICTION_KEYS.items() if pred.get(v)}
    except Exception as e:
        log.debug("race predictions: %s", e)
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


def push_window(
    g: Garmin | None, store: Store, today: date, athlete: Athlete, dry_run: bool,
    paces: dict[str, float] | None = None,
) -> list[str]:
    """Make Garmin's calendar match the plan for the next WINDOW_DAYS days."""
    msgs = []
    paces = paces or {}

    def version(s: Session) -> str:
        # Watch-side settings (and current paces) change the uploaded workout too.
        used = sorted({st.pace for st, _ in _all_steps(s.steps) if st.pace})
        pace_sig = ",".join(f"{p}{round(paces[p])}" for p in used if p in paces)
        return f"{s.content_hash()}:hr{int(athlete.hr_targets)}:fuel{athlete.fuel_every_min}:{pace_sig}"

    end = today + timedelta(days=WINDOW_DAYS - 1)
    wanted = {s.id: s for s in store.between(today, end) if s.uploadable}

    for sid, rec in list(store.garmin.items()):
        rec_date = date.fromisoformat(rec["date"])
        if sid.startswith(MANUAL_PREFIX):
            # Workouts pushed by hand (race pacing) aren't part of the plan: only tidy up old ones.
            if rec_date < today - timedelta(days=KEEP_PAST_DAYS) and not dry_run:
                _remove_uploaded(g, rec)
                del store.garmin[sid]
            continue
        stale_future = rec_date >= today and (sid not in wanted or version(wanted[sid]) != rec["hash"])
        too_old = rec_date < today - timedelta(days=KEEP_PAST_DAYS)
        if stale_future or too_old:
            if stale_future:
                msgs.append(f"Removing old version: {rec['name']} ({rec_date:%a %d %b})")
            if not dry_run:
                _remove_uploaded(g, rec)
                del store.garmin[sid]

    for sid, s in wanted.items():
        rec = store.garmin.get(sid)
        if rec and rec["hash"] == version(s):
            continue
        msgs.append(f"Sending to watch: {s.date:%a %d %b}  {s.title}")
        if dry_run:
            continue
        resp = g.upload_workout(to_payload(s, athlete.hr_targets, athlete.fuel_every_min, paces))
        workout_id = resp["workoutId"]
        sched = g.schedule_workout(workout_id, s.date.isoformat()) or {}
        store.garmin[sid] = {
            "workout_id": workout_id,
            "schedule_id": sched.get("workoutScheduleId") or sched.get("id"),
            "hash": version(s),
            "date": s.date.isoformat(),
            "name": s.title,
        }
        store.save()  # save as we go so an interrupted sync can resume cleanly
        time.sleep(0.5)
    return msgs


def _all_steps(steps: list[Step], n: int = 1) -> list[tuple[Step, int]]:
    out = []
    for st in steps:
        out += _all_steps(st.steps, n * st.reps) if st.kind == "repeat" else [(st, n)]
    return out


def push_manual(g: Garmin, store: Store, key: str, s: Session, paces: dict[str, float], tolerance: float) -> dict:
    """Upload and schedule a one-off workout, replacing an earlier one with the same key."""
    sid = MANUAL_PREFIX + key
    if sid in store.garmin:
        _remove_uploaded(g, store.garmin.pop(sid))
    resp = g.upload_workout(to_payload(s, hr_targets=False, paces=paces, pace_tolerance=tolerance))
    sched = g.schedule_workout(resp["workoutId"], s.date.isoformat()) or {}
    store.garmin[sid] = {
        "workout_id": resp["workoutId"],
        "schedule_id": sched.get("workoutScheduleId") or sched.get("id"),
        "hash": s.content_hash(),
        "date": s.date.isoformat(),
        "name": s.title,
    }
    store.save()
    return store.garmin[sid]


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
        out.append(Activity(
            day,
            _dig(a, "activityType", "typeKey") or "",
            (a.get("duration") or 0) / 60,
            avg_hr=a.get("averageHR"),
            zone_secs=[a.get(f"hrTimeInZone_{i}") or 0.0 for i in range(1, 6)],
            elevation=a.get("elevationGain") or 0,
            load=a.get("activityTrainingLoad") or 0,
        ))
    return out


def zone_warning(g: Garmin) -> str | None:
    """Warn if HR zones are max-HR based while Garmin knows your lactate threshold."""
    try:
        zones = g.get_heart_rate_zones() or []
    except Exception as e:
        log.debug("hr zones: %s", e)
        return None
    z = next((z for z in zones if z.get("sport") == "RUNNING"), None) or next(
        (z for z in zones if z.get("sport") == "DEFAULT"), None)
    lthr = z and z.get("lactateThresholdHeartRateUsed")
    if not z or not lthr or z.get("trainingMethod") != "HR_MAX":
        return None
    return (
        f"Your HR zones are based on max HR ({z.get('maxHeartRateUsed')}), so Zone 2 is "
        f"{z['zone2Floor']}-{z['zone3Floor'] - 1} bpm. Garmin has your lactate threshold at {lthr} bpm: "
        "in Garmin Connect set Heart Rate Zones to '% Lactate Threshold' so workout targets fit you."
    )


def detect_fitness(g: Garmin, today: date) -> tuple[int, int]:
    """(weekly running minutes over the last 4 weeks, longest run in the last 6 weeks)."""
    acts = [a for a in fetch_activities(g, today - timedelta(days=42), today) if a.is_run]
    last4 = sum(a.minutes for a in acts if a.date > today - timedelta(days=28))
    by_day: dict[date, float] = {}
    for a in acts:
        by_day[a.date] = by_day.get(a.date, 0) + a.minutes
    return round(last4 / 4), round(max(by_day.values(), default=0))
