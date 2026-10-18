"""trailplan command line."""

from __future__ import annotations

import argparse
import json
import logging
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from .adapt import adapt_today, reconcile
from .config import ConfigError, load_config
from .model import fmt_min
from .planner import build_season, monday
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
STATUS_MARK = {"done": "✓", "partial": "~", "missed": "✗", "planned": " ", "dropped": "-"}
PLIST = Path.home() / "Library/LaunchAgents/com.trailplan.sync.plist"
DASHBOARD = ROOT / "dashboard.html"


def main() -> None:
    p = argparse.ArgumentParser(prog="trailplan", description="Adaptive trail running plan for Garmin")
    p.add_argument("--config", type=Path, default=ROOT / "season.toml")
    p.add_argument("--state", type=Path, default=ROOT / "state.json")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="(Re)build the season plan from season.toml")
    sp.add_argument("--weekly-hours", type=float, help="override current weekly hours")
    sp.add_argument("--long-run", type=int, help="override longest recent run (minutes)")

    sp = sub.add_parser("show", help="Show the plan")
    sp.add_argument("--season", action="store_true", help="one line per week for the whole season")
    sp.add_argument("--weeks", type=int, default=2, help="how many weeks of detail (default 2)")
    sp.add_argument("--from", dest="start", type=date.fromisoformat, help="start date YYYY-MM-DD")

    sp = sub.add_parser("sync", help="Daily: check sleep/readiness, adapt, update the watch")
    sp.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    sp.add_argument("--force", action="store_true", help="re-run today's adaptation even if already done")
    sp.add_argument("--notify", action="store_true", help="macOS notification with today's workout")

    sub.add_parser("login", help="Log in to Garmin Connect and save tokens")
    sp = sub.add_parser("race-plan", help="Race-day splits, cutoff buffers and fuel plan")
    sp.add_argument("race", nargs="?", help="race name (default: next race)")
    sp.add_argument("--goal", help="goal time H:MM (default: season.toml goal_time or estimate)")
    sp = sub.add_parser("dashboard", help="Build the progress dashboard (dashboard.html) and open it")
    sp.add_argument("--no-open", action="store_true", help="just write the file")
    sp = sub.add_parser("install-schedule", help="Run sync automatically every morning (macOS)")
    sp.add_argument("--times", default="06:30,08:00,10:00", help="comma-separated HH:MM (default 06:30,08:00,10:00)")
    sub.add_parser("uninstall-schedule", help="Stop the automatic morning sync")
    sp = sub.add_parser("reset", help="Delete every workout this app put on Garmin")
    sp.add_argument("--by-name", action="store_true", help="also delete any Garmin workout named 'TP ...'")
    sp = sub.add_parser("debug", help="Print raw Garmin wellness data for a day")
    sp.add_argument("day", nargs="?", type=date.fromisoformat, default=date.today())

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        {
            "plan": cmd_plan, "show": cmd_show, "sync": cmd_sync, "login": cmd_login,
            "install-schedule": cmd_install, "uninstall-schedule": cmd_uninstall,
            "reset": cmd_reset, "debug": cmd_debug, "dashboard": cmd_dashboard, "race-plan": cmd_race_plan,
        }[args.cmd](args)
    except (ConfigError, ValueError) as e:
        sys.exit(f"Error: {e}")


def _store(args) -> Store:
    store = Store.load(args.state)
    if not store.has_plan:
        sys.exit("No plan yet - run `trailplan plan` first.")
    return store


def _connect():
    from .garmin import connect

    if sys.stdin.isatty() or (os.environ.get("GARMIN_EMAIL") and os.environ.get("GARMIN_PASSWORD")):
        return connect()
    # No terminal to type a password into (scheduler, or an embedded shell): saved tokens only.
    from garminconnect import Garmin

    from .garmin import TOKENSTORE
    try:
        g = Garmin()
        g.login(TOKENSTORE)
        return g
    except Exception as e:
        sys.exit(
            "Not logged in to Garmin yet (or the saved login expired).\n"
            "Open the Terminal app and run:\n\n"
            f"    cd {ROOT} && uv run trailplan login\n\n"
            f"It will ask for your Garmin email, password and MFA code once. ({type(e).__name__})"
        )


# ---------------------------------------------------------------- commands

def cmd_login(args) -> None:
    g = _connect()
    print(f"Logged in as {g.get_full_name()}. Tokens saved - you won't need your password again for a while.")


def cmd_plan(args) -> None:
    cfg = load_config(args.config)
    today = date.today()
    weekly = round(args.weekly_hours * 60) if args.weekly_hours else cfg.athlete.weekly_min
    long_run = args.long_run or cfg.athlete.long_run_min
    if weekly is None or long_run is None:
        from .garmin import detect_fitness

        print("Reading your last 6 weeks of runs from Garmin to gauge current fitness...")
        d_weekly, d_long = detect_fitness(_connect(), today)
        weekly, long_run = weekly or d_weekly, long_run or d_long
        print(f"  ~{fmt_min(weekly)} running per week, longest run {fmt_min(long_run)}.")
        if weekly < 60:
            print("  That's under an hour a week - starting from a 1h/week, 45 min long-run baseline.")
    weekly, long_run = max(weekly, 60), max(long_run, 45)

    season = build_season(cfg, today, weekly, long_run)
    store = Store.load(args.state)
    # Today is already underway (it may have been adapted or synced), so a rebuild
    # only replaces tomorrow onwards - unless nothing has touched today yet.
    today_started = today.isoformat() in store.decisions or any(s.date == today for s in store.sessions.values())
    keep_until = today if today_started else today - timedelta(days=1)
    store.sessions = {k: s for k, s in store.sessions.items() if s.date <= keep_until}
    for s in season.sessions:
        if s.date > keep_until:
            store.put(s)
    store.weeks = {wk.monday.isoformat(): {"number": wk.number, "phase": wk.label, "race": wk.race} for wk in season.weeks}
    store.notes = season.notes
    store.save()

    print(f"\nPlan built: {len(season.weeks)} weeks, {sum(1 for s in season.sessions if s.is_run)} runs.\n")
    for n in season.notes:
        print(" •", n)
    print()
    _print_season(store)
    print("\nNext: `trailplan show` for detail, then `trailplan sync` to send the next 7 days to your watch.")


def cmd_show(args) -> None:
    store = _store(args)
    if args.season:
        _print_season(store)
        return
    start = monday(args.start or date.today())
    for i in range(args.weeks):
        _print_week(store, start + timedelta(weeks=i))


def cmd_sync(args) -> None:
    from .garmin import fetch_activities, fetch_fitness, fetch_wellness, push_window, training_paces, zone_warning

    cfg = load_config(args.config)
    store = _store(args)
    today = date.today()
    try:
        g = _connect()
    except Exception as e:
        msg = f"Garmin login failed ({e}). Run `uv run trailplan login` in a terminal."
        _notify(args, "Trail plan: login needed", msg)
        sys.exit(msg)

    since = date.fromisoformat(store.reconciled_until) + timedelta(days=1) if store.reconciled_until else today - timedelta(days=7)
    since = max(since, today - timedelta(days=14))
    acts = fetch_activities(g, min(since, today), today)
    msgs = reconcile(store, acts, since, today - timedelta(days=1))
    trained_today = any(a.date == today and (a.is_run or a.is_strength) for a in acts)
    wellness = fetch_wellness(g, today)
    msgs += adapt_today(store, today, wellness, trained_today, force=args.force)
    fitness = fetch_fitness(g)
    msgs += _new_pbs(store.fitness, fitness)
    if fitness["prs"] or fitness["predictions"]:
        store.fitness = {**fitness, "date": today.isoformat()}
    marathon_goal = next(
        (r.goal_min for r in cfg.races if r.road and r.distance_km >= 42 and r.date >= today and r.goal_min), None)
    paces = training_paces(store.fitness, marathon_goal)
    msgs += push_window(None if args.dry_run else g, store, today, cfg.athlete, args.dry_run, paces)
    if warning := zone_warning(g):
        msgs.append(warning)
    if not args.dry_run:
        store.save()
        from .dashboard import write_dashboard

        write_dashboard(store, cfg, today, DASHBOARD)

    print(("[dry run] " if args.dry_run else "") + f"Sync {today:%a %d %b}")
    for m in msgs:
        print(" •", m)
    print()
    _print_days(store, today, today + timedelta(days=6))

    run = store.run_on(today)
    headline = run.title if run else "Rest day"
    detail = (run.adjusted if run and run.adjusted else wellness.summary())
    _notify(args, f"Today: {headline}", detail)


PB_NAMES = {"5k": "5 km", "10k": "10 km", "hm": "half marathon", "m": "marathon"}


def fmt_secs(secs: float) -> str:
    s = round(secs)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _new_pbs(old: dict, new: dict) -> list[str]:
    msgs = []
    for key, pr in (new.get("prs") or {}).items():
        before = (old.get("prs") or {}).get(key)
        if before and pr["secs"] < before["secs"]:
            msgs.append(f"New {PB_NAMES[key]} PB: {fmt_secs(pr['secs'])} (was {fmt_secs(before['secs'])}). Nice work!")
    return msgs


def cmd_race_plan(args) -> None:
    from .config import _parse_hm
    from .raceplan import Checkpoint, build_race_plan, format_race_plan, road_checkpoints

    cfg = load_config(args.config)
    upcoming = [r for r in cfg.races if r.date >= date.today()]
    if args.race:
        matches = [r for r in cfg.races if args.race.lower() in r.name.lower()]
        if not matches:
            sys.exit(f"No race matching '{args.race}'. Races: {', '.join(r.name for r in cfg.races)}")
        race = matches[0]
    elif upcoming:
        race = upcoming[0]
    else:
        sys.exit("No upcoming races in season.toml")
    a = cfg.athlete
    checkpoints = [Checkpoint(**c) for c in race.checkpoints]
    if not checkpoints and race.road:
        checkpoints = road_checkpoints(race)
    plan = build_race_plan(
        race,
        checkpoints,
        start=race.start,
        target_min=_parse_hm(args.goal) if args.goal else None,
        carbs_per_hour=a.carbs_per_hour,
        fluid_ml_per_hour=a.fluid_ml_per_hour,
        slowdown=0.02 if race.road else 0.30,  # aim for near-even splits on the road
    )
    print(format_race_plan(plan, a.carbs_per_hour, a.fluid_ml_per_hour))


def cmd_dashboard(args) -> None:
    from .dashboard import write_dashboard

    path = write_dashboard(_store(args), load_config(args.config), date.today(), DASHBOARD)
    print(f"Dashboard: {path}")
    if not args.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(path)])


def cmd_install(args) -> None:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found on PATH")
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    times = []
    for t in args.times.split(","):
        h, m = t.strip().split(":")
        times.append({"Hour": int(h), "Minute": int(m)})
    plist = {
        "Label": "com.trailplan.sync",
        "ProgramArguments": [uv, "run", "--project", str(ROOT), "trailplan", "sync", "--notify"],
        "StartCalendarInterval": times,
        "StandardOutPath": str(logs / "sync.log"),
        "StandardErrorPath": str(logs / "sync.log"),
        "WorkingDirectory": str(ROOT),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    print(f"Installed: sync runs daily at {args.times} (and on wake if the Mac was asleep).")
    print(f"Log: {logs / 'sync.log'}")


def cmd_uninstall(args) -> None:
    if PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink()
    print("Automatic sync removed.")


def cmd_reset(args) -> None:
    from .garmin import remove_all

    store = Store.load(args.state)
    if input("Delete all trailplan workouts from Garmin Connect? [y/N] ").lower() != "y":
        return
    n = remove_all(_connect(), store, args.by_name)
    store.save()
    print(f"Removed {n} workouts.")


def cmd_debug(args) -> None:
    g = _connect()
    iso = args.day.isoformat()
    for label, fn in [
        ("training readiness", lambda: g.get_training_readiness(iso)),
        ("sleep", lambda: g.get_sleep_data(iso).get("dailySleepDTO")),
        ("hrv", lambda: (g.get_hrv_data(iso) or {}).get("hrvSummary")),
        ("activities", lambda: g.get_activities_by_date(iso, iso)),
    ]:
        try:
            print(f"--- {label}\n{json.dumps(fn(), indent=1, default=str)[:4000]}")
        except Exception as e:
            print(f"--- {label}: error {e}")


# ---------------------------------------------------------------- output

def _notify(args, title: str, body: str) -> None:
    if not getattr(args, "notify", False) or sys.platform != "darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'], capture_output=True)


def _print_days(store: Store, start: date, end: date) -> None:
    for s in store.between(start, end):
        mark = STATUS_MARK.get(s.status, " ")
        mins = "" if s.kind == "race" else fmt_min(s.minutes)
        extra = f"   ← {s.adjusted}" if s.adjusted else ""
        print(f"  {mark} {s.date:%a %d %b}  {s.title:<32} {mins:>6}{extra}")


def _print_week(store: Store, mon: date) -> None:
    info = store.weeks.get(mon.isoformat(), {})
    sessions = store.between(mon, mon + timedelta(days=6))
    run_min = sum(s.minutes for s in sessions if s.is_run)
    phase = info.get("phase", "-")
    race = f" · {info['race']}" if info.get("race") else ""
    print(f"\nWeek {info.get('number', '?')} · {phase}{race} · {mon:%d %b} – {mon + timedelta(days=6):%d %b} · {fmt_min(run_min)} running")
    if not sessions:
        print("  (nothing planned)")
    _print_days(store, mon, mon + timedelta(days=6))


def _print_season(store: Store) -> None:
    print(f"{'Wk':>3}  {'Week of':<9} {'Phase':<9} {'Run time':>8}  {'Long':>5}  Key session")
    for iso, info in sorted(store.weeks.items()):
        mon = date.fromisoformat(iso)
        sessions = store.between(mon, mon + timedelta(days=6))
        run_min = sum(s.minutes for s in sessions if s.is_run)
        longs = [s for s in sessions if s.kind == "long"]
        key = info["race"] and f"RACE: {info['race']}" or next((s.title for s in sessions if s.kind == "quality"), "")
        long_txt = fmt_min(longs[0].minutes) if longs else ""
        print(f"{info['number']:>3}  {mon:%d %b %y} {info['phase']:<9} {fmt_min(run_min):>8}  {long_txt:>5}  {key}")


if __name__ == "__main__":
    main()
