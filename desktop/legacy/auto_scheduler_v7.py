# -*- coding: utf-8 -*-
"""
Auto Scheduler (Pomodoro-linked) — v7

New in v7 (your "方案B"):
✅ Daily leisure reserve window (default 21:00–22:00):
   - Treated as protected leisure time (WORK cannot use it).
   - BUT: it can be borrowed ONLY by WORK tasks that HAVE a due date (DDL tasks),
     when those DDL tasks cannot fit otherwise.
   - Non-DDL "personal growth" work will NOT occupy the reserve.
✅ Reserve window is shown inside Schedule (highlighted) when not borrowed.
✅ Fixed events are interleaved at their real time position.
✅ Rolling refresh from next full hour.
✅ Day separators.
✅ 1 hour block == 2 pomodoros by default (pomos_per_block).

Run:
    python auto_scheduler_v7.py
Creates:
    auto_scheduler_v7.config.json
    schedule_export.csv
"""

import json
import os
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REFRESH_MS = 5 * 60 * 1000  # 5 minutes


# -------------------------
# TomatoManager data reading
# -------------------------

def default_tm_data_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "TomatoManager" / "tm_data.json"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


# -------------------------
# Time helpers / intervals
# -------------------------

def parse_hhmm(s: str) -> time:
    s = s.strip()
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def dt_combine(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, 0)


def minutes_between(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def ceil_to_next_hour(dt: datetime) -> datetime:
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    dt0 = dt.replace(minute=0, second=0, microsecond=0)
    return dt0 + timedelta(hours=1)


def overlaps(a: Tuple[datetime, datetime], b: Tuple[datetime, datetime]) -> bool:
    return not (a[1] <= b[0] or a[0] >= b[1])


def subtract_intervals(
    base: List[Tuple[datetime, datetime]],
    blocks: List[Tuple[datetime, datetime]],
) -> List[Tuple[datetime, datetime]]:
    remain = base[:]
    for bs, be in blocks:
        new_remain: List[Tuple[datetime, datetime]] = []
        for rs, re in remain:
            if be <= rs or bs >= re:
                new_remain.append((rs, re))
                continue
            if bs > rs:
                new_remain.append((rs, bs))
            if be < re:
                new_remain.append((be, re))
        remain = new_remain
    remain.sort(key=lambda x: x[0])
    return remain


# -------------------------
# Availability / fixed / reserve
# -------------------------

def build_daily_slots(
    d: date,
    availability: Dict[str, List[List[str]]],
    start_dt: Optional[datetime],
) -> List[Tuple[datetime, datetime]]:
    weekday = d.weekday()
    slots = availability.get(str(weekday), []) or []
    out: List[Tuple[datetime, datetime]] = []
    for pair in slots:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        s, e = pair
        try:
            st = dt_combine(d, parse_hhmm(s))
            en = dt_combine(d, parse_hhmm(e))
            if en <= st:
                continue
            if start_dt is not None and d == start_dt.date():
                st = max(st, start_dt)
            if en > st:
                out.append((st, en))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def fixed_blocks_for_day(
    d: date,
    fixed_events: List[Dict[str, Any]],
) -> List[Tuple[datetime, datetime, str]]:
    out: List[Tuple[datetime, datetime, str]] = []
    iso = d.isoformat()
    for ev in fixed_events or []:
        if ev.get("date") != iso:
            continue
        try:
            st = dt_combine(d, parse_hhmm(ev["start"]))
            en = dt_combine(d, parse_hhmm(ev["end"]))
            if en > st:
                out.append((st, en, str(ev.get("title", "Fixed"))))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def reserve_blocks_for_day(
    d: date,
    cfg: Dict[str, Any],
    start_dt: datetime,
) -> List[Tuple[datetime, datetime, str]]:
    reserve = cfg.get("daily_leisure_reserve", {}) or {}
    start_s = str(reserve.get("start", "")).strip()
    end_s = str(reserve.get("end", "")).strip()
    if not start_s or not end_s:
        return []

    weekdays = reserve.get("weekdays", [0, 1, 2, 3, 4, 5, 6])
    try:
        weekdays = list(weekdays)
    except Exception:
        weekdays = [0, 1, 2, 3, 4, 5, 6]

    if d.weekday() not in set(int(x) for x in weekdays):
        return []

    try:
        st = dt_combine(d, parse_hhmm(start_s))
        en = dt_combine(d, parse_hhmm(end_s))
        if en <= st:
            return []
        if d == start_dt.date() and en <= start_dt:
            return []
        if d == start_dt.date() and st < start_dt:
            st = start_dt
        return [(st, en, "Leisure Reserve")]
    except Exception:
        return []


# -------------------------
# Task sorting + scheduler
# -------------------------

def sort_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(t):
        due = t.get("due") or ""
        due_key = due if due else "9999-12-31"
        pr = -int(t.get("priority", 3))
        rem = -int(t.get("remaining_blocks", 0))
        return (due_key, pr, rem, t.get("category", ""), t.get("title", ""))

    return sorted(tasks, key=key)


def category_cycle(tasks: List[Dict[str, Any]]) -> List[str]:
    cats = []
    seen = set()
    for t in tasks:
        c = (t.get("category") or "").strip()
        if c and c not in seen:
            seen.add(c)
            cats.append(c)
    return cats


def schedule_kind(
    tasks: List[Dict[str, Any]],
    availability: Dict[str, List[List[str]]],
    fixed_events: List[Dict[str, Any]],
    reserve_as_blocks: bool,
    cfg: Dict[str, Any],
    start_dt: datetime,
    horizon_days: int,
    block_minutes: int,
    mix_categories: bool,
    kind: str,
    occupied: Optional[Dict[str, List[Tuple[datetime, datetime]]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tasks_k = [t for t in tasks if t.get("kind") == kind and t.get("remaining_blocks", 0) > 0]
    tasks_k = sort_tasks(tasks_k)

    cat_list = category_cycle(tasks_k) if mix_categories else []
    cat_i = 0

    schedule: List[Dict[str, Any]] = []
    occupied = occupied or {}

    for i in range(horizon_days):
        d = start_dt.date() + timedelta(days=i)
        base = build_daily_slots(d, availability, start_dt=start_dt if i == 0 else None)

        fixed = fixed_blocks_for_day(d, fixed_events)
        blocks: List[Tuple[datetime, datetime]] = [(a, b) for a, b, _ in fixed]
        blocks += occupied.get(d.isoformat(), [])

        if reserve_as_blocks:
            res = reserve_blocks_for_day(d, cfg, start_dt)
            blocks += [(a, b) for a, b, _ in res]

        blocks.sort(key=lambda x: x[0])
        free = subtract_intervals(base, blocks)

        for fs, fe in free:
            cursor = fs
            while minutes_between(cursor, fe) >= block_minutes:
                chosen = None

                if mix_categories and cat_list:
                    target = cat_list[cat_i % len(cat_list)]
                    cat_i += 1
                    for t in tasks_k:
                        if t.get("remaining_blocks", 0) <= 0:
                            continue
                        if (t.get("category") or "") == target:
                            chosen = t
                            break

                if chosen is None:
                    for t in tasks_k:
                        if t.get("remaining_blocks", 0) > 0:
                            chosen = t
                            break

                if chosen is None:
                    break

                start_block = cursor
                end_block = cursor + timedelta(minutes=block_minutes)

                schedule.append(
                    {
                        "date": d.isoformat(),
                        "start": start_block.strftime("%H:%M"),
                        "end": end_block.strftime("%H:%M"),
                        "kind": kind,
                        "category": chosen.get("category", ""),
                        "title": chosen.get("title", ""),
                        "is_deadline": bool(chosen.get("is_deadline", False)),
                    }
                )

                chosen["remaining_blocks"] -= 1
                cursor = end_block

    overflow = [t for t in tasks_k if t.get("remaining_blocks", 0) > 0]
    return schedule, overflow


def occupied_map_from_schedule(schedule: List[Dict[str, Any]]) -> Dict[str, List[Tuple[datetime, datetime]]]:
    occ: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for s in schedule:
        d = date.fromisoformat(s["date"])
        st = dt_combine(d, parse_hhmm(s["start"]))
        en = dt_combine(d, parse_hhmm(s["end"]))
        occ.setdefault(s["date"], []).append((st, en))
    for k in occ:
        occ[k].sort(key=lambda x: x[0])
    return occ


# -------------------------
# Config
# -------------------------

def config_path() -> Path:
    return Path(__file__).with_suffix(".config.json")


def default_config() -> Dict[str, Any]:
    return {
        "availability": {
            "0": [["08:00", "12:00"], ["19:00", "22:00"]],
            "1": [["08:00", "12:00"], ["19:00", "22:00"]],
            "2": [["08:00", "12:00"], ["19:00", "22:00"]],
            "3": [["08:00", "12:00"], ["19:00", "22:00"]],
            "4": [["08:00", "12:00"], ["19:00", "22:00"]],
            "5": [["10:00", "12:00"], ["14:00", "17:00"], ["19:00", "22:00"]],
            "6": [["10:00", "12:00"], ["14:00", "17:00"], ["19:00", "22:00"]],
        },
        "fixed_events": [],
        "block_minutes": 60,
        "pomos_per_block": 2,
        "horizon_days": 21,
        "auto_refresh": True,
        "mix_categories": True,
        "leisure_categories": ["娱乐", "休闲", "休息", "Leisure"],

        # ✅ Daily protected leisure time (can be borrowed ONLY by DDL work if needed)
        "daily_leisure_reserve": {
            "start": "21:00",
            "end": "22:00",
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
        },

        "teacher_unavailable": {"start": "", "end": ""},
        "teacher_required_categories": ["申请材料", "文书", "SOP", "PS"],
    }


def load_or_init_config() -> Dict[str, Any]:
    p = config_path()
    if not p.exists():
        cfg = default_config()
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return cfg
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cfg = default_config()
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# -------------------------
# UI App
# -------------------------

class AutoSchedulerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto Scheduler (Pomodoro-linked) — v7")
        self.geometry("1240x800")

        self.cfg = load_or_init_config()
        self.tm_path_var = tk.StringVar(value=str(default_tm_data_path()))
        self.status_var = tk.StringVar(value="Ready.")
        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_refresh", True)))

        self._build_ui()
        self.refresh_fixed_table()
        self.refresh_and_reschedule()
        self._schedule_next_tick()

    def _build_ui(self):
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=22)

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="tm_data.json:").pack(side="left")
        ttk.Entry(top, textvariable=self.tm_path_var, width=82).pack(side="left", padx=(6, 10), fill="x", expand=True)
        ttk.Button(top, text="Refresh + Reschedule", command=self.refresh_and_reschedule).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(top, text="Auto (5 min)", variable=self.auto_var, command=self._sync_auto).pack(side="left")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Settings
        settings_frame = ttk.Frame(nb)
        self.settings_text = tk.Text(settings_frame, wrap="none")
        self.settings_text.pack(fill="both", expand=True, side="top")
        btns = ttk.Frame(settings_frame, padding=(0, 8, 0, 0))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="Save settings", command=self.save_settings_from_editor).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Reset to default", command=self.reset_settings).pack(side="left")
        nb.add(settings_frame, text="Settings (JSON)")

        # Fixed events
        fixed_frame = ttk.Frame(nb, padding=10)
        self.fixed_tree = ttk.Treeview(fixed_frame, columns=("date", "start", "end", "title"), show="headings", height=14)
        for c, w in [("date", 120), ("start", 80), ("end", 80), ("title", 700)]:
            self.fixed_tree.heading(c, text=c)
            self.fixed_tree.column(c, width=w, anchor="w")
        self.fixed_tree.pack(fill="both", expand=True)
        fixed_btns = ttk.Frame(fixed_frame, padding=(0, 8, 0, 0))
        fixed_btns.pack(fill="x")
        ttk.Button(fixed_btns, text="Add fixed event", command=self.add_fixed_event).pack(side="left", padx=(0, 8))
        ttk.Button(fixed_btns, text="Delete selected", command=self.delete_fixed_event).pack(side="left")
        nb.add(fixed_frame, text="Fixed Events")

        # Schedule
        sched_frame = ttk.Frame(nb, padding=10)
        self.sched_tree = ttk.Treeview(
            sched_frame,
            columns=("date", "start", "end", "kind", "category", "title"),
            show="headings",
            height=20,
        )
        for c, w in [("date", 120), ("start", 80), ("end", 80), ("kind", 90), ("category", 160), ("title", 560)]:
            self.sched_tree.heading(c, text=c)
            self.sched_tree.column(c, width=w, anchor="w")
        self.sched_tree.pack(fill="both", expand=True)

        # Tag styles (non-red!)
        self.sched_tree.tag_configure("fixed", background="#DDEBFF")   # light blue
        self.sched_tree.tag_configure("daysep", background="#E8E8E8")  # separator gray
        self.sched_tree.tag_configure("leisure", background="#FFF6CC")  # pale yellow
        self.sched_tree.tag_configure("reserve", background="#FFE9A6")  # stronger warm yellow

        sched_btns = ttk.Frame(sched_frame, padding=(0, 8, 0, 0))
        sched_btns.pack(fill="x")
        ttk.Button(sched_btns, text="Export schedule (CSV)", command=self.export_csv).pack(side="left", padx=(0, 8))
        nb.add(sched_frame, text="Schedule")

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

        self._refresh_settings_editor()

    def _refresh_settings_editor(self):
        self.settings_text.delete("1.0", "end")
        self.settings_text.insert("1.0", json.dumps(self.cfg, ensure_ascii=False, indent=2))

    def _sync_auto(self):
        self.cfg["auto_refresh"] = bool(self.auto_var.get())
        save_config(self.cfg)

    def _schedule_next_tick(self):
        self.after(REFRESH_MS, self._auto_tick)

    def _auto_tick(self):
        if self.auto_var.get():
            self.refresh_and_reschedule(silent=True)
        self._schedule_next_tick()

    def reset_settings(self):
        self.cfg = default_config()
        save_config(self.cfg)
        self.auto_var.set(bool(self.cfg.get("auto_refresh", True)))
        self._refresh_settings_editor()
        self.refresh_fixed_table()
        self.refresh_and_reschedule()

    def save_settings_from_editor(self):
        try:
            raw = self.settings_text.get("1.0", "end").strip()
            cfg_new = json.loads(raw)

            if "availability" not in cfg_new:
                raise ValueError("Config must include 'availability'.")

            # Preserve fixed events if missing (avoid accidental wipe)
            if "fixed_events" not in cfg_new:
                cfg_new["fixed_events"] = self.cfg.get("fixed_events", []) or []

            # Backup current config
            try:
                p = config_path()
                if p.exists():
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    bak = p.with_name(p.stem + f".bak_{ts}" + p.suffix)
                    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

            self.cfg = cfg_new
            save_config(self.cfg)
            self.auto_var.set(bool(self.cfg.get("auto_refresh", True)))
            self.refresh_fixed_table()
            self.refresh_and_reschedule()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def refresh_fixed_table(self):
        for item in self.fixed_tree.get_children():
            self.fixed_tree.delete(item)
        for ev in self.cfg.get("fixed_events", []) or []:
            self.fixed_tree.insert(
                "",
                "end",
                values=(ev.get("date", ""), ev.get("start", ""), ev.get("end", ""), ev.get("title", "")),
            )

    def add_fixed_event(self):
        try:
            d = simple_ask("Date (YYYY-MM-DD)", default=datetime.now().date().isoformat())
            st = simple_ask("Start (HH:MM)", default="18:00")
            en = simple_ask("End (HH:MM)", default="20:00")
            title = simple_ask("Title", default="Interview / Exam")

            _ = date.fromisoformat(d)
            _ = parse_hhmm(st)
            _ = parse_hhmm(en)
            if dt_combine(date.fromisoformat(d), parse_hhmm(en)) <= dt_combine(date.fromisoformat(d), parse_hhmm(st)):
                raise ValueError("End must be after start.")

            self.cfg.setdefault("fixed_events", []).append({"date": d, "start": st, "end": en, "title": title})
            save_config(self.cfg)
            self.refresh_fixed_table()
            self.refresh_and_reschedule()
        except Exception as e:
            messagebox.showerror("Add failed", str(e))

    def delete_fixed_event(self):
        sel = self.fixed_tree.selection()
        if not sel:
            return
        to_delete = set()
        for iid in sel:
            vals = self.fixed_tree.item(iid, "values")
            to_delete.add(tuple(vals))

        new_events = []
        for ev in self.cfg.get("fixed_events", []) or []:
            tup = (ev.get("date", ""), ev.get("start", ""), ev.get("end", ""), ev.get("title", ""))
            if tup not in to_delete:
                new_events.append(ev)

        self.cfg["fixed_events"] = new_events
        save_config(self.cfg)
        self.refresh_fixed_table()
        self.refresh_and_reschedule()

    def refresh_and_reschedule(self, silent: bool = False):
        try:
            tm_path = Path(self.tm_path_var.get()).expanduser()
            if not tm_path.exists():
                raise FileNotFoundError(f"tm_data.json not found:\n{tm_path}")

            data = load_json(tm_path)
            todos = data.get("todos", []) or []

            block_minutes = int(self.cfg.get("block_minutes", 60))
            pomos_per_block = int(self.cfg.get("pomos_per_block", 2))
            if pomos_per_block <= 0:
                pomos_per_block = 2

            horizon_days = int(self.cfg.get("horizon_days", 21))
            mix = bool(self.cfg.get("mix_categories", True))

            leisure_cats = set(self.cfg.get("leisure_categories", []) or [])

            # teacher constraint (optional)
            teacher_req_cats = set(self.cfg.get("teacher_required_categories", []) or [])
            teacher_un = self.cfg.get("teacher_unavailable", {}) or {}
            teacher_s = date.fromisoformat(teacher_un["start"]) if teacher_un.get("start") else None

            start_dt = ceil_to_next_hour(datetime.now())

            # Build tasks from tm_data todos
            tasks: List[Dict[str, Any]] = []
            for t in todos:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("title", "")).strip()
                if not title:
                    continue

                cat = str(t.get("category", "")).strip()
                due = str(t.get("due", "")).strip()

                is_done = bool(t.get("done", False))
                planned = safe_int(t.get("pomodoros_planned", 0), 0)
                done_p = safe_int(t.get("pomodoros_done", 0), 0)

                remaining_pomos = 0 if is_done else max(planned - done_p, 0)

                # teacher constraint: categories must finish before teacher start
                eff_due = due
                if (not is_done) and teacher_s and (cat in teacher_req_cats):
                    try:
                        if not eff_due:
                            eff_due = (teacher_s - timedelta(days=1)).isoformat()
                        else:
                            d_due = date.fromisoformat(eff_due)
                            if d_due >= teacher_s:
                                eff_due = (teacher_s - timedelta(days=1)).isoformat()
                    except Exception:
                        pass

                remaining_blocks = (remaining_pomos + pomos_per_block - 1) // pomos_per_block if remaining_pomos > 0 else 0

                kind = "leisure" if (cat in leisure_cats) else "work"

                tasks.append(
                    {
                        "title": title,
                        "category": cat,
                        "due": eff_due,
                        "kind": kind,
                        "priority": 3,
                        "remaining_blocks": int(remaining_blocks),
                        "is_deadline": bool(eff_due),
                    }
                )

            availability = self.cfg.get("availability", {}) or {}
            fixed_events = self.cfg.get("fixed_events", []) or []

            # ---- Work scheduling with protected reserve ----
            deadline_work = [t for t in tasks if t["kind"] == "work" and t.get("is_deadline") and t.get("remaining_blocks", 0) > 0]
            nondeadline_work = [t for t in tasks if t["kind"] == "work" and (not t.get("is_deadline")) and t.get("remaining_blocks", 0) > 0]

            # 1) schedule DDL work WITHOUT using reserve
            ddl_schedule, ddl_overflow = schedule_kind(
                deadline_work,
                availability,
                fixed_events,
                reserve_as_blocks=True,
                cfg=self.cfg,
                start_dt=start_dt,
                horizon_days=horizon_days,
                block_minutes=block_minutes,
                mix_categories=mix,
                kind="work",
            )

            # 2) if still overflow, re-run allowing DDL work to borrow reserve
            if ddl_overflow:
                # rebuild snapshot
                deadline_work = [t for t in tasks if t["kind"] == "work" and t.get("is_deadline") and t.get("remaining_blocks", 0) > 0]
                ddl_schedule, ddl_overflow = schedule_kind(
                    deadline_work,
                    availability,
                    fixed_events,
                    reserve_as_blocks=False,
                    cfg=self.cfg,
                    start_dt=start_dt,
                    horizon_days=horizon_days,
                    block_minutes=block_minutes,
                    mix_categories=mix,
                    kind="work",
                )

            occ_after_ddl = occupied_map_from_schedule(ddl_schedule)

            # 3) schedule non-DDL work with reserve ALWAYS protected
            nonddl_schedule, nonddl_overflow = schedule_kind(
                nondeadline_work,
                availability,
                fixed_events,
                reserve_as_blocks=True,
                cfg=self.cfg,
                start_dt=start_dt,
                horizon_days=horizon_days,
                block_minutes=block_minutes,
                mix_categories=mix,
                kind="work",
                occupied=occ_after_ddl,
            )

            work_schedule = ddl_schedule + nonddl_schedule
            work_overflow = ddl_overflow + nonddl_overflow

            # Leisure tasks: only if all work fits
            final_schedule = work_schedule[:]
            leisure_overflow: List[Dict[str, Any]] = []
            if not work_overflow:
                occ_work = occupied_map_from_schedule(work_schedule)
                leisure_schedule, leisure_overflow = schedule_kind(
                    tasks,
                    availability,
                    fixed_events,
                    reserve_as_blocks=False,
                    cfg=self.cfg,
                    start_dt=start_dt,
                    horizon_days=horizon_days,
                    block_minutes=block_minutes,
                    mix_categories=mix,
                    kind="leisure",
                    occupied=occ_work,
                )
                final_schedule += leisure_schedule

            # ---- Build display rows: day separator + fixed + reserve + schedule (interleaved by time) ----
            rows: List[Tuple[str, str, str, str, str, str, str]] = []
            days = sorted(
                {s["date"] for s in final_schedule}
                | {ev.get("date", "") for ev in fixed_events if ev.get("date")}
                | {(start_dt.date() + timedelta(days=i)).isoformat() for i in range(horizon_days)}
            )

            scheduled_intervals = occupied_map_from_schedule(final_schedule)

            for dstr in days:
                rows.append((dstr, "", "", "", "", "", "daysep"))
                try:
                    d0 = date.fromisoformat(dstr)
                except Exception:
                    continue

                items: List[Dict[str, Any]] = []

                fixed = fixed_blocks_for_day(d0, fixed_events)
                for st, en, title in fixed:
                    if d0 == start_dt.date() and en <= start_dt:
                        continue
                    items.append(
                        {
                            "_st": st,
                            "start": st.strftime("%H:%M"),
                            "_en": en,
                            "end": en.strftime("%H:%M"),
                            "kind": "fixed",
                            "category": "Fixed",
                            "title": title,
                            "tag": "fixed",
                        }
                    )

                # reserve: show only if overlaps availability AND not overlapped by fixed/scheduled
                reserve = reserve_blocks_for_day(d0, self.cfg, start_dt)
                day_av = build_daily_slots(d0, availability, start_dt=start_dt if d0 == start_dt.date() else None)
                for rs, re, rtitle in reserve:
                    if not any(overlaps((rs, re), av) for av in day_av):
                        continue
                    occs = [(a, b) for a, b, _ in fixed] + scheduled_intervals.get(dstr, [])
                    if any(overlaps((rs, re), ob) for ob in occs):
                        continue
                    items.append(
                        {
                            "_st": rs,
                            "start": rs.strftime("%H:%M"),
                            "_en": re,
                            "end": re.strftime("%H:%M"),
                            "kind": "reserve",
                            "category": "Leisure",
                            "title": rtitle,
                            "tag": "reserve",
                        }
                    )

                for s in [x for x in final_schedule if x["date"] == dstr]:
                    dd = date.fromisoformat(s["date"])
                    st_dt = dt_combine(dd, parse_hhmm(s["start"]))
                    en_dt = dt_combine(dd, parse_hhmm(s["end"]))
                    if en_dt <= start_dt:
                        continue
                    tag = "leisure" if s["kind"] == "leisure" else ""
                    items.append(
                        {
                            "_st": st_dt,
                            "start": s["start"],
                            "_en": en_dt,
                            "end": s["end"],
                            "kind": s["kind"],
                            "category": s.get("category", ""),
                            "title": s["title"],
                            "tag": tag,
                        }
                    )

                def pri(k: str) -> int:
                    return 0 if k == "fixed" else (1 if k == "reserve" else 2)

                items.sort(key=lambda x: (x["_st"], pri(x["kind"])))

                for it in items:
                    rows.append((dstr, it["start"], it["end"], it["kind"], it["category"], it["title"], it["tag"]))

            # Render tree
            for item in self.sched_tree.get_children():
                self.sched_tree.delete(item)

            for d0, st, en, kind, cat, title, tag in rows:
                values = (d0, st, en, kind, cat, title)
                if tag:
                    self.sched_tree.insert("", "end", values=values, tags=(tag,))
                else:
                    self.sched_tree.insert("", "end", values=values)

            now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = (
                f"Rescheduled @ {now_s}. Start={start_dt.strftime('%Y-%m-%d %H:%M')}, "
                f"Block={block_minutes}min (~{pomos_per_block} pomos). Horizon={horizon_days}d. "
                f"Work overflow={len(work_overflow)}"
            )
            if work_overflow:
                msg += f" (DDL overflow={len(ddl_overflow)}, non-DDL overflow={len(nonddl_overflow)})"
            else:
                msg += f", Leisure overflow={len(leisure_overflow)}"
            self.status_var.set(msg)

        except Exception as e:
            self.status_var.set(f"Error: {e}")
            if not silent:
                messagebox.showerror("Reschedule failed", str(e))

    def export_csv(self):
        try:
            out = Path(__file__).with_name("schedule_export.csv")
            lines = ["date,start,end,kind,category,title"]
            for iid in self.sched_tree.get_children():
                d0, st, en, kind, cat, title = self.sched_tree.item(iid, "values")
                if st == "" and en == "" and kind == "" and title == "":
                    continue
                title = str(title).replace('"', '""')
                cat = str(cat).replace('"', '""')
                lines.append(f"{d0},{st},{en},{kind},\"{cat}\",\"{title}\"")
            out.write_text("\n".join(lines), encoding="utf-8")
            messagebox.showinfo("Exported", f"Saved:\n{out}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))


def simple_ask(prompt: str, default: str = "") -> str:
    from tkinter import simpledialog

    ans = simpledialog.askstring("Input", f"{prompt}:", initialvalue=default)
    if ans is None:
        raise ValueError("Cancelled")
    return ans.strip()


if __name__ == "__main__":
    app = AutoSchedulerApp()
    app.mainloop()
