"""Local HTML dashboard: plan vs actual, long-run progression, readiness, recent sessions.

Rebuilt after every sync. Everything is inlined so the file works offline and
your health data never leaves the machine.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .config import Config
from .model import Session
from .store import Store


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _session_row(s: Session) -> dict:
    a = s.actual or {}
    return {
        "date": s.date.isoformat(),
        "title": s.title,
        "kind": s.kind,
        "planned": s.minutes if s.kind != "race" else None,
        "status": s.status,
        "adjusted": s.adjusted,
        "target_vert": s.target_vert or None,
        "done": a.get("minutes"),
        "avg_hr": a.get("avg_hr"),
        "zones": a.get("zone_min"),
        "elev": a.get("elev"),
        "flags": a.get("flags", []),
    }


def build_data(store: Store, cfg: Config, today: date) -> dict:
    by_week: dict[str, list[Session]] = {}
    for s in store.sessions.values():
        by_week.setdefault(_monday(s.date).isoformat(), []).append(s)

    weeks = []
    for iso in sorted(set(store.weeks) | set(by_week)):
        info = store.weeks.get(iso, {})
        sessions = by_week.get(iso, [])
        runs = [s for s in sessions if s.is_run]
        longs = [s for s in runs if s.kind == "long"]
        past = date.fromisoformat(iso) <= today
        done = [s.actual.get("minutes", 0) for s in runs if s.actual]
        done_long = [s.actual.get("minutes", 0) for s in longs if s.actual]
        weeks.append({
            "monday": iso,
            "number": info.get("number"),
            "phase": info.get("phase", ""),
            "race": info.get("race", ""),
            "planned": sum(s.minutes for s in runs),
            "done": sum(done) if past else None,
            "planned_long": max((s.minutes for s in longs), default=None),
            "done_long": max(done_long, default=None) if past else None,
        })

    recent = store.between(today - timedelta(days=14), today + timedelta(days=7))
    this_mon = _monday(today)
    this_week = [s for s in store.between(this_mon, this_mon + timedelta(days=6)) if s.is_run]
    next_race = next((r for r in cfg.races if r.date >= today), None)
    peak_long = max(
        (s.minutes for s in store.sessions.values() if s.kind == "long" and next_race and s.date < next_race.date),
        default=None,
    )
    recent_long = max(
        (s.actual.get("minutes", 0) for s in store.sessions.values()
         if s.is_run and s.actual and s.date >= today - timedelta(days=28)),
        default=0,
    )
    today_run = store.run_on(today)
    decision = store.decisions.get(today.isoformat(), {})

    return {
        "today": today.isoformat(),
        "races": [
            {"name": r.name, "date": r.date.isoformat(), "priority": r.priority, "est_min": r.est_min}
            for r in cfg.races
        ],
        "tiles": {
            "next_race": next_race.name if next_race else None,
            "days_to_race": (next_race.date - today).days if next_race else None,
            "race_date": next_race.date.isoformat() if next_race else None,
            "week_planned": sum(s.minutes for s in this_week),
            "week_done": sum(s.actual.get("minutes", 0) for s in this_week if s.actual),
            "recent_long": recent_long,
            "peak_long": peak_long,
            "today_title": today_run.title if today_run else "Rest day",
            "today_adjusted": today_run.adjusted if today_run else "",
            "today_status": decision.get("status"),
            "readiness": decision.get("readiness"),
            "sleep": decision.get("sleep"),
        },
        "weeks": weeks,
        "wellness": [
            {"date": d, "readiness": v.get("readiness"), "sleep": v.get("sleep"),
             "hrv": v.get("hrv"), "status": v.get("status")}
            for d, v in sorted(store.decisions.items())
            if d >= (today - timedelta(days=42)).isoformat()
        ],
        "sessions": [_session_row(s) for s in recent],
        "log": store.log[-25:][::-1],
    }


def write_dashboard(store: Store, cfg: Config, today: date, path: Path) -> Path:
    data = json.dumps(build_data(store, cfg, today)).replace("</", "<\\/")
    path.write_text(TEMPLATE.replace("/*__DATA__*/null", data))
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trail Plan</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834;
  --planned: #e1e0d9; --planned-line: #898781;
  --z1: #86b6ef; --z2: #5598e7; --z3: #2a78d6; --z4: #1c5cab; --z5: #104281;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926;
    --planned: #383835; --planned-line: #898781;
    --z1: #184f95; --z2: #256abf; --z3: #3987e5; --z4: #6da7ec; --z5: #9ec5f4;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #898781;
  --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926;
  --planned: #383835; --planned-line: #898781;
  --z1: #184f95; --z2: #256abf; --z3: #3987e5; --z4: #6da7ec; --z5: #9ec5f4;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text-primary);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 24px 16px 48px; }
header { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 20px; margin: 0; font-weight: 600; }
h2 { font-size: 15px; margin: 0 0 2px; font-weight: 600; }
.sub { color: var(--text-secondary); margin: 0 0 12px; font-size: 13px; }
.muted { color: var(--text-muted); }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-top: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 16px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.tile .label { color: var(--text-secondary); font-size: 13px; }
.tile .value { font-size: 28px; font-weight: 600; margin-top: 2px; }
.tile .value.hero { font-size: 48px; line-height: 1.05; }
.tile .note { color: var(--text-secondary); font-size: 13px; margin-top: 2px; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.key { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.ln { width: 16px; height: 2px; border-radius: 1px; display: inline-block; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart text { fill: var(--text-muted); font-size: 11px; }
.tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.12);
  white-space: nowrap; display: none; z-index: 2; }
.tip strong { font-size: 14px; color: var(--text-primary); }
.tip .row { display: flex; align-items: center; gap: 6px; color: var(--text-secondary); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--text-secondary); font-weight: 500; border-bottom: 1px solid var(--grid); padding: 6px 8px; }
td { border-bottom: 1px solid var(--grid); padding: 7px 8px; vertical-align: top; }
#sessions table { min-width: 680px; }
#sessions td:first-child { white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.future td { color: var(--text-muted); }
.table-wrap { overflow-x: auto; }
.badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.flag { color: var(--text-secondary); font-size: 12px; margin-top: 2px; }
.flag::before { content: "⚠ "; color: var(--warning); }
.adj { color: var(--text-secondary); font-size: 12px; margin-top: 2px; }
.zbar { display: flex; gap: 2px; width: 120px; height: 10px; }
.zbar span { display: block; height: 100%; }
.zbar span:first-child { border-radius: 2px 0 0 2px; } .zbar span:last-child { border-radius: 0 2px 2px 0; }
details { margin-top: 10px; } summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; }
.log li { margin: 2px 0; color: var(--text-secondary); }
.theme-btn { background: none; border: 1px solid var(--border); color: var(--text-secondary); border-radius: 8px; padding: 4px 10px; cursor: pointer; font: inherit; font-size: 13px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>Trail Plan</h1>
    <span class="muted" id="updated"></span>
    <button class="theme-btn" id="theme">Theme</button>
  </header>
  <section class="tiles" id="tiles"></section>

  <section class="card">
    <h2>Season: weekly running time</h2>
    <p class="sub">Planned vs done each week. Races are marked along the top.</p>
    <div class="legend"><span class="key"><span class="sw" style="background:var(--planned)"></span>Planned</span>
      <span class="key"><span class="sw" style="background:var(--series-1)"></span>Done</span></div>
    <div class="chart" id="volume"></div>
    <details><summary>Table view</summary><div class="table-wrap" id="volume-table"></div></details>
  </section>

  <section class="card">
    <h2>Long run progression</h2>
    <p class="sub">Longest run each week: the plan's build toward each race, and what you actually ran.</p>
    <div class="legend"><span class="key"><span class="ln" style="background:var(--planned-line)"></span>Planned long run</span>
      <span class="key"><span class="dot" style="background:var(--series-1)"></span>Done</span></div>
    <div class="chart" id="longrun"></div>
  </section>

  <section class="card">
    <h2>Readiness and sleep</h2>
    <p class="sub">Morning Training Readiness and sleep score (last 6 weeks). Below 50 readiness or 60 sleep, the plan eases off.</p>
    <div class="legend"><span class="key"><span class="ln" style="background:var(--series-1)"></span>Training Readiness</span>
      <span class="key"><span class="ln" style="background:var(--series-2)"></span>Sleep score</span></div>
    <div class="chart" id="wellness"></div>
  </section>

  <section class="card">
    <h2>Recent and upcoming sessions</h2>
    <p class="sub">Last two weeks and the next seven days.</p>
    <div class="legend" id="zone-legend"></div>
    <div class="table-wrap" id="sessions"></div>
  </section>

  <section class="card">
    <h2>What the plan changed</h2>
    <ul class="log" id="log"></ul>
  </section>
</main>
<script>
const DATA = /*__DATA__*/null;
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};
const h = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") n.textContent = v; else if (k === "style") n.style.cssText = v; else n.setAttribute(k, v);
  }
  for (const k of kids) if (k) n.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  return n;
};
const fmt = (m) => m == null ? "–" : (m < 60 ? `${Math.round(m)} min` : `${Math.floor(m / 60)}h${String(Math.round(m % 60)).padStart(2, "0")}`);
const d = (iso) => new Date(iso + "T00:00:00");
const dShort = (iso) => d(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const dDay = (iso) => d(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

// theme toggle
const root = document.documentElement;
try { const t = localStorage.getItem("tp-theme"); if (t) root.dataset.theme = t; } catch (e) {}
$("theme").onclick = () => {
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("tp-theme", root.dataset.theme); } catch (e) {}
  renderAll();
};

function tooltip(container) {
  const t = h("div", { class: "tip" });
  container.appendChild(t);
  return {
    show(x, y, rows) {
      t.replaceChildren(...rows);
      t.style.display = "block";
      const cw = container.clientWidth, tw = t.offsetWidth;
      t.style.left = Math.min(Math.max(x + 12, 0), cw - tw) + "px";
      t.style.top = Math.max(y - t.offsetHeight - 8, 0) + "px";
    },
    hide() { t.style.display = "none"; },
  };
}
const tipRow = (color, label, value, line = true) => h("div", { class: "row" },
  color ? h("span", { class: line ? "ln" : "sw", style: `background:${color}` }) : null,
  h("strong", { text: value }), h("span", { text: label }));

function axisY(svg, x0, x1, y, max, H, top, fmtTick, unit = 1) {
  const step = niceStep(max / unit) * unit;  // unit=60 puts gridlines on whole hours
  for (let v = 0; v <= max + 1e-9; v += step) {
    const yy = y(v);
    el("line", { x1: x0, x2: x1, y1: yy, y2: yy, stroke: v === 0 ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: x0 - 6, y: yy + 4, "text-anchor": "end" }, svg); t.textContent = fmtTick(v);
  }
}
function niceStep(max) {
  const raw = max / 5, p = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
const roundedTopRect = (x, y, w, hgt, r) => {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
};

function renderTiles() {
  const t = DATA.tiles, box = $("tiles");
  box.replaceChildren();
  const tile = (label, value, note, hero) => box.appendChild(h("div", { class: "tile" },
    h("div", { class: "label", text: label }), h("div", { class: "value" + (hero ? " hero" : ""), text: value }),
    note ? h("div", { class: "note", text: note }) : null));
  if (t.next_race) tile(`Days to ${t.next_race}`, String(t.days_to_race), dDay(t.race_date), true);
  tile("This week", `${fmt(t.week_done)}`, `of ${fmt(t.week_planned)} planned`);
  tile("Longest run, last 4 weeks", fmt(t.recent_long), t.peak_long ? `builds to ${fmt(t.peak_long)} before ${t.next_race}` : "");
  const status = t.today_status ? t.today_status[0].toUpperCase() + t.today_status.slice(1) : "Not checked yet";
  const bits = [t.readiness != null ? `readiness ${t.readiness}` : null, t.sleep != null ? `sleep ${t.sleep}` : null].filter(Boolean).join(", ");
  tile("Today", t.today_title, `${status}${bits ? " · " + bits : ""}${t.today_adjusted ? " · " + t.today_adjusted : ""}`);
}

function renderVolume() {
  const box = $("volume"); box.replaceChildren();
  const W = Math.max(box.clientWidth, 320), H = 272, L = 44, R = 8, T = 40, B = 26;
  const weeks = DATA.weeks, n = weeks.length;
  const max = Math.max(60, ...weeks.map(w => Math.max(w.planned || 0, w.done || 0)));
  const raceLabelEnds = [-Infinity, -Infinity];  // right edge of the last label on each row
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Weekly running time, planned vs done" }, box);
  const y = (v) => T + (H - T - B) * (1 - v / (max * 1.05));
  axisY(svg, L, W - R, y, max * 1.05, H, T, (v) => v ? `${Math.round(v / 60)}h` : "0", 60);
  const slot = (W - L - R) / n, bw = Math.min(24, Math.max(3, slot - 2)), dw = Math.max(2, bw * 0.55);
  const tip = tooltip(box);
  const todayMon = DATA.weeks.findIndex(w => w.monday > DATA.today) - 1;
  weeks.forEach((w, i) => {
    const x = L + i * slot + (slot - bw) / 2;
    if (w.planned) el("path", { d: roundedTopRect(x, y(w.planned), bw, y(0) - y(w.planned), Math.min(4, bw / 2)), fill: "var(--planned)" }, svg);
    if (w.done) el("path", { d: roundedTopRect(x + (bw - dw) / 2, y(w.done), dw, y(0) - y(w.done), Math.min(4, dw / 2)), fill: "var(--series-1)" }, svg);
    if (w.race) {
      const cx = L + i * slot + slot / 2;
      const anchor = cx > W - 90 ? "end" : cx < L + 60 ? "start" : "middle";
      const t = el("text", { x: anchor === "end" ? cx + 4 : cx, y: 0, "text-anchor": anchor, style: "fill:var(--text-secondary);font-weight:600" }, svg);
      t.textContent = w.race;
      // Labels that would collide go on a second row, with the marker line reaching up to them.
      const bb = t.getBBox(), row = bb.x > raceLabelEnds[0] + 6 ? 0 : 1;
      raceLabelEnds[row] = bb.x + bb.width;
      const ty = row === 0 ? T - 24 : T - 10;
      t.setAttribute("y", ty);
      el("line", { x1: cx, x2: cx, y1: ty + 3, y2: y(0), stroke: "var(--baseline)", "stroke-width": 1 }, svg);
    }
    const hit = el("rect", { x: L + i * slot, y: T, width: slot, height: H - T - B, fill: "transparent", tabindex: 0 }, svg);
    const show = (ev) => {
      const r = box.getBoundingClientRect(), px = ev.clientX ? ev.clientX - r.left : L + i * slot, py = ev.clientY ? ev.clientY - r.top : y(w.planned);
      tip.show(px, py, [
        h("div", { text: `Week ${w.number ?? "–"} · ${w.phase || ""} · ${dShort(w.monday)}` + (w.race ? ` · ${w.race}` : "") }),
        tipRow(css("--series-1"), "done", w.done != null ? fmt(w.done) : "–", false),
        tipRow(css("--planned"), "planned", fmt(w.planned), false),
      ]);
    };
    hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", () => tip.hide()); hit.addEventListener("blur", () => tip.hide());
  });
  if (todayMon >= 0) {
    const cx = L + (todayMon + 0.5) * slot;
    el("line", { x1: cx, x2: cx, y1: T, y2: y(0), stroke: "var(--text-secondary)", "stroke-width": 1 }, svg);
    const t = el("text", { x: cx + 4, y: T + 10, style: "fill:var(--text-secondary)" }, svg); t.textContent = "Today";
  }
  // month ticks
  let lastMonth = null, lastX = -Infinity;
  weeks.forEach((w, i) => {
    const m = d(w.monday).getMonth();
    if (m !== lastMonth && (i === 0 || d(w.monday).getDate() <= 7) && L + i * slot - lastX >= 36) {
      lastX = L + i * slot;
      const t = el("text", { x: L + i * slot, y: H - 8 }, svg);
      t.textContent = d(w.monday).toLocaleDateString(undefined, { month: "short" }) + (m === 0 ? " " + String(d(w.monday).getFullYear()).slice(2) : "");
    }
    lastMonth = m;
  });
  // table view
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Week" }), h("th", { text: "Starts" }), h("th", { text: "Phase" }),
    h("th", { class: "num", text: "Planned" }), h("th", { class: "num", text: "Done" }), h("th", { class: "num", text: "Long (plan)" }))));
  const tb = h("tbody");
  weeks.forEach(w => tb.appendChild(h("tr", {}, h("td", { text: String(w.number ?? "–") }), h("td", { text: dShort(w.monday) }),
    h("td", { text: w.race ? `race: ${w.race}` : w.phase }), h("td", { class: "num", text: fmt(w.planned) }),
    h("td", { class: "num", text: w.done != null ? fmt(w.done) : "–" }), h("td", { class: "num", text: fmt(w.planned_long) }))));
  tbl.appendChild(tb); $("volume-table").replaceChildren(tbl);
}

function renderLongRun() {
  const box = $("longrun"); box.replaceChildren();
  const W = Math.max(box.clientWidth, 320), H = 220, L = 44, R = 12, T = 12, B = 26;
  const weeks = DATA.weeks, n = weeks.length;
  const max = Math.max(60, ...weeks.map(w => Math.max(w.planned_long || 0, w.done_long || 0)));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Long run per week, planned vs done" }, box);
  const y = (v) => T + (H - T - B) * (1 - v / (max * 1.08));
  const x = (i) => L + (W - L - R) * (i + 0.5) / n;
  axisY(svg, L, W - R, y, max * 1.08, H, T, (v) => v ? `${Math.round(v / 60)}h` : "0", 60);
  let dPath = "", pen = false;
  weeks.forEach((w, i) => {
    if (w.planned_long) { dPath += `${pen ? "L" : "M"}${x(i)},${y(w.planned_long)}`; pen = true; } else pen = false;
  });
  el("path", { d: dPath, fill: "none", stroke: "var(--planned-line)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
  weeks.forEach((w, i) => {
    if (w.done_long) el("circle", { cx: x(i), cy: y(w.done_long), r: 4, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
  });
  // crosshair + tooltip
  const cross = el("line", { y1: T, y2: H - B, stroke: "var(--baseline)", "stroke-width": 1, visibility: "hidden" }, svg);
  const tip = tooltip(box);
  const hit = el("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", tabindex: 0 }, svg);
  const at = (px) => Math.max(0, Math.min(n - 1, Math.round((px - L) / ((W - L - R) / n) - 0.5)));
  const show = (ev) => {
    const r = box.getBoundingClientRect(), scale = W / r.width;
    const i = at(((ev.clientX ?? r.left + x(0) / scale) - r.left) * scale), w = weeks[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    tip.show(x(i) / scale, (y(w.planned_long || 0)) / scale, [
      h("div", { text: `Week ${w.number ?? "–"} · ${dShort(w.monday)}` + (w.race ? ` · ${w.race}` : "") }),
      tipRow(css("--series-1"), "done", w.done_long ? fmt(w.done_long) : "–"),
      tipRow(css("--planned-line"), "planned", fmt(w.planned_long)),
    ]);
  };
  hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
  hit.addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); });
  // label the peak planned long run before each race
  DATA.races.forEach(rc => {
    let best = -1;
    weeks.forEach((w, i) => { if (w.planned_long && w.monday < rc.date && (best < 0 || w.planned_long >= weeks[best].planned_long) && w.monday > (DATA.races.filter(o => o.date < rc.date).map(o => o.date).pop() || "")) best = i; });
    if (best >= 0) { const t = el("text", { x: x(best), y: y(weeks[best].planned_long) - 8, "text-anchor": "middle", style: "fill:var(--text-secondary)" }, svg); t.textContent = fmt(weeks[best].planned_long); }
  });
}

function renderWellness() {
  const box = $("wellness"); box.replaceChildren();
  const rows = DATA.wellness;
  if (!rows.length) { box.appendChild(h("p", { class: "muted", text: "No readings yet - they appear after each morning sync." })); return; }
  const W = Math.max(box.clientWidth, 320), H = 200, L = 32, R = 12, T = 10, B = 26;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Training readiness and sleep score" }, box);
  const start = d(DATA.today).getTime() - 41 * 864e5, span = 41 * 864e5;
  const x = (iso) => L + (W - L - R) * (d(iso).getTime() - start) / span;
  const y = (v) => T + (H - T - B) * (1 - v / 100);
  [0, 25, 50, 75, 100].forEach(v => {
    el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v === 0 ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, svg); t.textContent = v;
  });
  for (const [key, color] of [["readiness", "var(--series-1)"], ["sleep", "var(--series-2)"]]) {
    const pts = rows.filter(r => r[key] != null);
    if (pts.length > 1) el("path", { d: pts.map((r, i) => `${i ? "L" : "M"}${x(r.date)},${y(r[key])}`).join(""), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    pts.forEach(r => el("circle", { cx: x(r.date), cy: y(r[key]), r: 4, fill: color, stroke: "var(--surface-1)", "stroke-width": 2 }, svg));
  }
  [0, 14, 28, 41].forEach(off => { const iso = new Date(start + off * 864e5).toISOString().slice(0, 10); const t = el("text", { x: x(iso), y: H - 8, "text-anchor": off === 0 ? "start" : off === 41 ? "end" : "middle" }, svg); t.textContent = dShort(iso); });
  const cross = el("line", { y1: T, y2: H - B, stroke: "var(--baseline)", "stroke-width": 1, visibility: "hidden" }, svg);
  const tip = tooltip(box);
  const hit = el("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", tabindex: 0 }, svg);
  const show = (ev) => {
    const r = box.getBoundingClientRect(), scale = W / r.width, px = ((ev.clientX ?? r.right) - r.left) * scale;
    const near = rows.reduce((a, b) => Math.abs(x(b.date) - px) < Math.abs(x(a.date) - px) ? b : a);
    cross.setAttribute("x1", x(near.date)); cross.setAttribute("x2", x(near.date)); cross.setAttribute("visibility", "visible");
    tip.show(x(near.date) / scale, y(Math.max(near.readiness ?? 0, near.sleep ?? 0)) / scale, [
      h("div", { text: `${dDay(near.date)}${near.status ? " · " + near.status : ""}${near.hrv ? " · HRV " + near.hrv.toLowerCase() : ""}` }),
      tipRow(css("--series-1"), "readiness", near.readiness ?? "–"),
      tipRow(css("--series-2"), "sleep", near.sleep ?? "–"),
    ]);
  };
  hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
  hit.addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); });
}

function renderSessions() {
  $("zone-legend").replaceChildren(h("span", { class: "muted", text: "Time in HR zone:" }),
    ...[1, 2, 3, 4, 5].map(z => h("span", { class: "key" }, h("span", { class: "sw", style: `background:var(--z${z})` }), `Zone ${z}`)));
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Day" }), h("th", { text: "Session" }),
    h("th", { class: "num", text: "Planned" }), h("th", { class: "num", text: "Done" }), h("th", { class: "num", text: "Avg HR" }),
    h("th", { text: "HR zones" }), h("th", { class: "num", text: "Climb" }))));
  const tb = h("tbody");
  const label = { done: "✓ done", partial: "~ partial", missed: "✗ missed", planned: "", dropped: "– dropped" };
  for (const s of DATA.sessions) {
    const future = s.date > DATA.today;
    const zt = s.zones ? s.zones.reduce((a, b) => a + b, 0) : 0;
    const zbar = zt ? h("div", { class: "zbar", title: s.zones.map((m, i) => `Z${i + 1} ${m} min`).join(", ") },
      ...s.zones.map((m, i) => m ? h("span", { style: `width:${100 * m / zt}%;background:var(--z${i + 1})` }) : null)) : null;
    const name = h("td", {}, h("div", { text: s.title }),
      s.status !== "planned" && s.date <= DATA.today ? h("div", { class: "adj", text: label[s.status] || s.status }) : null,
      s.adjusted ? h("div", { class: "adj", text: "Changed: " + s.adjusted }) : null,
      ...(s.flags || []).map(f => h("div", { class: "flag", text: f })));
    tb.appendChild(h("tr", { class: future ? "future" : "" }, h("td", { text: dDay(s.date) }), name,
      h("td", { class: "num", text: s.planned != null ? fmt(s.planned) : "" }),
      h("td", { class: "num", text: s.done != null ? fmt(s.done) : "" }),
      h("td", { class: "num", text: s.avg_hr ?? "" }), h("td", {}, zbar),
      h("td", { class: "num", text: s.elev != null ? `${s.elev} m` + (s.target_vert ? ` / ${s.target_vert}` : "") : (s.target_vert ? `target ${s.target_vert} m` : "") })));
  }
  tbl.appendChild(tb); $("sessions").replaceChildren(tbl);
}

function renderLog() {
  const items = DATA.log.map(l => h("li", {}, h("span", { class: "muted", text: dShort(l.date) + "  " }), l.msg));
  $("log").replaceChildren(...(items.length ? items : [h("li", { text: "Nothing yet." })]));
}

function renderAll() { renderTiles(); renderVolume(); renderLongRun(); renderWellness(); renderSessions(); renderLog(); }
$("updated").textContent = "Updated " + dDay(DATA.today);
renderAll();
let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(renderAll, 150); });
</script>
</body>
</html>
"""
