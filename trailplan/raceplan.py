"""Race-day plan: split times per checkpoint, cutoff buffers, and fuel to carry.

Pacing model: each leg costs "effort-km" (km + climbing/100), and you slow down
as the race goes on (up to SLOWDOWN slower by the finish). Leg times are scaled
so the total matches your goal time (or the plan's estimate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Race
from .model import fmt_min

SLOWDOWN = 0.30  # pace at the finish is this much slower than at the start
GEL_CARBS = 25  # grams of carbs per gel / equivalent


@dataclass
class Checkpoint:
    name: str
    km: float  # cumulative
    gain_m: float | None = None  # cumulative climbing to here (None = not published, spread by distance)
    aid: str = "full"  # full | water | none
    cutoff: str = ""  # "HH:MM" clock time on race day ("+1 HH:MM" for the next day), or empty


@dataclass
class Leg:
    to: Checkpoint
    km: float
    gain_m: float
    minutes: float
    arrive: datetime
    buffer_min: float | None
    carbs_g: int
    fluid_ml: int


@dataclass
class RacePlan:
    race: Race
    start: datetime
    target_min: int
    legs: list[Leg] = field(default_factory=list)
    carry: list[tuple[str, int, int]] = field(default_factory=list)  # (from, gels, fluid ml) per full-aid section


def _fill_gain(cps: list[Checkpoint]) -> None:
    """Fill unpublished cumulative climbing by interpolating on distance between known points."""
    known = [(0.0, 0.0)] + [(c.km, c.gain_m) for c in cps if c.gain_m is not None]
    for c in cps:
        if c.gain_m is None:
            (k0, g0), (k1, g1) = max((p for p in known if p[0] <= c.km), key=lambda p: p[0]), min(
                (p for p in known if p[0] >= c.km), key=lambda p: p[0])
            c.gain_m = g0 + (g1 - g0) * ((c.km - k0) / (k1 - k0) if k1 > k0 else 0)


def _cutoff_dt(race_start: datetime, cutoff: str) -> datetime | None:
    if not cutoff:
        return None
    day = 0
    if cutoff.startswith("+"):
        day, cutoff = int(cutoff[1]), cutoff[2:].strip()
    hh, mm = (int(x) for x in cutoff.split(":"))
    return race_start.replace(hour=hh, minute=mm) + timedelta(days=day)


def build_race_plan(
    race: Race,
    checkpoints: list[Checkpoint],
    start: str = "06:00",
    target_min: int | None = None,
    carbs_per_hour: int = 75,
    fluid_ml_per_hour: int = 600,
) -> RacePlan:
    if not checkpoints:
        raise ValueError(f"No checkpoints for {race.name} - add [[race.checkpoint]] entries in season.toml")
    cps = sorted(checkpoints, key=lambda c: c.km)
    if cps[-1].km < race.distance_km - 0.5:
        cps.append(Checkpoint("Finish", race.distance_km, race.elevation_m, "full"))
    if cps[-1].gain_m is None:
        cps[-1].gain_m = race.elevation_m
    _fill_gain(cps)
    hh, mm = (int(x) for x in start.split(":"))
    t0 = datetime.combine(race.date, datetime.min.time()).replace(hour=hh, minute=mm)
    target = target_min or race.est_min
    total_km, total_effort = cps[-1].km, cps[-1].km + max(cps[-1].gain_m, race.elevation_m) / 100

    # Effort per leg, weighted by how far into the race it is (fatigue).
    raw, prev_km, prev_gain = [], 0.0, 0.0
    for c in cps:
        km, gain = c.km - prev_km, max(c.gain_m - prev_gain, 0)
        effort = km + gain / 100
        mid = (prev_km + c.km) / 2 / total_km
        raw.append((c, km, gain, effort * (1 + SLOWDOWN * mid)))
        prev_km, prev_gain = c.km, max(c.gain_m, prev_gain)
    scale = target / sum(r[3] for r in raw) if total_effort else 0

    plan = RacePlan(race, t0, target)
    clock = t0
    for c, km, gain, weighted in raw:
        minutes = weighted * scale
        clock += timedelta(minutes=minutes)
        cut = _cutoff_dt(t0, c.cutoff)
        plan.legs.append(Leg(
            c, km, gain, minutes, clock,
            (cut - clock).total_seconds() / 60 if cut else None,
            round(carbs_per_hour * minutes / 60),
            round(fluid_ml_per_hour * minutes / 60, -1),
        ))

    # What to carry out of the start and each full aid station: food until the next
    # full aid, fluid until the next place you can refill (full or water-only).
    section_from, gels, fluid, max_fluid = "Start", 0.0, 0.0, 0.0
    for leg in plan.legs:
        last = leg is plan.legs[-1]
        gels += leg.carbs_g / GEL_CARBS
        fluid += leg.fluid_ml
        if leg.to.aid in ("full", "water") or last:
            max_fluid, fluid = max(max_fluid, fluid), 0.0
        if leg.to.aid == "full" or last:
            plan.carry.append((section_from, math.ceil(gels - 0.15), int(round(max_fluid, -2))))
            section_from, gels, max_fluid = leg.to.name, 0.0, 0.0
    return plan


def _clock(t: datetime, start: datetime) -> str:
    days = (t.date() - start.date()).days
    return (f"+{days} " if days else "") + t.strftime("%H:%M")


def format_race_plan(plan: RacePlan, carbs_per_hour: int, fluid_ml_per_hour: int) -> str:
    r = plan.race
    lines = [
        f"{r.name} - {r.date:%a %d %b %Y}, start {plan.start:%H:%M}, target {fmt_min(plan.target_min)}",
        f"Fuel: ~{carbs_per_hour} g carbs/hr (about {carbs_per_hour / GEL_CARBS:.1f} gels/hr), "
        f"~{fluid_ml_per_hour} ml fluid/hr - more in heat.",
        "",
        f"{'Checkpoint':<34}{'km':>6}{'climb':>7}{'leg':>7}{'arrive':>10}{'cutoff':>10}{'buffer':>9}{'carbs':>7}",
    ]
    for leg in plan.legs:
        cut = leg.to.cutoff or "-"
        if leg.buffer_min is None:
            buf = "-"
        else:
            buf = fmt_min(abs(leg.buffer_min)) if leg.buffer_min >= 0 else "LATE " + fmt_min(-leg.buffer_min)
        aid = {"full": "", "water": " (water)", "none": " (no aid)"}.get(leg.to.aid, "")
        lines.append(
            f"{(leg.to.name + aid)[:33]:<34}{leg.to.km:>6.1f}{round(leg.gain_m):>6}m{fmt_min(leg.minutes):>7}"
            f"{_clock(leg.arrive, plan.start):>10}{cut:>10}{buf:>9}{leg.carbs_g:>6}g"
        )
    tight = [leg for leg in plan.legs if leg.buffer_min is not None and leg.buffer_min < 30]
    lines.append("")
    if tight:
        lines.append("⚠ Tight on cutoffs at: " + ", ".join(
            f"{leg.to.name} ({'late' if leg.buffer_min < 0 else fmt_min(leg.buffer_min) + ' spare'})" for leg in tight))
    lines.append("Carry out of each full aid station (gels or equivalent, fluid):")
    for frm, gels, fluid in plan.carry:
        lines.append(f"  from {frm:<28} {gels:>2} gels  {fluid:>5} ml")
    lines.append("")
    lines.append("On the watch: Trail Run > settings > Alerts > Add New > Custom, recurring - "
                 f"'EAT' every {round(60 * GEL_CARBS / carbs_per_hour)} min, 'DRINK' every 15-20 min.")
    return "\n".join(lines)
