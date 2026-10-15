"""Season planner: races + current fitness -> dated sessions.

Each race gets a segment: post-race recovery (after the previous race), then
base -> build -> peak with a deload every 4th week, then taper and race week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import workouts as w
from .config import Athlete, Config, Race
from .model import Session, fmt_min

# Run days as (offset from long-run day, role). Q = quality, E = easy, L = long.
LAYOUTS = {
    3: [(-4, "Q1"), (-2, "E"), (0, "L")],
    4: [(-4, "Q1"), (-2, "E"), (0, "L"), (1, "E")],
    5: [(-4, "Q1"), (-3, "E"), (-2, "Q2"), (0, "L"), (1, "E")],
    6: [(2, "E"), (-4, "Q1"), (-3, "E"), (-2, "Q2"), (0, "L"), (1, "E")],
}
# Rough share of weekly time the long run should take, by run days per week.
LONG_SHARE = {3: 0.42, 4: 0.35, 5: 0.30, 6: 0.27}
# Estimated race minutes -> peak long run minutes.
PEAK_LONG = [(60, 90), (120, 150), (240, 195), (360, 240), (600, 300), (1200, 330)]
# Post-race recovery weeks, as a share of the previous peak load.
RECOVERY_FACTORS = {1: [0.5], 2: [0.4, 0.7], 3: [0.3, 0.55, 0.75]}
# Max week-on-week growth in actual run time (deload weeks excluded).
MAX_RAMP = 1.12
# Long run never more than this share of the week's (ramp-capped) run time.
LONG_MAX_SHARE = 0.55
# Taper weeks before race week, as a share of peak load.
TAPER_FACTORS = {1: [], 2: [0.7], 3: [0.8, 0.6]}


@dataclass
class Week:
    number: int
    monday: date
    phase: str
    deload: bool = False
    race: str = ""
    sessions: list[Session] = field(default_factory=list)

    @property
    def run_minutes(self) -> int:
        return sum(s.minutes for s in self.sessions if s.is_run)

    @property
    def label(self) -> str:
        return "deload" if self.deload else self.phase


@dataclass
class SeasonPlan:
    weeks: list[Week]
    notes: list[str]

    @property
    def sessions(self) -> list[Session]:
        return [s for wk in self.weeks for s in wk.sessions]


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def interp(x: float, pts: list[tuple[float, float]]) -> float:
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def recovery_weeks(race: Race) -> int:
    weeks = int(clamp(round(race.est_min / 60 / 5), 1, 3))
    return max(1, weeks - 1) if race.priority == "B" else weeks


def rest_days_after(race: Race) -> int:
    return int(clamp(round(race.est_min / 60 / 2), 2, 7))


def taper_weeks(race: Race) -> int:
    if race.priority == "B":
        return 2  # one lighter week, then race week
    return 3 if race.est_min >= 600 else 2


def run_days(W: float, max_days: int) -> int:
    """Fewer run days while weekly volume is low, so every run is still worth doing."""
    days = 3 if W < 180 else 4 if W < 270 else 5 if W < 360 else 6
    return min(days, max_days)


def split_phases(pre: int, after_race: bool) -> list[str]:
    if pre <= 0:
        return []
    peak = min(4, max(1, round(pre * 0.25)))
    if after_race and pre <= 10:
        build, base = pre - peak, 0
    else:
        build = min(8, max(0, round(pre * 0.4)))
        base = pre - peak - build
    return ["base"] * base + ["build"] * build + ["peak"] * peak


def build_season(cfg: Config, today: date, weekly_min: int, long_min: int) -> SeasonPlan:
    a = cfg.athlete
    races = [r for r in cfg.races if r.date > today]
    if not races:
        raise ValueError("No upcoming races in season.toml")

    weeks: list[Week] = []
    notes: list[str] = []
    W0, L0 = float(weekly_min), float(long_min)
    prev: Race | None = None
    prev_W = prev_L = 0.0

    for race in races:
        if prev is None:
            seg_start, first_mon = today, monday(today)
        else:
            seg_start, first_mon = prev.date + timedelta(days=1), monday(prev.date - timedelta(days=3)) + timedelta(weeks=1)
        race_mon = monday(race.date - timedelta(days=3))
        n = (race_mon - first_mon).days // 7 + 1
        if n < 1:
            notes.append(f"{race.name} is too close to {prev.name if prev else 'today'} to plan for - skipped.")
            continue

        taper = taper_weeks(race)
        rec = min(recovery_weeks(prev), max(n - taper - 1, 0)) if prev else 0
        taper = min(taper, n - rec)
        pre = n - rec - taper
        phases = ["recovery"] * rec + split_phases(pre, prev is not None) + ["taper"] * (taper - 1) + ["race"]
        deloads = [pre >= 6 and (j + 1) % 4 == 0 and j < pre - 1 for j in range(pre)]
        nb = pre - sum(deloads)

        ideal_long = interp(race.est_min, PEAK_LONG)
        step = 20 if race.ultra else 15
        L_peak = min(L0, 330) if L0 >= ideal_long else min(ideal_long, L0 + step * max(nb - 1, 0))
        W_target = max(W0, L_peak / LONG_SHARE[a.days])
        W_peak = max(W0, min(W_target, W0 * 1.07 ** max(nb - 1, 0)))
        if L_peak < ideal_long * 0.85:
            notes.append(
                f"{race.name}: long run only builds to {fmt_min(L_peak)} (ideal ~{fmt_min(ideal_long)}) "
                "because of the safe ramp rate from your current fitness."
            )

        k, last_W, last_L = 0, W0, L0
        last_actual = W0
        levels: dict[str, int] = {}
        for i, phase in enumerate(phases):
            mon = first_mon + timedelta(weeks=i)
            deload = False
            if phase == "recovery":
                f = RECOVERY_FACTORS[rec][i]
                W, L = prev_W * f, prev_L * f * 0.7
            elif phase == "taper":
                f = TAPER_FACTORS[taper][i - rec - pre]
                W, L = W_peak * f, L_peak * f
            elif phase == "race":
                W = L = 0
            else:
                deload = deloads[i - rec]
                if deload:
                    W, L = last_W * 0.72, last_L * 0.7
                else:
                    frac = k / (nb - 1) if nb > 1 else 1.0
                    # Weekly time grows by a steady percentage; long run by steady minutes.
                    W, L = W0 * (W_peak / W0) ** frac, L0 + (L_peak - L0) * frac
                    last_W, last_L, k = W, L, k + 1

            level = levels.get(phase, 0)
            if not deload:
                levels[phase] = level + 1
            not_before = seg_start
            if phase == "recovery" and i == 0 and prev:
                not_before = prev.date + timedelta(days=rest_days_after(prev))
            progressive = phase in ("base", "build", "peak") and not deload
            if progressive:
                # Keep the long run in proportion to the week you can actually handle.
                L = min(L, max(L0, LONG_MAX_SHARE * last_actual * MAX_RAMP))
            week = Week(len(weeks) + 1, mon, phase, deload, race.name if phase == "race" else "")
            week.sessions = _week_sessions(
                a, race, mon, phase, deload, W, L, level, first_recovery=(phase == "recovery" and i == 0)
            )
            week.sessions = [
                s for s in week.sessions
                if s.date >= not_before and (phase == "race" or s.date < race.date - timedelta(days=6))
            ]
            if progressive:
                week.sessions = _trim(week.sessions, last_actual * MAX_RAMP)
                last_actual = max(last_actual, week.run_minutes)
            for s in week.sessions:
                s.week, s.phase = week.number, week.label
            weeks.append(week)

        # Carry forward what the segment actually reached, not the targets.
        seg = [wk for wk in weeks if wk.phase not in ("race", "recovery", "taper") and wk.race == "" and wk.monday >= first_mon]
        actual_W = max((wk.run_minutes for wk in seg), default=W_peak)
        actual_L = max((s.minutes for wk in seg for s in wk.sessions if s.kind == "long"), default=L_peak)
        notes.append(
            f"{race.name} ({race.date:%a %d %b %Y}, {race.priority} race): est. {fmt_min(race.est_min)}, "
            f"biggest week {fmt_min(actual_W)}, longest run {fmt_min(actual_L)}."
        )
        prev, prev_W, prev_L = race, actual_W, actual_L
        # Long-run fitness is mostly kept after a B race; rebuild more gently after an A race.
        W0, L0 = actual_W * 0.8, actual_L * (0.75 if race.priority == "B" else 0.6)

    return SeasonPlan(weeks, notes)


def _week_sessions(
    a: Athlete, race: Race, mon: date, phase: str, deload: bool, W: float, L: float, level: int, first_recovery: bool
) -> list[Session]:
    if phase == "race":
        return _race_week(a, race)

    long_date = mon + timedelta(days=a.long_day)
    vph = race.vert_per_hour
    easy_wk = phase == "recovery" or deload
    out: list[Session] = []
    easy_days: list[date] = []

    days = run_days(W, a.days)
    for offset, role in LAYOUTS[days]:
        d = long_date + timedelta(days=offset)
        if first_recovery:
            easy_days.append(d)
        elif role == "L":
            if phase == "peak" and not deload:
                out.append(w.race_sim(d, max(L, 60), vph, race.ultra))
            else:
                out.append(w.long_run(d, max(L, 45), vph))
        elif role == "Q1":
            if easy_wk:
                out.append(w.strides(d, 35))
            elif phase == "base":
                out.append(w.hill_sprints(d, clamp(W * 0.12, 20, 50)))
            elif phase == "build":
                out.append(w.hill_repeats(d, level))
            elif phase == "peak":
                out.append(w.climb_tempo(d, level))
            else:
                out.append(w.sharpener(d))
        elif role == "Q2" and not easy_wk:
            if phase == "base":
                out.append(w.strides(d, 40))
            elif phase == "build":
                out.append(w.fartlek(d, level))
            elif phase == "peak":
                out.append(w.steady(d, level))
            else:
                out.append(w.strides(d, 35))
        elif (
            role == "E" and offset == 1 and race.ultra and days >= 4
            and phase in ("build", "peak") and not deload
        ):
            out.append(w.back_to_back(d, clamp(L * 0.5, 60, 150)))
        else:
            easy_days.append(d)

    if first_recovery:
        out += [w.easy(d, 30) for d in easy_days[-3:]]
    elif easy_days:
        spare = W - sum(s.minutes for s in out)
        each = clamp(spare / len(easy_days), 20 if W < 180 else 30, 90 if race.ultra else 75)
        out += [w.easy(d, each) for d in easy_days]

    if a.strength and not first_recovery:
        offsets = [-3, 2] if phase == "base" and not deload else [-3]
        out += [w.strength(long_date + timedelta(days=o), light=phase == "taper") for o in offsets]

    return sorted(out, key=lambda s: (s.date, s.kind == "strength"))


def _trim(sessions: list[Session], cap: float) -> list[Session]:
    """Shrink easy running (then back-to-backs) until the week fits under cap minutes."""
    def total() -> int:
        return sum(s.minutes for s in sessions if s.is_run)

    sessions = list(sessions)
    while total() > cap:
        easy = sorted((s for s in sessions if s.kind == "easy"), key=lambda s: s.minutes, reverse=True)
        b2b = next((s for s in sessions if s.kind == "b2b"), None)
        excess = total() - cap
        # Always cut at least 5 min: durations round to 5, so a smaller cut could be a no-op.
        cut = max(5, excess)
        if easy and easy[0].minutes > 25:
            s = easy[0]
            sessions[sessions.index(s)] = w.easy(s.date, max(20, s.minutes - cut))
        elif b2b and b2b.minutes > 45:
            sessions[sessions.index(b2b)] = w.back_to_back(b2b.date, max(45, b2b.minutes - cut))
        elif easy and sum(s.is_run for s in sessions) > 3:
            sessions.remove(easy[-1])
        elif b2b:
            sessions[sessions.index(b2b)] = w.easy(b2b.date, 30)
        else:
            break
    return sessions


def _race_week(a: Athlete, race: Race) -> list[Session]:
    out = []
    if a.days >= 4:
        out.append(w.easy(race.date - timedelta(days=5), 40 if race.ultra else 35))
    out.append(w.openers(race.date - timedelta(days=3)))
    out.append(w.shakeout(race.date - timedelta(days=1)))
    out.append(w.race(race.date, race.name, race.est_min))
    return out
