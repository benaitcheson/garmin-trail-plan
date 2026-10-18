# trailplan

An adaptive trail-running plan for a Garmin watch (built for an Enduro 3).

It builds a whole season around your races (`season.toml`), then every morning:

1. Checks what you actually ran yesterday (done / partial / missed).
2. Reads **Training Readiness, sleep score and HRV status** from Garmin Connect.
3. Adjusts today and the next few days.
4. Keeps the next 7 days on your Garmin calendar as structured workouts, so they show up on the watch. Trail sessions use heart-rate zone targets and road sessions use pace targets.

| Morning status | What happens |
|---|---|
| **Good** (readiness ≥ 50, sleep ≥ 60) | As planned |
| **Low** (readiness 25–49, sleep 40–59, or HRV low) | Hard session → easy run; the hard session moves to a free easy day later in the week, or is dropped. Long run –25% and all Zone 2. Back-to-back –30%. |
| **Poor** (readiness < 25 or sleep < 40) | Long run moves to tomorrow if possible, otherwise it's halved. Anything else → 30 min recovery or rest. Strength dropped. |
| 3 low/poor days in a row | Next 3 days lightened (hard sessions become easy, long runs –20%) |
| Missed yesterday's long run | Moved to today if today was an easy day and you're not in poor shape |
| Missed a hard session | Not made up; the week carries on |

Once today's decision is made it's locked, so later runs that day don't change it again. Use `sync --force` to redo it.

## Setup

```bash
cd ~/Documents/Personal/garmin-trail-plan
uv run trailplan login            # once: Garmin email/password (+ MFA code); tokens saved in ~/.garminconnect
uv run trailplan plan             # builds the season; reads your last 6 weeks from Garmin to gauge fitness
uv run trailplan show             # this week + next
uv run trailplan show --season    # one line per week, whole season
uv run trailplan sync --dry-run   # see what today's sync would do
uv run trailplan sync             # do it: adapt + send the next 7 days to the watch
uv run trailplan install-schedule # run sync automatically at 06:30, 08:00, 10:00 with a Mac notification
uv run trailplan uninstall-schedule # stop the automatic sync
```

Run `login` in a real Terminal window. It asks for your password and an MFA code, which can't be typed into an embedded shell. If the saved login ever expires, the scheduled sync sends a notification asking you to run it again.

Sleep and readiness only reach Garmin Connect after your watch syncs with the phone app in the morning. The automatic sync runs three times, so a late sync is still picked up. If there's no data yet, it waits rather than guessing.

## Dashboard, effort checks and race plans

- `uv run trailplan dashboard` opens `dashboard.html`, which is rebuilt after every sync. It shows a countdown to the next race, this week's planned vs done, weekly running time across the season, long-run progression toward each race, readiness and sleep, recent sessions with time in each heart-rate zone, and everything the plan changed. It's a local file, so nothing leaves your machine.
- Each sync also checks **how** you ran, using your Garmin heart-rate zones. It flags easy days run too hard, hard sessions that never reached the target zone, and long runs well short of their climbing target. It also warns if your zones are based on max HR while Garmin knows your lactate threshold.
- `uv run trailplan race-plan [race] [--goal H:MM]` prints race-day splits: arrival time at each checkpoint, buffer against each cutoff, carbs per leg, and gels and fluid to carry out of each aid station. Checkpoints and cutoffs live in `season.toml` under `[[race.checkpoint]]`. Road races without checkpoints get even 5 km splits and a pace column, and races under 75 minutes get no fuel plan. Set `carbs_per_hour` and `fluid_ml_per_hour` to match what you've practised.

## Road PBs

Add a race with `type = "road"` and the plan switches to road training for that block. Sessions are 5k-pace intervals by distance, threshold cruise intervals, long runs finishing at marathon pace, and a 5k time trial (parkrun works well). There are no back-to-backs or climbing targets, and long runs are capped at 3h. Road sessions use **pace targets**, worked out each time a workout is sent to the watch from Garmin's latest race predictions. As your predictions improve, your paces get faster too. If you've set a marathon `goal_time`, marathon pace uses whichever is faster: that goal or the prediction. Every sync reads your 5k, 10k, half and marathon PBs from Garmin, tells you when you set a new one, and the dashboard shows PBs against predictions. Short races (under 90 min) don't trigger recovery weeks or reset your fitness. For goals under an hour, use `H:MM:SS`, e.g. `goal_time = "0:44:00"`.

## Race-day pacing workouts

```bash
uv run road-race 5km --goal 19:55                       # today, 1 km steps
uv run road-race marathon --goal 3:50 --date 2027-10-10 # 5 km steps, gel reminders every ~30 min
uv run road-race half --goal 1:42 --window 2 --dry-run  # tighter pace window; preview only
```

This sends an even-paced workout to your Garmin calendar. Each step ends at a distance and has a pace window (±3% by default), so the watch beeps if you drift too fast or too slow. The step note tells you when you should reach the end of the chunk, plus gel reminders in races over an hour. Goals: `19:55` is minutes:seconds, `1:45` for a half or marathon is hours:minutes, and `H:MM:SS` always works. Running the command again for the same date and distance replaces the earlier workout, and the daily sync leaves these alone. On race day, start a Run and pick **Do Workout**. Try one at a parkrun first.

## On the watch

Workouts are named `TP …` and appear in the Garmin Connect calendar and on the watch, under the day's scheduled workout and Training → Workouts. Start trail sessions from the **Trail Run** activity and road sessions from **Run**: when a workout is scheduled for today the watch offers **Do Workout**, or find it under the activity's menu → Training.

**Heart-rate zones:** trail workout targets are your watch's HR zones, so base them on your lactate threshold, not max HR. On an Enduro 3: hold the middle-left button → **Watch Settings → User Profile → Heart Rate & Power Zones → Heart Rate**. Check **LTHR** (the watch detects it), then **Zones → Based On → %LTHR**. Sync, and `trailplan sync` stops warning about zones. Set `hr_targets = false` in `season.toml` if you'd rather not get zone alerts.

Hill repeats use the LAP button: press it at the bottom of the hill to start the next rep.

**Fuel prompts:** long runs and back-to-backs are split into 30-minute steps. At each one the watch beeps and shows **FUEL: gel + drink**, so you practise race-day fuelling on every long run. Change the interval with `fuel_every_min` in `season.toml` (`0` turns it off). Road races pushed with `road-race` carry their own gel reminders. For trail races, set up the same reminders on the watch under Trail Run → settings → Alerts → Add New → **Custom**, recurring every 30 min (or every ~250 calories).

## Changing things

Edit `season.toml` (races and goal times, days per week, long-run day, strength on/off, fuel interval, race-day carbs and fluid), then `uv run trailplan plan`. Past days and their history are kept. Today is also kept once it's been synced or adapted, and everything after that is rebuilt. `uv run trailplan reset` removes every workout this app put on Garmin.

`uv run trailplan debug [YYYY-MM-DD]` prints the raw Garmin data for a day if something looks off.

## Caveats

- This uses Garmin Connect's **unofficial** API (the `garminconnect` library), not Garmin Coach. Garmin can change it without notice.
- The plan comes from rules, not a coach. Treat it as a strong default and listen to your body, especially for niggles, which it can't see.
- Race durations are estimated from distance + climbing (100 m up ≈ 1 km) and your easy pace. Set `goal_time` per race if you know better.

## Licence

MIT, see [LICENSE](LICENSE).
