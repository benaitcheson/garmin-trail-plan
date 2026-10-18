from datetime import date, timedelta
from pathlib import Path

import pytest

from trailplan import workouts as w
from trailplan.adapt import Activity, Wellness, adapt_today, reconcile
from trailplan.config import load_config
from trailplan.garmin import to_payload
from trailplan.planner import build_season
from trailplan.store import Store

ROOT = Path(__file__).resolve().parent.parent
MON = date(2027, 3, 8)  # a Monday in the UTA build


@pytest.fixture
def season():
    cfg = load_config(ROOT / "season.toml")
    return build_season(cfg, date(2026, 10, 15), 240, 90)


def make_store(tmp_path, *sessions):
    store = Store(tmp_path / "state.json")
    for s in sessions:
        store.put(s)
    return store


GOOD = Wellness(readiness=70, sleep=80, hrv="BALANCED")
LOW = Wellness(readiness=40, sleep=55, hrv="UNBALANCED")
POOR = Wellness(readiness=15, sleep=30, hrv="LOW")


def test_classify():
    assert GOOD.classify() == "good"
    assert LOW.classify() == "low"
    assert POOR.classify() == "poor"
    assert Wellness(readiness=80, sleep=45).classify() == "low"  # bad sleep alone downgrades
    assert Wellness(readiness=80, sleep=85, hrv="LOW").classify() == "low"
    assert Wellness().classify() == "unknown"


def test_season_sessions_are_well_formed(season):
    sessions = season.sessions
    ids = [s.id for s in sessions]
    assert len(ids) == len(set(ids)), "one run + one strength max per day"
    for race_day in (date(2027, 5, 15), date(2027, 7, 17), date(2028, 2, 5)):
        assert any(s.kind == "race" and s.date == race_day for s in sessions)
        # nothing hard in the 6 days before a race except the planned openers
        for s in sessions:
            if race_day - timedelta(days=6) <= s.date < race_day:
                assert s.kind in ("easy", "quality", "shakeout") and s.phase == "race"


def test_every_session_converts_to_garmin_payload(season):
    for s in season.sessions:
        if s.uploadable:
            payload = to_payload(s)
            assert payload["workoutName"].startswith("TP ")
            assert payload["workoutSegments"][0]["workoutSteps"]


def test_good_day_changes_nothing(tmp_path):
    q = w.hill_repeats(MON + timedelta(days=1), 2)
    store = make_store(tmp_path, q)
    adapt_today(store, q.date, GOOD, trained_today=False)
    assert store.run_on(q.date).title == q.title


def test_low_day_swaps_hard_session_and_moves_it(tmp_path):
    tue = MON + timedelta(days=1)
    q = w.hill_repeats(tue, 2)
    wed = w.easy(tue + timedelta(days=1), 60)
    store = make_store(tmp_path, q, wed, w.long_run(MON + timedelta(days=5), 180))
    msgs = adapt_today(store, tue, LOW, trained_today=False)
    assert store.run_on(tue).kind == "easy"
    assert store.run_on(tue).adjusted
    assert store.run_on(tue + timedelta(days=1)).title == q.title, msgs


def test_low_day_shortens_long_run(tmp_path):
    sat = MON + timedelta(days=5)
    store = make_store(tmp_path, w.long_run(sat, 240))
    adapt_today(store, sat, LOW, trained_today=False)
    assert store.run_on(sat).minutes == 180


def test_poor_day_moves_long_run_to_tomorrow(tmp_path):
    sat = MON + timedelta(days=5)
    store = make_store(tmp_path, w.long_run(sat, 240), w.back_to_back(sat + timedelta(days=1), 120))
    adapt_today(store, sat, POOR, trained_today=False)
    assert store.run_on(sat) is None
    assert store.run_on(sat + timedelta(days=1)).kind == "long"


def test_missed_long_run_is_made_up_today(tmp_path):
    sat = MON + timedelta(days=5)
    store = make_store(tmp_path, w.long_run(sat, 200), w.easy(sat + timedelta(days=1), 60))
    reconcile(store, [], sat, sat)
    assert store.run_on(sat).status == "missed"
    adapt_today(store, sat + timedelta(days=1), GOOD, trained_today=False)
    assert store.run_on(sat + timedelta(days=1)).kind == "long"


def test_reconcile_marks_done_and_partial(tmp_path):
    tue, wed = MON + timedelta(days=1), MON + timedelta(days=2)
    store = make_store(tmp_path, w.easy(tue, 60), w.easy(wed, 60))
    reconcile(store, [Activity(tue, "trail_running", 55), Activity(wed, "running", 20)], tue, wed)
    assert store.run_on(tue).status == "done"
    assert store.run_on(wed).status == "partial"


def test_three_low_days_trigger_deload(tmp_path):
    days = [MON + timedelta(days=i) for i in range(7)]
    store = make_store(tmp_path, w.easy(days[0], 45), w.easy(days[1], 45), w.easy(days[2], 45),
                       w.hill_repeats(days[3], 3), w.long_run(days[5], 200))
    for d in days[:3]:
        adapt_today(store, d, LOW, trained_today=False)
    assert store.run_on(days[3]).kind == "easy"


def test_decision_locked_once_made(tmp_path):
    sat = MON + timedelta(days=5)
    store = make_store(tmp_path, w.long_run(sat, 240))
    adapt_today(store, sat, LOW, trained_today=False)
    adapt_today(store, sat, LOW, trained_today=False)  # second run of the day must not shorten again
    assert store.run_on(sat).minutes == 180


def _flat_steps(steps):
    return [s for s in steps if s.kind != "repeat"]


def test_fuel_prompts_every_30_min_without_changing_duration():
    run = w.long_run(MON, 180)
    fuelled = w.add_fuel(run.steps, 30)
    assert sum(s.est_seconds() for s in fuelled) == sum(s.est_seconds() for s in run.steps)
    # step boundaries (elapsed seconds) where a fuel note appears
    marks, elapsed = [], 0
    for s in fuelled:
        if s.note.startswith(w.FUEL_PREFIX):
            marks.append(elapsed / 60)
        elapsed += s.est_seconds()
    assert marks == [30, 60, 90, 120, 150]
    assert all(s.zone == 2 for s in fuelled if s.kind == "run")


def test_fuel_never_creates_tiny_steps():
    for minutes in range(60, 331, 5):
        for s in w.add_fuel(w.long_run(MON, minutes).steps, 30):
            assert s.seconds is None or s.seconds >= w.MIN_PIECE


def test_fuel_prompts_in_race_sim_and_payload():
    sim = w.race_sim(MON, 240, 300, ultra=False)
    payload = to_payload(sim, fuel_every_min=30)
    notes = [st.get("description", "") for st in payload["workoutSegments"][0]["workoutSteps"]]
    assert sum(n.startswith("FUEL") for n in notes) == 7  # 30..210 min
    assert "fuel every 30 min" in payload["description"]
    # easy runs and quality sessions are left alone
    easy_payload = to_payload(w.easy(MON, 60), fuel_every_min=30)
    assert not any("FUEL" in (st.get("description") or "") for st in easy_payload["workoutSegments"][0]["workoutSteps"])


def test_fuel_off():
    run = w.long_run(MON, 180)
    assert w.add_fuel(run.steps, 0) == run.steps


@pytest.mark.parametrize("weekly_min,long_min", [(100, 57), (240, 90)])
def test_back_to_back_shrinks_before_midweek_easy_goes_short(weekly_min, long_min):
    cfg = load_config(ROOT / "season.toml")
    for wk in build_season(cfg, date(2026, 10, 15), weekly_min, long_min).weeks:
        if any(s.kind == "b2b" and s.minutes > 45 for s in wk.sessions):
            assert all(s.minutes >= 30 for s in wk.sessions if s.kind == "easy"), wk.monday


def _act(day, minutes, zones_min, elev=0):
    return Activity(day, "trail_running", minutes, avg_hr=150, zone_secs=[z * 60 for z in zones_min], elevation=elev)


def test_easy_run_too_hard_is_flagged(tmp_path):
    tue = MON + timedelta(days=1)
    store = make_store(tmp_path, w.easy(tue, 45))
    msgs = reconcile(store, [_act(tue, 45, [2, 18, 20, 5, 0])], tue, tue)
    s = store.run_on(tue)
    assert s.status == "done" and s.actual["zone_min"] == [2, 18, 20, 5, 0]
    assert any("too hard" in f for f in s.actual["flags"]) and any("too hard" in m for m in msgs)


def test_easy_run_in_zone_is_not_flagged(tmp_path):
    tue = MON + timedelta(days=1)
    store = make_store(tmp_path, w.easy(tue, 45))
    reconcile(store, [_act(tue, 45, [5, 35, 5, 0, 0])], tue, tue)
    assert store.run_on(tue).actual["flags"] == []


def test_long_run_short_on_climbing_is_flagged(tmp_path):
    sun = MON + timedelta(days=6)
    run = w.long_run(sun, 180, vert_per_hour=300)
    assert run.target_vert == 900
    store = make_store(tmp_path, run)
    reconcile(store, [_act(sun, 180, [10, 150, 20, 0, 0], elev=300)], sun, sun)
    assert any("climbing" in f for f in store.run_on(sun).actual["flags"])


def test_soft_intervals_are_flagged(tmp_path):
    tue = MON + timedelta(days=1)
    store = make_store(tmp_path, w.hill_repeats(tue, 4))  # 6 x 2 min in Zone 4 = 12 min planned
    reconcile(store, [_act(tue, 50, [10, 25, 13, 2, 0])], tue, tue)
    assert any("Zone 4+" in f for f in store.run_on(tue).actual["flags"])


def test_dashboard_builds(tmp_path, season):
    from trailplan.dashboard import build_data, write_dashboard
    store = make_store(tmp_path, *season.sessions)
    for wk in season.weeks:
        store.weeks[wk.monday.isoformat()] = {"number": wk.number, "phase": wk.label, "race": wk.race}
    cfg = load_config(ROOT / "season.toml")
    data = build_data(store, cfg, date(2026, 10, 20))
    assert data["tiles"]["next_race"] == "UTA 50" and len(data["weeks"]) == len(season.weeks)
    html = write_dashboard(store, cfg, date(2026, 10, 20), tmp_path / "d.html").read_text()
    assert "/*__DATA__*/" not in html and "UTA 50" in html


def test_race_plan_splits_sum_to_target_and_flag_tight_cutoffs():
    from trailplan.config import Race
    from trailplan.raceplan import Checkpoint, build_race_plan, format_race_plan
    race = Race("Test 50", date(2027, 5, 15), 50, 2400)
    cps = [
        Checkpoint("CP1", 10, 500, "full", "09:00"),
        Checkpoint("Water", 20, 900, "water"),
        Checkpoint("CP2", 30, 1500, "full", "10:15"),
        Checkpoint("Finish", 50, 2400, "full", "20:00"),
    ]
    plan = build_race_plan(race, cps, start="06:00", target_min=480)
    assert abs(sum(leg.minutes for leg in plan.legs) - 480) < 0.5
    assert plan.legs[-1].arrive.strftime("%H:%M") == "14:00"
    # later legs are slower per effort-km than early ones (fatigue)
    first, last = plan.legs[0], plan.legs[-1]
    assert last.minutes / (last.km + last.gain_m / 100) > first.minutes / (first.km + first.gain_m / 100)
    assert plan.legs[2].buffer_min < 0  # reaches 30 km at ~10:37 on an 8h pace, after the 10:15 cutoff
    text = format_race_plan(plan, 75, 600)
    assert "LATE" in text and "Tight on cutoffs" in text
    # gels carried from the start cover the start -> CP1 leg; fluid only to the next refill
    assert [c[0] for c in plan.carry] == ["Start", "CP1", "CP2"]


FITNESS = {"predictions": {"5k": 1323, "10k": 2822, "hm": 6425, "m": 14271},
           "prs": {"5k": {"secs": 1251, "date": "2019-09-13"}}}


def test_parse_goal_times():
    from trailplan.config import _parse_hm
    assert _parse_hm("3:50") == 230 and _parse_hm("0:44:00") == 44 and _parse_hm("6:30") == 390


def test_road_block_between_guzzler_and_cradle(season):
    road = [s for s in season.sessions if date(2027, 8, 1) <= s.date < date(2027, 10, 10)]
    titles = [s.title for s in road]
    assert any(t.startswith("Intervals") for t in titles)
    assert any(t.startswith("Threshold") for t in titles)
    assert any("@MP" in t for t in titles)
    assert any(t == "5k Time Trial" for t in titles)
    assert not any(s.kind == "b2b" for s in road), "no back-to-backs in the road block"
    assert max(s.minutes for s in road if s.kind == "long") <= 180
    assert any(s.kind == "race" and s.title == "RACE: Melbourne Marathon" for s in season.sessions)


def test_training_paces_from_predictions():
    from trailplan.garmin import training_paces
    p = training_paces(FITNESS)
    assert round(p["5k"]) == 265 and round(p["10k"]) == 282          # 4:25/km, 4:42/km
    assert p["10k"] < p["thr"] < p["hm"]
    assert round(p["mp"]) == round(14271 / 42.195)
    assert round(training_paces(FITNESS, marathon_goal_min=230)["mp"]) == 327  # goal 3:50 is faster -> 5:27/km
    assert training_paces({}) == {}


def test_pace_targets_in_payload():
    from trailplan.garmin import training_paces
    s = w.road_intervals(MON, 0)  # 5 x 1000 m at 5k pace
    payload = to_payload(s, paces=training_paces(FITNESS))
    rep = next(st for st in payload["workoutSegments"][0]["workoutSteps"] if st["type"] == "RepeatGroupDTO"
               and st["workoutSteps"][0].get("endCondition", {}).get("conditionTypeKey") == "distance")
    work = rep["workoutSteps"][0]
    assert work["endConditionValue"] == 1000 and work["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert work["targetValueOne"] < 1000 / 265 < work["targetValueTwo"]  # slower bound first, in m/s
    assert "4:25/km" in work["description"]
    # without predictions the same step falls back to its HR zone
    fallback = to_payload(s)
    rep = next(st for st in fallback["workoutSegments"][0]["workoutSteps"] if st["type"] == "RepeatGroupDTO"
               and st["workoutSteps"][0].get("endCondition", {}).get("conditionTypeKey") == "distance")
    assert rep["workoutSteps"][0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"


def test_new_pb_detected():
    from trailplan.cli import _new_pbs
    new = {"prs": {"5k": {"secs": 1225, "date": "2027-08-21"}}}
    assert _new_pbs(FITNESS, new) == ["New 5 km PB: 20:25 (was 20:51). Nice work!"]
    assert _new_pbs(FITNESS, FITNESS) == []


def test_road_race_goal_and_distance_parsing():
    from trailplan.roadrace import parse_distance, parse_goal
    assert parse_distance("5km") == 5 and parse_distance("10k") == 10 and parse_distance("half") == 21.0975
    assert parse_distance("marathon") == 42.195 and parse_distance("8") == 8
    assert parse_goal("19:55", 5) == 1195
    assert parse_goal("44:30", 10) == 2670
    assert parse_goal("1:45", 21.0975) == 6300 and parse_goal("3:50", 42.195) == 13800
    assert parse_goal("95:00", 21.0975) == 5700 and parse_goal("1:45:30", 21.0975) == 6330
    with pytest.raises(ValueError):
        parse_goal("9:55", 5)  # 1:59/km - a typo, not a goal
    with pytest.raises(ValueError):
        parse_distance("parkrun")


def test_road_race_workout_adds_up():
    from trailplan.roadrace import build_race_workout
    s = build_race_workout(5, 1195, MON)
    assert [st.meters for st in s.steps] == [1000] * 5
    assert abs(sum(st.est_seconds() for st in s.steps) - 1195) < 1
    m = build_race_workout(42.195, 13800, MON)
    assert sum(st.meters for st in m.steps) == 42195 and len(m.steps) == 9
    assert sum("Gel" in st.note for st in m.steps) == 7
    assert not any("Gel" in st.note for st in s.steps)
    payload = to_payload(m, hr_targets=False, paces={"race": 13800 / 42.195}, pace_tolerance=0.03)
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert step["endCondition"]["conditionTypeKey"] == "distance" and step["endConditionValue"] == 5000
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone" and "5:27/km" in step["description"]


def test_sync_leaves_manual_race_workouts_alone(tmp_path):
    from trailplan.config import Athlete
    from trailplan.garmin import push_window
    store = make_store(tmp_path)
    store.garmin["manual:race:2026-10-25:5"] = {"workout_id": 1, "schedule_id": 2, "hash": "x",
                                                "date": "2026-10-25", "name": "RACE 5k 19:55"}
    msgs = push_window(None, store, date(2026, 10, 20), Athlete(), dry_run=True)
    assert "manual:race:2026-10-25:5" in store.garmin and not any("Removing" in m for m in msgs)
