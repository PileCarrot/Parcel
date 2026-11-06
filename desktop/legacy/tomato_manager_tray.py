# -*- coding: utf-8 -*-
import os, sys, json, math, datetime, threading
import tkinter as tk
from tkinter import ttk, messagebox as mb, simpledialog as sd

def resource_path(relative):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative)

def appdata_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "TomatoManager")
    else:
        base = os.path.expanduser("~/.local/share")
        path = os.path.join(base, "TomatoManager")
    os.makedirs(path, exist_ok=True)
    return path

DATA_FILE = os.path.join(appdata_dir(), "tm_data.json")

DEFAULT_DATA = {
    "rules": {"work":25,"rest":5,"long_rest":15,"long_every":4,"auto_next":False,"force_note":False},
    "rule_notes": [], "todos": [], "history": [], "categories": ["作品集"]
}

def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k,v in DEFAULT_DATA.items():
        if k not in data:
            data[k] = json.loads(json.dumps(v))
    for t in data["todos"]:
        t.setdefault("pomodoros_done", 0); t.setdefault("done", False); t.setdefault("due","")
    return data

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def now_iso(): return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
def fmt_datetime(dt_iso):
    try:
        dt = datetime.datetime.strptime(dt_iso, "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception: return dt_iso
def days_to_due(due):
    if not due: return None
    try:
        d = datetime.datetime.strptime(due, "%Y-%m-%d").date()
        return (d - datetime.date.today()).days
    except Exception: return None

class RulesDialog(tk.Toplevel):
    def __init__(self, master, rules):
        super().__init__(master)
        self.title("计时设置"); self.configure(bg="#ffffff"); self.resizable(False, False)
        self.result = None
        frm = tk.Frame(self, bg="#ffffff"); frm.pack(padx=12, pady=12)
        labels = ["工作时长(分钟)","休息时长(分钟)","长休(分钟)","每几轮长休","自动开始下一阶段","休息时强制填写备注"]
        for i, text in enumerate(labels):
            tk.Label(frm, text=text, bg="#ffffff").grid(row=i, column=0, sticky="e", pady=4)
        self.e_work = tk.Entry(frm, width=8); self.e_work.insert(0, str(rules.get("work",25)))
        self.e_rest = tk.Entry(frm, width=8); self.e_rest.insert(0, str(rules.get("rest",5)))
        self.e_lrest= tk.Entry(frm, width=8); self.e_lrest.insert(0, str(rules.get("long_rest",15)))
        self.e_levery=tk.Entry(frm, width=8); self.e_levery.insert(0, str(rules.get("long_every",4)))
        self.v_auto = tk.BooleanVar(value=bool(rules.get("auto_next", False)))
        self.v_force= tk.BooleanVar(value=bool(rules.get("force_note", False)))
        self.e_work.grid(row=0,column=1,sticky="w"); self.e_rest.grid(row=1,column=1,sticky="w")
        self.e_lrest.grid(row=2,column=1,sticky="w"); self.e_levery.grid(row=3,column=1,sticky="w")
        tk.Checkbutton(frm, text="", variable=self.v_auto, bg="#ffffff").grid(row=4, column=1, sticky="w")
        tk.Checkbutton(frm, text="", variable=self.v_force, bg="#ffffff").grid(row=5, column=1, sticky="w")
        btns = tk.Frame(self, bg="#ffffff"); btns.pack(pady=(8,0))
        tk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        self.grab_set()
    def ok(self):
        try:
            r = {
                "work": max(1, int(self.e_work.get() or "25")),
                "rest": max(1, int(self.e_rest.get() or "5")),
                "long_rest": max(1, int(self.e_lrest.get() or "15")),
                "long_every": max(1, int(self.e_levery.get() or "4")),
                "auto_next": bool(self.v_auto.get()),
                "force_note": bool(self.v_force.get())
            }
        except Exception:
            tk.messagebox.showwarning("提示","请填写有效数字"); return
        self.result = r; self.destroy()

class RuleNotesWindow(tk.Toplevel):
    def __init__(self, master, data):
        super().__init__(master)
        self.title("工作规则（只能追加）"); self.configure(bg="#ffffff"); self.resizable(False, False)
        self.data = data
        tk.Label(self, text="已经记录的规则（只读）：", bg="#ffffff").pack(anchor="w", padx=12, pady=(10,4))
        frame = tk.Frame(self, bg="#ffffff"); frame.pack(fill="both", expand=True, padx=12)
        self.listbox = tk.Listbox(frame, width=56, height=12); self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(frame); sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set); sb.config(command=self.listbox.yview)
        for r in self.data.get("rule_notes", []):
            ts = r.get("ts",""); text = r.get("text","")
            self.listbox.insert("end", f"{ts} · {text}")
        tk.Label(self, text="追加一条新规则：", bg="#ffffff").pack(anchor="w", padx=12, pady=(8,2))
        add_row = tk.Frame(self, bg="#ffffff"); add_row.pack(fill="x", padx=12, pady=(0,12))
        self.entry = tk.Entry(add_row, width=42); self.entry.pack(side="left", fill="x", expand=True)
        tk.Button(add_row, text="追加", command=self.append_rule).pack(side="left", padx=6)
        btns = tk.Frame(self, bg="#ffffff"); btns.pack(pady=(0,10)); tk.Button(btns, text="关闭", command=self.destroy).pack()
        self.grab_set()
    def append_rule(self):
        text = self.entry.get().strip()
        if not text: tk.messagebox.showinfo("提示","请输入一条规则文本"); return
        rec = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text}
        self.data.setdefault("rule_notes", []).append(rec); save_data(self.data)
        self.listbox.insert("end", f"{rec['ts']} · {rec['text']}"); self.entry.delete(0, "end")

class App(tk.Tk):
    def __init__(self, minimize_to_tray=True):
        super().__init__()
        self.title("番茄时间管理")
        self.geometry("900x640"); self.configure(bg="#f6f6f6")
        self.data = load_data()

        # top bar
        top = tk.Frame(self, bg="#f6f6f6"); top.pack(fill="x", padx=12, pady=(10,8))
        tk.Button(top, text="打开番茄钟", command=self.start_timer_flow).pack(side="left")
        tk.Button(top, text="新增待办", command=self.add_todo).pack(side="left", padx=8)
        tk.Button(top, text="新增大类", command=self.add_category).pack(side="left", padx=8)
        tk.Button(top, text="计时设置", command=self.edit_rules).pack(side="left", padx=8)

        self.port_var = tk.StringVar(); self.update_portfolio_summary()
        tk.Label(top, textvariable=self.port_var, bg="#f6f6f6", fg="#111827", font=("Microsoft YaHei UI", 10, "bold")).pack(side="right")

        self._summary_host = tk.Frame(self, bg="#f6f6f6"); self._summary_host.pack(fill="x", padx=12, pady=(0,8))
        self.cp_var = tk.StringVar(); tk.Label(self._summary_host, textvariable=self.cp_var, bg="#f6f6f6", fg="#111827", font=("Microsoft YaHei UI", 10)).pack(anchor="w")
        self.daily_info_var = tk.StringVar(); tk.Label(self._summary_host, textvariable=self.daily_info_var, bg="#f6f6f6", fg="#6b7280").pack(anchor="w", pady=(2,0))
        self.daily_pb = ttk.Progressbar(self._summary_host, orient="horizontal", mode="determinate", length=360, maximum=50); self.daily_pb.pack(anchor="w", pady=(2,6))

        main = tk.PanedWindow(self, sashwidth=6, sashrelief="ridge", bg="#dddddd"); main.pack(fill="both", expand=True, padx=12, pady=(0,12))
        left = tk.Frame(main, bg="#ffffff", bd=1, relief="solid"); right = tk.Frame(main, bg="#ffffff", bd=1, relief="solid")
        main.add(left, width=500); main.add(right)

        tk.Label(left, text="待办事项", bg="#ffffff", anchor="w", font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", padx=10, pady=(8,4))
        self.todo_frame = tk.Frame(left, bg="#ffffff"); self.todo_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.todo_canvas = tk.Canvas(self.todo_frame, bg="#ffffff", highlightthickness=0); self.todo_scroll = tk.Scrollbar(self.todo_frame, orient="vertical", command=self.todo_canvas.yview)
        self.todo_list = tk.Frame(self.todo_canvas, bg="#ffffff")
        self.todo_list.bind("<Configure>", lambda e: self.todo_canvas.configure(scrollregion=self.todo_canvas.bbox("all")))
        self.todo_canvas.create_window((0,0), window=self.todo_list, anchor="nw"); self.todo_canvas.configure(yscrollcommand=self.todo_scroll.set)
        self.todo_canvas.pack(side="left", fill="both", expand=True); self.todo_scroll.pack(side="right", fill="y")

        tk.Label(right, text="历史记录", bg="#ffffff", anchor="w", font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", padx=10, pady=(8,4))
        self.hist_frame = tk.Frame(right, bg="#ffffff"); self.hist_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.hist_canvas = tk.Canvas(self.hist_frame, bg="#ffffff", highlightthickness=0); self.hist_scroll = tk.Scrollbar(self.hist_frame, orient="vertical", command=self.hist_canvas.yview)
        self.hist_list = tk.Frame(self.hist_canvas, bg="#ffffff")
        self.hist_list.bind("<Configure>", lambda e: self.hist_canvas.configure(scrollregion=self.hist_canvas.bbox("all")))
        self.hist_canvas.create_window((0,0), window=self.hist_list, anchor="nw"); self.hist_canvas.configure(yscrollcommand=self.hist_scroll.set)
        self.hist_canvas.pack(side="left", fill="both", expand=True); self.hist_scroll.pack(side="right", fill="y")

        self.refresh_todos(); self.refresh_history(); self._update_summaries()

        self.withdraw()  # start minimized
        if minimize_to_tray: self._install_tray()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _get_portfolio_work_records(self):
        recs = []
        for rec in self.data.get("history", []):
            if rec.get("type") != "work": continue
            tid = rec.get("todo_id"); 
            if not tid: continue
            t = next((x for x in self.data.get("todos", []) if x.get("id")==tid), None)
            if t and t.get("category")=="作品集": recs.append(rec)
        return recs

    def _compute_total_savings(self):
        recs = self._get_portfolio_work_records(); day_counts = {}
        for rec in recs:
            ts = rec.get("end") or rec.get("start") or ""; day = (ts[:10] if ts else "")
            if not day: continue
            day_counts[day] = day_counts.get(day, 0) + 1
        total_pomos = sum(day_counts.values()); bonus_days = sum(1 for c in day_counts.values() if c >= 8)
        return 2 * total_pomos + 8 * bonus_days

    def _compute_today_stats(self):
        today = datetime.date.today().strftime("%Y-%m-%d")
        recs = self._get_portfolio_work_records(); n_today = 0
        for rec in recs:
            ts = rec.get("end") or rec.get("start") or ""
            if ts.startswith(today): n_today += 1
        today_money = 2 * n_today + (8 if n_today >= 8 else 0)
        return n_today, today_money, min(50, today_money)

    def update_portfolio_summary(self):
        hours = 0.0
        for t in self.data.get("todos", []):
            if t.get("category") == "作品集": hours += (t.get("pomodoros_done", 0) / 2.0)
        self.port_var.set(f"作品集累计已完成：{hours:.1f} 小时")

    def _update_summaries(self):
        today = datetime.date.today(); cp_date = datetime.date(2025, 12, 27)
        days_left = (cp_date - today).days; total_money = self._compute_total_savings()
        n_today, today_money, today_money_clamped = self._compute_today_stats()
        self.cp_var.set(f"cp32倒计时 {max(0, days_left)} 天，已经攒钱 {total_money} 元吗？（每满4h可再加8元）")
        self.daily_info_var.set(f"今天：{n_today} 个作品集番茄，¥{today_money_clamped} / ¥50"); self.daily_pb['value'] = today_money_clamped

    def edit_rules(self):
        d = RulesDialog(self, self.data["rules"]); self.wait_window(d)
        if d.result: self.data["rules"].update(d.result); save_data(self.data)

    def add_category(self):
        name = sd.askstring("新增大类", "输入新大类名称：")
        if not name: return
        name = name.strip()
        if not name: return
        if name in self.data.get("categories", []):
            mb.showinfo("提示", f"大类「{name}」已存在。"); return
        self.data.setdefault("categories", []).append(name); save_data(self.data); mb.showinfo("已添加", f"已新增大类：{name}")

    def add_todo(self):
        d = TodoDialog(self, self.data.get("categories", [])); self.wait_window(d)
        if d.result:
            new = d.result
            if new["category"] not in self.data.get("categories", []):
                self.data.setdefault("categories", []).append(new["category"])
            new["id"] = "t" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"); new["pomodoros_done"] = 0; new["done"] = False
            self.data["todos"].append(new); save_data(self.data); self.refresh_todos()

    def edit_todo(self, todo):
        d = TodoDialog(self, self.data.get("categories", []), initial=todo); self.wait_window(d)
        if d.result:
            if d.result["category"] not in self.data.get("categories", []):
                self.data.setdefault("categories", []).append(d.result["category"])
            todo.update(d.result); todo["done"] = todo["pomodoros_done"] >= todo["pomodoros_planned"]
            save_data(self.data); self.refresh_todos()

    def delete_todo(self, todo):
        if not mb.askyesno("删除确认", f'删除待办：{todo.get("title","")} ？'): return
        self.data["todos"].remove(todo); save_data(self.data); self.refresh_todos()

    def start_timer_flow(self):
        picker = TaskPicker(self, self.data["todos"]); self.wait_window(picker); sel = picker.result
        if not sel: return
        FloatPomodoro(self, self.data["rules"], self.data, todo=sel, on_work_complete=self.on_work_complete)

    def on_work_complete(self, todo, start_iso, end_iso, note):
        rec = {"id":"h"+datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"),
               "type":"work",
               "todo_id": todo["id"] if todo else None,
               "start": start_iso,
               "end": end_iso,
               "note": note or ""}
        self.data["history"].append(rec)
        if todo is not None:
            todo["pomodoros_done"] = todo.get("pomodoros_done",0) + 1
            todo["done"] = todo["pomodoros_done"] >= todo.get("pomodoros_planned",1)
        save_data(self.data); self.refresh_todos(); self.refresh_history(); self.update_portfolio_summary(); self._update_summaries()

    def on_rest_logged(self, todo, note):
        rec = {"id":"h"+datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"), "type":"rest", "todo_id": todo["id"] if todo else None, "start":"", "end": now_iso(), "note": note or ""}
        self.data["history"].append(rec); save_data(self.data); self.refresh_history()

    def refresh_todos(self):
        for w in self.todo_list.winfo_children(): w.destroy()
        def key(todo):
            d = days_to_due(todo.get("due","")); d = 99999 if d is None else d
            return (todo.get("done",False), d)
        todos = sorted(self.data["todos"], key=key)
        for t in todos:
            row = tk.Frame(self.todo_list, bg="#ffffff", bd=0); row.pack(fill="x", pady=6)
            left = tk.Frame(row, bg="#ffffff"); left.pack(side="left", fill="x", expand=True)
            title = f'[{t.get("category","")}] {t.get("title","")}'
            tk.Label(left, text=title, bg="#ffffff", anchor="w", font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x")
            ddl = t.get("due","") or "无DDL"
            dleft = days_to_due(t.get("due","")); ddl_color = "#111827"
            if dleft is not None:
                if dleft < 0: ddl_color = "#ef4444"
                elif dleft <= 1: ddl_color = "#f59e0b"
            tk.Label(left, text=f"DDL：{ddl}", bg="#ffffff", fg=ddl_color).pack(anchor="w")
            planned = max(1, int(t.get("pomodoros_planned",1)))
            done = min(max(0, int(t.get("pomodoros_done",0))), planned)
            pb = ttk.Progressbar(left, orient="horizontal", mode="determinate", length=320); pb["maximum"] = planned; pb["value"] = done; pb.pack(fill="x", pady=(2,0))
            tk.Label(left, text=f"{done}/{planned} 完成", bg="#ffffff", fg="#6b7280").pack(anchor="w")
            ops = tk.Frame(row, bg="#ffffff"); ops.pack(side="right")
            tk.Button(ops, text="编辑", command=lambda t=t: self.edit_todo(t)).pack(side="top", pady=2)
            tk.Button(ops, text="删除", command=lambda t=t: self.delete_todo(t)).pack(side="top", pady=2)
        self.todo_list.update_idletasks(); self.todo_canvas.configure(scrollregion=self.todo_canvas.bbox("all"))

    def refresh_history(self):
        for w in self.hist_list.winfo_children(): w.destroy()
        # 仅显示工作记录，最近200条
        hist = [rec for rec in self.data.get("history", []) if rec.get("type")=="work"]
        hist.reverse(); hist = hist[:200]

        def parse_iso(iso):
            try: return datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")
            except Exception: return None

        for rec in hist:
            row = tk.Frame(self.hist_list, bg="#ffffff", bd=0); row.pack(fill="x", pady=4)
            ds = parse_iso(rec.get("start","")); de = parse_iso(rec.get("end",""))
            if ds and de:
                when = f'{ds.strftime("%m-%d")} {ds.strftime("%H:%M")}—{de.strftime("%H:%M")}'
            else:
                # 兼容旧数据：只有end
                when_raw = rec.get("end","")
                try:
                    de = datetime.datetime.strptime(when_raw, "%Y-%m-%dT%H:%M:%S")
                    when = f'{de.strftime("%m-%d %H:%M")}'
                except Exception:
                    when = when_raw or "时间未知"
            todo_title = ""
            if rec.get("todo_id"):
                t = next((x for x in self.data["todos"] if x.get("id")==rec["todo_id"]), None)
                if t: todo_title = f' - [{t.get("category","")}] {t.get("title","")}'

            note = rec.get("note",""); txt = f"{when} | 工作{todo_title}"
            tk.Label(row, text=txt, bg="#ffffff", anchor="w").pack(fill="x")
            if note: tk.Label(row, text=f"备注：{note}", bg="#ffffff", fg="#6b7280", anchor="w").pack(fill="x")

        self.hist_list.update_idletasks(); self.hist_canvas.configure(scrollregion=self.hist_canvas.bbox("all"))

    def _install_tray(self):
        from pystray import Icon, MenuItem, Menu
        from PIL import Image
        icon_path = resource_path('tomato.ico')
        try: image = Image.open(icon_path)
        except Exception:
            image = Image.new("RGBA", (64,64), (220,30,30,255))
        def on_show(icon, item=None):
            self.deiconify(); self.lift(); self.after(50, lambda: self.focus_force())
        def on_hide(icon, item=None): self.withdraw()
        def on_open_timer(icon, item=None):
            self.deiconify(); self.lift(); self.start_timer_flow()
        def on_quit(icon, item=None):
            icon.stop(); self.destroy()
        menu = Menu(MenuItem('显示窗口', on_show), MenuItem('隐藏窗口', on_hide), MenuItem('打开番茄钟', on_open_timer), MenuItem('退出', on_quit))
        self._tray_icon = Icon("TomatoManager", image, "番茄时间管理", menu)
        def tray_run(): self._tray_icon.run()
        threading.Thread(target=tray_run, daemon=True).start()

    def on_close(self): self.withdraw()

class TodoDialog(tk.Toplevel):
    def __init__(self, master, categories, initial=None):
        super().__init__(master)
        self.title("待办事项"); self.configure(bg="#ffffff"); self.resizable(False, False)
        self.result = None
        frm = tk.Frame(self, bg="#ffffff"); frm.pack(padx=12, pady=12)
        tk.Label(frm, text="大类", bg="#ffffff").grid(row=0,column=0,sticky="e", pady=4)
        tk.Label(frm, text="具体工作", bg="#ffffff").grid(row=1,column=0,sticky="e", pady=4)
        tk.Label(frm, text="DDL (YYYY-MM-DD，可空)", bg="#ffffff").grid(row=2,column=0,sticky="e", pady=4)
        tk.Label(frm, text="预计番茄数", bg="#ffffff").grid(row=3,column=0,sticky="e", pady=4)
        self.cat = tk.StringVar()
        self.cat_menu = ttk.Combobox(frm, values=categories, width=18, textvariable=self.cat, state="readonly"); self.cat_menu.grid(row=0,column=1,sticky="w")
        self.e_title = tk.Entry(frm, width=32); self.e_title.grid(row=1,column=1,sticky="w")
        self.e_due = tk.Entry(frm, width=18); self.e_due.grid(row=2,column=1,sticky="w")
        self.e_plan = tk.Entry(frm, width=8); self.e_plan.grid(row=3,column=1,sticky="w")
        if initial:
            self.cat.set(initial.get("category","")); self.e_title.insert(0, initial.get("title","")); self.e_due.insert(0, initial.get("due","")); self.e_plan.insert(0, str(initial.get("pomodoros_planned",1)))
        else:
            self.cat.set(categories[0] if categories else "通用"); self.e_plan.insert(0, "1")
        btns = tk.Frame(self, bg="#ffffff"); btns.pack(pady=(8,0))
        tk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        self.grab_set()
    def ok(self):
        cat = self.cat.get().strip() or "通用"; title = self.e_title.get().strip(); due = self.e_due.get().strip()
        try: plan = max(1, int(self.e_plan.get() or "1"))
        except Exception: mb.showwarning("提示","预计番茄数需为整数 ≥1"); return
        if not title: mb.showwarning("提示","请填写具体工作"); return
        self.result = {"category":cat,"title":title,"due":due,"pomodoros_planned":plan}; self.destroy()

class TaskPicker(tk.Toplevel):
    def __init__(self, master, todos):
        super().__init__(master)
        self.title("选择待办事项"); self.configure(bg="#ffffff"); self.resizable(False, False)
        self.result = None
        tk.Label(self, text="请选择一个未完成的待办（或点击随机）", bg="#ffffff").pack(padx=12, pady=(10,4))
        frame = tk.Frame(self, bg="#ffffff"); frame.pack(padx=12, pady=(0,10), fill="both", expand=True)
        self.listbox = tk.Listbox(frame, width=56, height=10); self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(frame); sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set); sb.config(command=self.listbox.yview)
        self.items = [t for t in todos if not t.get("done")]
        def weight(todo):
            d = days_to_due(todo.get("due","")); rem = todo.get("pomodoros_planned",1) - todo.get("pomodoros_done",0)
            base = 1 + max(0, rem); 
            if d is None: return base
            return base + max(0, 10 - min(10, d))
        self.items.sort(key=lambda t: (t.get("done"), weight(t)), reverse=True)
        for t in self.items:
            ddl = t.get("due","") or "无DDL"; rem = max(0, t.get("pomodoros_planned",1)-t.get("pomodoros_done",0))
            self.listbox.insert("end", f'[{t.get("category","")}] {t.get("title","")} | DDL:{ddl} | 剩余:{rem}')
        btns = tk.Frame(self, bg="#ffffff"); btns.pack(pady=(0,10))
        tk.Button(btns, text="确定", command=self.ok).pack(side="left", padx=6)
        tk.Button(btns, text="随机挑一个", command=self.pick_random).pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        self.grab_set()
    def ok(self):
        idx = self.listbox.curselection()
        if not idx: return
        self.result = self.items[idx[0]]; self.destroy()
    def pick_random(self):
        import random
        if not self.items: mb.showinfo("提示","暂无未完成待办"); return
        self.result = random.choice(self.items); self.destroy()

class FloatPomodoro(tk.Toplevel):
    def __init__(self, master, rules, data_ref, todo=None, on_work_complete=None, on_rest_logged=None):
        super().__init__(master)
        self.title("番茄钟"); self.attributes("-topmost", True)
        self.resizable(False, False); self.configure(bg="#ffffff")
        self.rules = rules; self.data_ref = data_ref; self.todo = todo
        self.on_work_complete = on_work_complete; self.on_rest_logged = on_rest_logged
        self.phase = "work"; self.round_count = 0
        self.left = self.rules.get("work",25) * 60; self.total = self.left
        self.after_id = None; self.running = False
        self.work_start_iso = None  # 记录工作阶段开始时间（ISO）
        self._drag = None
        def start_drag(e): self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())
        def do_drag(e):
            if self._drag: self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")
        def stop_drag(e): self._drag = None
        top = tk.Frame(self, bg="#ffffff"); top.pack(fill="x", padx=10, pady=(8,2))
        title = self.todo.get("title","临时番茄") if self.todo else "临时番茄"
        self.title_var = tk.StringVar(value=title)
        tk.Label(top, textvariable=self.title_var, bg="#ffffff", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        tk.Button(top, text="规则", command=self.open_rule_notes).pack(side="right", padx=4)
        self.canvas = tk.Canvas(self, width=220, height=220, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(padx=10, pady=4)
        self.canvas.bind("<ButtonPress-1>", start_drag); self.canvas.bind("<B1-Motion>", do_drag); self.canvas.bind("<ButtonRelease-1>", stop_drag)
        controls = tk.Frame(self, bg="#ffffff"); controls.pack(pady=(0,6))
        tk.Button(controls, text="开始", command=self.start).pack(side="left", padx=4)
        tk.Button(controls, text="暂停", command=self.pause).pack(side="left", padx=4)
        tk.Button(controls, text="重置", command=self.reset).pack(side="left", padx=4)
        self.note_var = tk.StringVar(); self.note_box = tk.Entry(self, textvariable=self.note_var, width=28); self.note_box.pack(padx=10, pady=(2,10))
        self.note_box.insert(0, "（工作备注，可选）")
        self.update_ring()
    def open_rule_notes(self): RuleNotesWindow(self, self.data_ref)
    def secs(self): return max(0, int(self.left))
    def update_ring(self):
        c=self.canvas; c.delete("all"); cx,cy,r = 110,110,90
        c.create_oval(cx-r, cy-r, cx+r, cy+r, outline="#e5e7eb", width=16)
        frac = 1.0 * self.left / max(1, self.total); extent = 360 * frac; color = "#3b82f6" if self.phase=="work" else "#10b981"
        c.create_arc(cx-r, cy-r, cx+r, cy+r, start=90, extent=-extent, style="arc", width=16, outline=color)
        m = self.secs() // 60; s = self.secs() % 60
        c.create_text(cx, cy, text=f"{m:02d}:{s:02d}", font=("Consolas", 28, "bold"), fill="#111827")
        c.create_text(cx, cy+34, text=("工作中" if self.phase=="work" else "休息中"), font=("Microsoft YaHei UI", 10), fill=color)
    def tick(self):
        if not self.running: return
        self.left -= 1
        if self.left <= 0:
            self.running = False
            if self.phase == "work":
                # Finish work session safely; pass start/end/note to callback
                try:
                    if self.on_work_complete:
                        end_iso = now_iso()
                        start_iso = self.work_start_iso or end_iso
                        note = (self.note_var.get() or "").strip()
                        self.on_work_complete(self.todo, start_iso, end_iso, note)
                except Exception as e:
                    try:
                        mb.showwarning("提示", f"保存工作记录出错：{e}")
                    except Exception:
                        pass
                finally:
                    self.work_start_iso = None

                # Switch to rest
                self.phase = "rest"
                self.left = self.rules.get("rest", 5) * 60
                self.total = self.left
                self.update_ring()
                if self.rules.get("auto_next", False):
                    self.start()
                else:
                    mb.showinfo("提示", "工作番茄完成，开始休息~")
            else:
                # Rest finished: do NOT log rest in history; only switch to next phase
                self.phase = "work"
                self.round_count += 1
                if self.rules.get("long_every", 4) > 0 and self.round_count % self.rules.get("long_every", 4) == 0:
                    self.left = self.rules.get("long_rest", 15) * 60
                    self.total = self.left
                    self.phase = "rest"
                else:
                    self.left = self.rules.get("work", 25) * 60
                    self.total = self.left
                self.update_ring()
                if self.rules.get("auto_next", False):
                    self.start()
                else:
                    mb.showinfo("提示", "休息结束，可以开始下一轮工作~")
            return
        self.update_ring(); self.after_id = self.after(1000, self.tick)
    def start(self):
        if self.after_id:
            try: self.after_cancel(self.after_id)
            except Exception: pass
            self.after_id = None
        if self.left <= 0:
            # At boundary, do not reset to full duration; let tick() finish
            self.left = 0
        if self.phase == "work" and not self.work_start_iso:
            self.work_start_iso = now_iso()
        self.running = True; self.tick()
    def pause(self):
        self.running = False
        if self.after_id:
            try: self.after_cancel(self.after_id)
            except Exception: pass
            self.after_id = None
    def reset(self):
        self.pause()
        if self.phase == "work": self.left = self.rules.get("work",25) * 60
        else: self.left = self.rules.get("rest",5) * 60
        self.total = self.left;
        # 非工作阶段时清理开始时间
        if self.phase != "work":
            self.work_start_iso = None
        self.update_ring()

# ---- Todo & Picker classes reused ----
# (Already defined above)

if __name__ == "__main__":
    app = App(minimize_to_tray=True); app.mainloop()
