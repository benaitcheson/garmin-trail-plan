# trailplan

An adaptive trail-running plan for a Garmin watch (built for an Enduro 3).

It builds a whole season around your races (`season.toml`), then every morning:

1. Checks what you actually ran yesterday (done / partial / missed).
2. Reads **Training Readiness, sleep score and HRV status** from Garmin Connect.
3. Adjusts today and the next few days.
4. Keeps the next 7 days on your Garmin calendar as structured workouts with heart-rate zone targets, so they show up on the watch.

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
```

Sleep and readiness only reach Garmin Connect after your watch syncs with the phone app in the morning. The automatic sync runs three times, so a late sync is still picked up. If there's no data yet, it waits rather than guessing.

## On the watch

Workouts are named `TP …` and appear in the Garmin Connect calendar and on the watch, under the day's scheduled workout and Training → Workouts. Start them from the **Trail Run** activity so you get trail-run data. Targets are your watch's **heart-rate zones**, so set those up properly. Lactate-threshold-based zones work best (Enduro 3 can detect your threshold with a chest strap). Set `hr_targets = false` in `season.toml` if you'd rather not get zone alerts.

Hill repeats use the LAP button: press it at the bottom of the hill to start the next rep.

**Fuel prompts:** long runs and back-to-backs are split into 30-minute steps. At each one the watch beeps and shows **FUEL: gel + drink**, so you practise race-day fuelling on every long run. Change the interval with `fuel_every_min` in `season.toml` (`0` turns it off). For races, which you won't run as a workout, set up the same reminders on the watch under Trail Run → settings → Alerts → Add New → **Custom**, recurring every 30 min (or every ~250 calories).

## Changing things

Edit `season.toml` (race dates, days per week, long-run day, strength on/off), then `uv run trailplan plan`. Past days and their history are kept, and anything from today onward is rebuilt. `uv run trailplan reset` removes every workout this app put on Garmin.

`uv run trailplan debug [YYYY-MM-DD]` prints the raw Garmin data for a day if something looks off.

## Caveats

- This uses Garmin Connect's **unofficial** API (the `garminconnect` library), not Garmin Coach. Garmin can change it without notice.
- The plan comes from rules, not a coach. Treat it as a strong default and listen to your body, especially for niggles, which it can't see.
- Race durations are estimated from distance + climbing (100 m up ≈ 1 km) and your easy pace. Set `goal_time` per race if you know better.

## Licence

MIT, see [LICENSE](LICENSE).
