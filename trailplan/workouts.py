"""Builders for each kind of training session.

Intensity is set by Garmin heart-rate zone (1-5) rather than pace, because pace
means little on trails. Zones come from the watch's own HR zone settings.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from .model import Exercise, Session, Step, fmt_min

M = 60  # seconds per minute

EASY_NOTE = "Conversational. Hike anything steep to stay in zone."
FUEL_NOTE = "Practise race fuelling (~60-90 g carbs/hr) and carry race kit."


FUEL_PREFIX = "FUEL: gel + drink. "
FUEL_KINDS = {"long", "b2b"}
MIN_PIECE = 5 * M  # never create a step shorter than this when inserting fuel prompts


def add_fuel(steps: list[Step], every_min: int) -> list[Step]:
    """Split timed steps so a new step starts every `every_min` minutes with a fuel note.

    The watch beeps and shows the step note at each step change, which makes it
    a fuelling reminder. Total duration and zones are unchanged.
    """
    if every_min <= 0:
        return steps
    every = every_min * M
    out: list[Step] = []
    elapsed, next_mark, pending = 0.0, float(every), False
    for st in steps:
        if st.kind == "repeat" or st.seconds is None:
            out.append(st)
            elapsed += st.est_seconds()
            continue
        start, end = elapsed, elapsed + st.seconds
        note = FUEL_PREFIX + st.note if pending else st.note
        pending, piece_start = False, start
        while next_mark < end:
            if next_mark - piece_start < MIN_PIECE:  # mark right at the start: prompt now
                note = note if note.startswith(FUEL_PREFIX) else FUEL_PREFIX + note
            elif end - next_mark < MIN_PIECE:  # mark right at the end: prompt on the next step
                pending = True
            else:
                out.append(replace(st, seconds=next_mark - piece_start, note=note))
                piece_start, note = next_mark, FUEL_PREFIX + st.note
            next_mark += every
        out.append(replace(st, seconds=end - piece_start, note=note))
        elapsed = end
    return out


def r5(x: float) -> int:
    return max(5, int(5 * round(x / 5)))


def _warmup(minutes: float = 10) -> Step:
    return Step("warmup", minutes * M, None, "Ease in")


def _cooldown(minutes: float = 10) -> Step:
    return Step("cooldown", minutes * M, None, "Easy jog / walk")


def _strides(reps: int = 6) -> Step:
    return Step(
        "repeat",
        reps=reps,
        steps=[
            Step("run", 20, None, "Stride: fast, relaxed, tall"),
            Step("recovery", 60, None, "Walk / easy jog"),
        ],
    )


def easy(day: date, minutes: int, title: str = "Easy Run") -> Session:
    minutes = r5(minutes)
    steps = [Step("run", minutes * M, 2, EASY_NOTE)] if minutes <= 25 else [
        _warmup(10),
        Step("run", (minutes - 10) * M, 2, EASY_NOTE),
    ]
    return Session(day, "easy", f"{title} {fmt_min(minutes)}", "Easy aerobic running, ideally on trails.", steps)


def recovery(day: date, minutes: int = 30) -> Session:
    return Session(
        day,
        "recovery",
        f"Recovery Run {fmt_min(minutes)}",
        "Very easy. Walking breaks are fine. Skip it if you feel flat.",
        [Step("run", minutes * M, 1, "Very easy - Zone 1")],
    )


def strides(day: date, minutes: int) -> Session:
    minutes = r5(minutes)
    return Session(
        day,
        "strides",
        f"Easy + Strides {fmt_min(minutes + 8)}",
        "Easy run finishing with 6 relaxed strides on flat or slight downhill.",
        [_warmup(10), Step("run", max(minutes - 10, 5) * M, 2, EASY_NOTE), _strides(6)],
    )


def hill_sprints(day: date, minutes: int) -> Session:
    minutes = r5(minutes)
    return Session(
        day,
        "quality",
        "Easy + Hill Sprints",
        "Builds power and running economy with almost no fatigue. Use a steep hill.",
        [
            _warmup(10),
            Step("run", max(minutes - 10, 10) * M, 2, EASY_NOTE),
            Step(
                "repeat",
                reps=8,
                steps=[
                    Step("run", 12, None, "Steep hill sprint - fast, powerful, tall"),
                    Step("recovery", None, None, "Walk back down. Press LAP at the bottom", estimate=75),
                ],
            ),
            _cooldown(5),
        ],
    )


HILL_PROGRESSION = [(5, 60), (6, 60), (6, 90), (7, 90), (6, 120), (8, 90), (7, 120), (10, 90)]


def hill_repeats(day: date, level: int) -> Session:
    reps, secs = HILL_PROGRESSION[min(level, len(HILL_PROGRESSION) - 1)]
    label = f"{secs // 60}min" if secs % 60 == 0 else f"{secs}s"
    return Session(
        day,
        "quality",
        f"Hill Repeats {reps}x{label}",
        "Hard uphill efforts on a 6-12% grade. Short steps, drive the arms.",
        [
            _warmup(15),
            Step(
                "repeat",
                reps=reps,
                steps=[
                    Step("run", secs, 4, "Hard uphill - Zone 4"),
                    Step("recovery", None, None, "Jog/walk back down. Press LAP at the bottom", estimate=secs * 1.3),
                ],
            ),
            _cooldown(10),
        ],
    )


CLIMB_PROGRESSION = [(3, 8), (3, 10), (3, 12), (2, 20), (3, 15), (2, 25)]


def climb_tempo(day: date, level: int) -> Session:
    reps, mins = CLIMB_PROGRESSION[min(level, len(CLIMB_PROGRESSION) - 1)]
    return Session(
        day,
        "quality",
        f"Climb Tempo {reps}x{mins}min",
        "Sustained comfortably-hard climbing. Power-hike the steep pitches, keep effort steady.",
        [
            _warmup(15),
            Step(
                "repeat",
                reps=reps,
                steps=[
                    Step("run", mins * M, 3, "Long climb or rolling hills - Zone 3"),
                    Step("recovery", 3 * M, None, "Easy jog"),
                ],
            ),
            _cooldown(10),
        ],
    )


def fartlek(day: date, level: int) -> Session:
    reps = min(5 + level, 10)
    return Session(
        day,
        "quality",
        f"Trail Fartlek {reps}x2min",
        "Rolling trail. Push the 2-min surges, float the recoveries.",
        [
            _warmup(10),
            Step("repeat", reps=reps, steps=[Step("run", 2 * M, 3, "Surge - Zone 3"), Step("recovery", 2 * M, 2, "Float")]),
            _cooldown(10),
        ],
    )


def steady(day: date, level: int) -> Session:
    mins = min(25 + 5 * level, 45)
    return Session(
        day,
        "quality",
        f"Steady Hills {mins}min",
        "Continuous Zone 3 on hilly terrain - your ultra 'cruise' effort.",
        [_warmup(15), Step("run", mins * M, 3, "Steady - Zone 3"), _cooldown(10)],
    )


def sharpener(day: date) -> Session:
    return Session(
        day,
        "quality",
        "Sharpener 4x3min",
        "Keeps the legs sharp while volume drops.",
        [
            _warmup(15),
            Step("repeat", reps=4, steps=[Step("run", 3 * M, 4, "Zone 4"), Step("recovery", 2 * M, None, "Easy jog")]),
            _cooldown(10),
        ],
    )


def openers(day: date) -> Session:
    return Session(
        day,
        "quality",
        "Race Openers",
        "Short and snappy. You should finish feeling fresh.",
        [
            _warmup(15),
            Step("repeat", reps=4, steps=[Step("run", 60, 4, "Race effort+"), Step("recovery", 60, None, "Easy")]),
            _cooldown(10),
        ],
    )


def shakeout(day: date) -> Session:
    return Session(
        day,
        "shakeout",
        "Shakeout 20min",
        "Loosen up. Check your kit and lay it out tonight.",
        [Step("run", 20 * M, 1, "Very easy"), _strides(4)],
    )


def _vert_target(minutes: int, vert_per_hour: float) -> int:
    return int(round(vert_per_hour * minutes / 60, -1)) if vert_per_hour >= 50 else 0


def _vert_note(minutes: int, vert_per_hour: float) -> str:
    target = _vert_target(minutes, vert_per_hour)
    return f" Aim for ~{target} m of climbing." if target else ""


def long_run(day: date, minutes: int, vert_per_hour: float = 0) -> Session:
    minutes = r5(minutes)
    return Session(
        day,
        "long",
        f"Long Run {fmt_min(minutes)}",
        "Time on feet, not pace. Hilly trail route." + _vert_note(minutes, vert_per_hour) + " " + FUEL_NOTE,
        [_warmup(15), Step("run", (minutes - 15) * M, 2, EASY_NOTE)],
        target_vert=_vert_target(minutes, vert_per_hour),
    )


def race_sim(day: date, minutes: int, vert_per_hour: float, ultra: bool) -> Session:
    minutes = r5(minutes)
    desc = "Race rehearsal: race shoes, pack, food and drinks." + _vert_note(minutes, vert_per_hour)
    if ultra:
        steps = [
            _warmup(15),
            Step("run", (minutes - 15) * M, 2, "Ultra race effort: run the flats, hike the steeps"),
        ]
    else:
        block = r5(minutes * 0.3)
        first = minutes - 15 - block - 20
        steps = [
            _warmup(15),
            Step("run", first * M, 2, EASY_NOTE),
            Step("run", block * M, 3, "Race effort - Zone 3"),
            Step("run", 20 * M, 2, "Ease back"),
        ]
    return Session(
        day, "long", f"Race-Sim Long Run {fmt_min(minutes)}", desc, steps,
        target_vert=_vert_target(minutes, vert_per_hour),
    )


def back_to_back(day: date, minutes: int) -> Session:
    minutes = r5(minutes)
    return Session(
        day,
        "b2b",
        f"Back-to-Back {fmt_min(minutes)}",
        "Second long day on tired legs - the key ultra adaptation. Easy, hike the climbs.",
        [Step("run", minutes * M, 2, "Easy on tired legs")],
    )


# ---------------------------------------------------------------- road (PB) sessions
# Pace targets are roles ("5k", "thr", "mp"...) turned into real paces at upload time
# from Garmin's latest race predictions, so they track your fitness. The HR zone is
# the fallback if no prediction is available.

INTERVAL_PROGRESSION = [(5, 1000), (6, 1000), (5, 1200), (4, 1600), (6, 1000), (3, 2000)]
THRESHOLD_PROGRESSION = [(3, 8), (3, 10), (2, 15), (4, 8), (2, 20)]


def road_intervals(day: date, level: int) -> Session:
    reps, metres = INTERVAL_PROGRESSION[min(level, len(INTERVAL_PROGRESSION) - 1)]
    label = f"{metres // 1000}km" if metres % 1000 == 0 else f"{metres}m"
    return Session(
        day,
        "quality",
        f"Intervals {reps}x{label}",
        "5k-pace repeats on a flat road or track. Even splits - the last rep should match the first.",
        [
            _warmup(15),
            _strides(4),
            Step(
                "repeat",
                reps=reps,
                steps=[
                    Step("run", None, 5, "5k pace", estimate=metres * 0.26, pace="5k", meters=metres),
                    Step("recovery", 120, None, "Easy jog"),
                ],
            ),
            _cooldown(10),
        ],
    )


def road_threshold(day: date, level: int) -> Session:
    reps, mins = THRESHOLD_PROGRESSION[min(level, len(THRESHOLD_PROGRESSION) - 1)]
    return Session(
        day,
        "quality",
        f"Threshold {reps}x{mins}min",
        "Comfortably hard, controlled - you could hold it for about an hour. Flat route.",
        [
            _warmup(15),
            Step(
                "repeat",
                reps=reps,
                steps=[Step("run", mins * M, 4, "Threshold pace", pace="thr"), Step("recovery", 2 * M, None, "Easy jog")],
            ),
            _cooldown(10),
        ],
    )


def road_long(day: date, minutes: int, race_pace_min: int = 0) -> Session:
    """Easy long run, optionally finishing with a block at marathon pace."""
    minutes = r5(minutes)
    block = min(r5(race_pace_min), minutes - 30) if race_pace_min else 0
    steps = [_warmup(15), Step("run", (minutes - 15 - block) * M, 2, "Easy, relaxed")]
    title = f"Long Run {fmt_min(minutes)}"
    desc = "Flat-ish road or smooth trail. " + FUEL_NOTE
    if block:
        steps.append(Step("run", block * M, 3, "Marathon pace - smooth and controlled", pace="mp"))
        title = f"Long Run {fmt_min(minutes)} ({block}min @MP)"
        desc = f"Finish with {block} min at marathon pace. Practise your race-day fuelling. " + FUEL_NOTE
    return Session(day, "long", title, desc, steps)


def time_trial(day: date, km: float) -> Session:
    label = f"{km:g}k"
    return Session(
        day,
        "quality",
        f"{label} Time Trial",
        f"All-out {label} - parkrun is perfect. Start controlled, race the last third. A PB attempt.",
        [
            _warmup(15),
            _strides(4),
            Step("run", None, 5, f"{label} race effort", estimate=km * 1000 * 0.26,
                 pace="5k" if km <= 5 else "10k", meters=km * 1000),
            _cooldown(10),
        ],
    )


def strength(day: date, light: bool = False) -> Session:
    sets = 2 if light else 3
    exercises = [
        Exercise("Step-up", sets, reps=20, rest=45, note="10 each leg, high box"),
        Exercise("Single-leg Romanian Deadlift with Dumbbell", sets, reps=16, rest=45, note="8 each leg"),
        Exercise("Walking Lunge", sets, reps=20, rest=45),
        Exercise("Single-leg Standing Calf Raise", sets, reps=30, rest=30, note="15 each leg, slow lowering"),
        Exercise("Single-leg Hip Raise", sets, reps=20, rest=30, note="10 each leg"),
        Exercise("Plank", sets, seconds=45, rest=30),
        Exercise("Side Plank", sets, seconds=30, rest=30, note="Alternate sides"),
    ]
    return Session(
        day,
        "strength",
        "Trail Strength" + (" (light)" if light else ""),
        "Climbing and descending strength. Bodyweight is fine; add dumbbells when easy.",
        exercises=exercises,
    )


def race(day: date, name: str, est_min: int) -> Session:
    return Session(day, "race", f"RACE: {name}", f"Estimated {fmt_min(est_min)}. Go get it.")
