"""Race-day pacing workouts: `road-race 5km --goal 19:55`.

Builds an even-paced workout split into chunks (1 km for short races, 5 km for
longer ones), each with a pace window around goal pace, and schedules it on
your Garmin calendar so it's waiting on the watch on race day.
"""

from __future__ import annotations

import re
from datetime import date

from .model import Session, Step, fmt_min

DISTANCES = {
    "5k": 5.0, "5km": 5.0, "10k": 10.0, "10km": 10.0, "15k": 15.0, "15km": 15.0,
    "half": 21.0975, "hm": 21.0975, "half-marathon": 21.0975, "21k": 21.0975, "21.1": 21.0975,
    "marathon": 42.195, "m": 42.195, "full": 42.195, "42k": 42.195, "42.2": 42.195,
}
FUEL_EVERY_MIN = 30  # gel + water about this often in races over an hour


def parse_distance(text: str) -> float:
    key = text.strip().lower()
    if key in DISTANCES:
        return DISTANCES[key]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|km)?", key)
    if not m:
        raise ValueError(f"Unknown distance '{text}'. Try 5km, 10k, half, marathon or a number of km.")
    return float(m.group(1))


def parse_goal(text: str, km: float) -> int:
    """Goal time in seconds. 'H:MM:SS' always; two parts read as M:SS unless it's
    a half/marathon and the first number is small (e.g. '1:45', '3:50' = H:MM)."""
    parts = [int(p) for p in text.strip().split(":")]
    if len(parts) == 3:
        secs = parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        a, b = parts
        secs = a * 3600 + b * 60 if km >= 21 and a <= 9 else a * 60 + b
    else:
        raise ValueError(f"Goal '{text}' should look like 19:55, 1:45 or 1:45:30.")
    pace = secs / km
    if not 150 <= pace <= 720:
        raise ValueError(f"Goal {text} for {km:g} km is {fmt_pace(pace)}/km - that doesn't look right.")
    return secs


def fmt_pace(secs_per_km: float) -> str:
    s = round(secs_per_km)
    return f"{s // 60}:{s % 60:02d}"


def fmt_time(secs: float) -> str:
    s = round(secs)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def label(km: float) -> str:
    return {5.0: "5k", 10.0: "10k", 21.0975: "Half", 42.195: "Marathon"}.get(km, f"{km:g}k")


def build_race_workout(km: float, goal_secs: int, day: date, split_km: float | None = None) -> Session:
    """Even-paced chunks with a running 'where you should be' note. Steps use pace role 'race'."""
    pace = goal_secs / km
    split = split_km or (1.0 if km <= 10 else 5.0)
    marks, at = [], 0.0
    while at + split < km - 0.05:
        at = round(at + split, 4)
        marks.append(at)
    marks.append(km)

    # Races over an hour: a gel every FUEL_EVERY_MIN minutes, placed at the km it falls on.
    gel_km = [t / pace for t in range(FUEL_EVERY_MIN * 60, int(goal_secs) - 600, FUEL_EVERY_MIN * 60)] \
        if goal_secs > 3600 else []
    steps, prev = [], 0.0
    for i, end in enumerate(marks):
        length = end - prev
        due = end * pace
        note = f"to {end:g} km - be there at {fmt_time(due)}"
        if i == len(marks) - 1:
            note = f"Finish - goal {fmt_time(goal_secs)}. Empty the tank."
        elif i == len(marks) - 2 and km <= 10:
            note = f"Last {km - prev:g} km - hold form. " + note
        gels = [g for g in gel_km if prev <= g < end]
        if gels:
            note = "Gel + water at " + " & ".join(f"{g:.1f} km" for g in gels) + ". " + note
        steps.append(Step("run", None, None, note, estimate=length * pace, pace="race", meters=round(length * 1000)))
        prev = end

    name = label(km)
    return Session(
        day,
        "quality",
        f"RACE {name} {fmt_time(goal_secs)}",
        f"Race pacing: {fmt_pace(pace)}/km for {km:g} km = {fmt_time(goal_secs)}. Each step ends at a distance; "
        "press LAP to move on early if your watch and the course markers disagree.",
        steps,
    )


def splits_table(s: Session, goal_secs: int, km: float) -> str:
    pace = goal_secs / km
    lines = [f"{s.title}  -  {fmt_pace(pace)}/km", "", f"{'Step':>4}  {'to km':>7}  {'arrive':>8}  note"]
    done = 0.0
    for i, st in enumerate(s.steps, 1):
        done += st.meters / 1000
        lines.append(f"{i:>4}  {done:>7.2f}  {fmt_time(done * pace):>8}  {st.note}")
    return "\n".join(lines)
