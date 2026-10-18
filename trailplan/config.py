"""Load season.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ConfigError(ValueError):
    pass


def _parse_hm(value: str) -> int:
    """'6:30' -> 390 (min:sec paces or h:mm times); 'H:MM:SS' -> minutes, e.g. '0:44:00' -> 44."""
    parts = [int(x) for x in value.strip().split(":")]
    if len(parts) == 3:
        return round(parts[0] * 60 + parts[1] + parts[2] / 60)
    return parts[0] * 60 + parts[1]


@dataclass
class Race:
    name: str
    date: date
    distance_km: float
    elevation_m: float = 0
    priority: str = "A"
    goal_min: int | None = None
    easy_pace_s: int = 390
    start: str = "06:00"
    checkpoints: list[dict] = field(default_factory=list)
    road: bool = False  # road race: pace-based training, no climbing targets

    @property
    def effort_km(self) -> float:
        # Common trail rule of thumb: 100 m of climbing costs about 1 km of flat running.
        return self.distance_km + self.elevation_m / 100

    @property
    def est_min(self) -> int:
        if self.goal_min:
            return self.goal_min
        if self.road:  # racing on the road is well under easy pace
            return round(self.distance_km * self.easy_pace_s * 0.82 / 60)
        return round(self.effort_km * self.easy_pace_s / 60)

    @property
    def ultra(self) -> bool:
        return self.distance_km > 42.2 or self.est_min >= 300

    @property
    def vert_per_hour(self) -> float:
        return self.elevation_m / (self.est_min / 60) if self.est_min else 0


@dataclass
class Athlete:
    weekly_min: int | None = None
    long_run_min: int | None = None
    days: int = 5
    long_day: int = 5
    strength: bool = True
    hr_targets: bool = True
    fuel_every_min: int = 30
    easy_pace_s: int = 390
    carbs_per_hour: int = 75
    fluid_ml_per_hour: int = 600


@dataclass
class Config:
    athlete: Athlete
    races: list[Race] = field(default_factory=list)


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"{path} not found")
    raw = tomllib.loads(path.read_text())
    a = raw.get("athlete", {})
    days = int(a.get("days_per_week", 5))
    if not 3 <= days <= 6:
        raise ConfigError("days_per_week must be between 3 and 6")
    long_day = str(a.get("long_run_day", "sat")).lower()[:3]
    if long_day not in DAYS:
        raise ConfigError(f"long_run_day must be one of {', '.join(DAYS)}")
    pace = _parse_hm(str(a.get("easy_pace", "6:30")))
    athlete = Athlete(
        weekly_min=round(float(a["weekly_hours"]) * 60) if "weekly_hours" in a else None,
        long_run_min=int(a["long_run_min"]) if "long_run_min" in a else None,
        days=days,
        long_day=DAYS.index(long_day),
        strength=bool(a.get("strength", True)),
        hr_targets=bool(a.get("hr_targets", True)),
        fuel_every_min=int(a.get("fuel_every_min", 30)),
        carbs_per_hour=int(a.get("carbs_per_hour", 75)),
        fluid_ml_per_hour=int(a.get("fluid_ml_per_hour", 600)),
        easy_pace_s=pace,
    )
    races = []
    for r in raw.get("race", []):
        try:
            races.append(
                Race(
                    name=r["name"],
                    date=r["date"] if isinstance(r["date"], date) else date.fromisoformat(r["date"]),
                    distance_km=float(r["distance_km"]),
                    elevation_m=float(r.get("elevation_m", 0)),
                    priority=str(r.get("priority", "A")).upper(),
                    goal_min=_parse_hm(r["goal_time"]) if r.get("goal_time") else None,
                    easy_pace_s=pace,
                    start=str(r.get("start", "06:00")),
                    road=str(r.get("type", "trail")).lower() == "road",
                    checkpoints=list(r.get("checkpoint", [])),
                )
            )
        except KeyError as e:
            raise ConfigError(f"race is missing {e}") from e
    if not races:
        raise ConfigError("add at least one [[race]]")
    return Config(athlete, sorted(races, key=lambda r: r.date))
