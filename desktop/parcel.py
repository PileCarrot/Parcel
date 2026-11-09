"""Parcel: three-column Windows desktop task planner and floating Pomodoro."""
from __future__ import annotations

import copy
import ctypes
import json
import math
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from ctypes.wintypes import RECT
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk, messagebox as mb, simpledialog as sd, filedialog

from parcel_core import (BASE, POMODORO_MINUTES, CONTEXTS, ENERGY_STATES, WORK_STATES, ENERGY_WINDOW_MINUTES,
                         Store, Clock, now, iso, parse_dt, remaining, schedule, uid, hm,
                         quick_parse, suggest_task_type, current_preference, category_priority, next_half_hour_delay)
from parcel_notion import NotionSync, SyncError, reconcile
from parcel_bridge import Bridge

CREAM = '#F5F3EB'
PAPER = '#FFFEF9'
MINT = '#98DCCB'
PALE = '#E7F3EC'
GREEN = '#276451'
ORANGE = '#F47A40'
COURSE_ORANGE = '#E7A12B'
TEXT = '#293D36'
MUTED = '#7C847A'
LINE = '#DFE3D8'
RED = '#BE533A'
FONT = 'Microsoft YaHei UI'
ASSETS = Path(getattr(sys, '_MEIPASS', BASE)) / 'assets'


def frame(parent, bg=PAPER, **kw):
    return tk.Frame(parent, bg=bg, **kw)


def label(parent, text='', size=10, color=TEXT, bold=False, bg=None, **kw):
    return tk.Label(parent, text=text, font=(FONT, size, 'bold' if bold else 'normal'),
                    fg=color, bg=bg or parent.cget('bg'), **kw)


def button(parent, text, command, accent=False, subtle=False, small=False, **kw):
    bg = ORANGE if accent else (parent.cget('bg') if subtle else PALE)
    return tk.Button(parent, text=text, command=command, font=(FONT, 9 if small else 10),
                     bg=bg, fg=PAPER if accent else GREEN, activebackground=MINT,
                     activeforeground=TEXT, relief='flat', bd=0, cursor='hand2',
                     padx=6 if small else 12, pady=2 if small else 7, highlightthickness=0, **kw)


def line(parent):
    frame(parent, LINE, height=1).pack(fill='x', pady=8)


def entry(parent, value='', width=30):
    item = tk.Entry(parent, font=(FONT, 10), bg=PAPER, fg=TEXT, relief='solid', bd=1,
                    insertbackground=GREEN, width=width)
    item.insert(0, str(value))
    return item


def minutes_text(value):
    value = round(value, 1)
    return f'{value:g} 分钟' if value < 60 else f'{value/60:.1f} 小时'


def pomodoro_text(minutes):
    return f'{minutes / POMODORO_MINUTES:.2f}'.rstrip('0').rstrip('.')


def wrapping_label(parent, text, **kw):
    item = label(parent, text, wraplength=250, anchor='w', justify='left', **kw)
    def fit(event):
        if event.width > 1 and int(item.cget('wraplength')) != event.width:
            item.config(wraplength=event.width)
    item.bind('<Configure>', fit)
    return item


def main_window_bounds(window):
    work = RECT(0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    border = RECT()
    if sys.platform == 'win32':
        user32 = ctypes.windll.user32
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        user32.AdjustWindowRectEx(ctypes.byref(border), 0x00CF0000, False, 0)  # WS_OVERLAPPEDWINDOW
    work_width = work.right - work.left
    outer_width = round(work_width * .6)
    width = outer_width - (border.right - border.left)
    height = work.bottom - work.top - (border.bottom - border.top)
    x = work.left + (work_width - outer_width) // 2
    return width, height, x, work.top


class ScrollArea(tk.Frame):
    def __init__(self, parent, bg=PAPER):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.body = frame(self.canvas, bg)
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    @staticmethod
    def wheel(event):
        w = event.widget
        while w is not None:
            if isinstance(w, ScrollArea):
                w.canvas.yview_scroll(-int(event.delta/120) or (-1 if event.delta > 0 else 1), 'units')
                return 'break'
            w = getattr(w, 'master', None)


class Dialog(tk.Toplevel):
    def __init__(self, owner, title, width=560):
        super().__init__(owner)
        self.owner = owner
        self.title(title)
        self.configure(bg=CREAM)
        self.transient(owner)
        self.resizable(False, False)
        self.result = None
        self.body = frame(self, CREAM)
        self.body.pack(fill='both', expand=True, padx=24, pady=20)
        self.column = 0
        self.bind('<Escape>', lambda e: self.destroy())
        self.update_idletasks()
        x = max(0, owner.winfo_rootx() + (owner.winfo_width()-width)//2)
        self.geometry(f'+{x}+{max(30, owner.winfo_rooty()+65)}')
        self.grab_set()

    def field(self, text, value='', values=None, *, parent=None):
        row = self.column
        parent = self.body if parent is None else parent
        label(parent, text).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 16))
        w = ttk.Combobox(parent, values=values, font=(FONT, 10), width=37) if values is not None else entry(parent, value, 39)
        if values is not None:
            w.set(value)
        w.grid(row=row, column=1, sticky='ew', pady=5)
        self.column += 1
        return w

    def footer(self, save, text='保存', secondary=None):
        row = frame(self, CREAM)
        row.pack(fill='x', padx=24, pady=(0, 20))
        button(row, text, save, accent=True).pack(side='right')
        button(row, '取消', self.destroy, subtle=True).pack(side='right', padx=8)
        if secondary:
            button(row, *secondary, subtle=True).pack(side='left')
        return row


class TaskDialog(Dialog):
    AUTO_TYPE = '自动建议'

    def __init__(self, owner, data, task=None, initial=None):
        super().__init__(owner, '编辑待办' if task else '新增待办')
        self.data = data
        self.task = task
        self.delete_requested = False
        self.complete_requested = False
        t = task or initial or {}
        self.title_e = self.field('具体工作', t.get('title', ''))
        self.category = self.field('大类', t.get('category', data['categories'][0] if data['categories'] else '通用'), data['categories'])
        self.estimate = self.field('预计总番茄数', f'{t.get("estimated_minutes", 25) / POMODORO_MINUTES:.12g}')
        self.due = self.field('截止日期（可留空）', t.get('due', ''))
        default_kind = '普通' if task else self.AUTO_TYPE
        self.kind = self.field('任务类型', t.get('task_type', default_kind),
                               [self.AUTO_TYPE, '普通', '输入', '输出', '低负荷', '休闲'])
        self.kind.configure(state='readonly')
        self.kind_hint = label(self.body, '', size=9, color=MUTED)
        self.kind_hint.grid(row=self.column, column=1, sticky='w')
        self.column += 1
        self.energy = self.field('所需精力', t.get('energy', '正常'), ['低', '正常', '高'])
        extra_row = self.column
        extras = frame(self.body, CREAM)
        self.column += 2
        self.worked = self.field('已投入番茄数', f'{t.get("worked_minutes", 0) / POMODORO_MINUTES:.12g}', parent=extras)
        self.earliest = self.field('最早开始（可留空）', t.get('earliest', ''), parent=extras)
        self.default_minimum = min(25, t.get('estimated_minutes', 25))
        self.minimum = self.field('最短连续工作（分钟）', t.get('min_session', self.default_minimum), parent=extras)
        context = t.get('context') or '任意'
        options = CONTEXTS if context in CONTEXTS else (*CONTEXTS, context)
        self.context = self.field('地点 / 环境', context, options, parent=extras)
        self.context.configure(state='readonly')
        self.description = self.field('任务描述（可留空）', t.get('description', ''), parent=extras)
        toggle = button(self.body, '其他设置 ▸', lambda: self.toggle_extras(extras, toggle), subtle=True)
        toggle.grid(row=extra_row, column=0, columnspan=2, sticky='w')
        extras.grid(row=extra_row+1, column=0, columnspan=2, sticky='w')
        extras.grid_remove()
        self.classification_text = []
        for widget in (self.title_e, self.description):
            value = tk.StringVar(self, value=widget.get())
            widget.configure(textvariable=value)
            value.trace_add('write', self.update_kind_hint)
            self.classification_text.append(value)
        self.update_kind_hint()
        label(self.body, '前置任务（按住 Ctrl 多选）').grid(row=self.column, column=0, sticky='nw', pady=5, padx=(0, 12))
        host = frame(self.body)
        host.grid(row=self.column, column=1, sticky='ew', pady=5)
        self.dependencies = tk.Listbox(host, selectmode='multiple', exportselection=False, height=4,
                                       width=38, font=(FONT, 9), bg=PAPER, fg=TEXT,
                                       selectbackground=MINT, selectforeground=TEXT)
        sb = ttk.Scrollbar(host, command=self.dependencies.yview)
        self.dependencies.configure(yscrollcommand=sb.set)
        self.dependencies.pack(side='left', fill='x', expand=True)
        sb.pack(side='right', fill='y')
        if task:
            self.options = [a for a in data['tasks'] if a['id'] != task['id']]
        else:
            self.options = [a for a in data['tasks'] if remaining(a) > 0]
        for i, a in enumerate(self.options):
            self.dependencies.insert('end', ('✓ ' if a.get('done') else '') + a['title'])
            if a['id'] in t.get('dependencies', []):
                self.dependencies.selection_set(i)
        self.column += 1
        label(self.body, '1 个番茄 = 25 分钟工作，休息另计。\n日期格式：2026-11-06 或 2026-11-06 18:00', size=9, color=MUTED, justify='left').grid(row=self.column, column=0, columnspan=2, sticky='w', pady=(7, 0))
        footer = self.footer(self.save, secondary=('删除待办', self.request_delete) if task else None)
        if task and remaining(task) > 0:
            button(footer, '已完成', self.request_complete).pack(side='left', padx=8)
        self.title_e.focus_set()

    def update_kind_hint(self, *args):
        suggestion = suggest_task_type(self.title_e.get(), self.description.get())
        if suggestion:
            hint = f'本地建议：{suggestion}'
        else:
            hint = '类型不明确，自动建议将使用「普通」'
        self.kind_hint.configure(text=hint)

    @staticmethod
    def toggle_extras(extras, toggle):
        if extras.winfo_manager():
            extras.grid_remove()
            toggle.configure(text='其他设置 ▸')
        else:
            extras.grid()
            toggle.configure(text='其他设置 ▾')

    def request_complete(self):
        self.complete_requested = True
        self.destroy()

    def request_delete(self):
        self.delete_requested = True
        self.destroy()

    def save(self):
        try:
            title = self.title_e.get().strip()
            if not title:
                raise ValueError('请填写具体工作')
            estimate = round(float(self.estimate.get()) * POMODORO_MINUTES, 4)
            worked = round(float(self.worked.get()) * POMODORO_MINUTES, 4)
            minimum = float(self.minimum.get())
            if not self.task and minimum == self.default_minimum:
                minimum = min(minimum, estimate)
            if not all(map(math.isfinite, (estimate, worked, minimum))) or estimate <= 0 or worked < 0 or not .1 <= minimum <= estimate:
                raise ValueError('总工时须大于 0，已投入不能小于 0；最短时长须大于 0 且不超过总工时')
            due, earliest = self.due.get().strip(), self.earliest.get().strip()
            deadline, release = parse_dt(due, True), parse_dt(earliest)
            if deadline and release and release >= deadline:
                raise ValueError('最早开始必须早于截止日期')
            deps = [self.options[i]['id'] for i in self.dependencies.curselection()]
            if self.task:
                graph = {a['id']: a.get('dependencies', []) for a in self.data['tasks']}
                graph[self.task['id']] = deps
                def reaches(k, seen):
                    if k == self.task['id']:
                        return True
                    if k in seen:
                        return False
                    return any(reaches(d, seen | {k}) for d in graph.get(k, []))
                if any(reaches(k, set()) for k in deps):
                    raise ValueError('这些前置任务会形成循环依赖')
            already_done = bool(self.task and self.task.get('done'))
            description = self.description.get().strip()
            kind = self.kind.get()
            if kind == self.AUTO_TYPE:
                kind = suggest_task_type(title, description) or '普通'
            self.result = dict(title=title, category=self.category.get().strip() or '通用',
                               estimated_minutes=estimate, worked_minutes=worked, min_session=minimum,
                               due=due, earliest=earliest, dependencies=deps,
                               task_type=kind, energy=self.energy.get(),
                               context='' if self.context.get() == '任意' else self.context.get(),
                               description=description, done=already_done or worked >= estimate)
            self.destroy()
        except ValueError as exc:
            mb.showwarning('检查任务', str(exc), parent=self)


class SettingsDialog(Dialog):
    def __init__(self, owner, cfg):
        super().__init__(owner, '计时与排程设置', 680)
        self.cfg = copy.deepcopy(cfg)
        book = ttk.Notebook(self.body)
        book.pack()
        timer, planner, avail = [frame(book, CREAM) for _ in range(3)]
        for panel, name in zip([timer, planner, avail], ['番茄钟', '排程', '每周可用时间']):
            book.add(panel, text=name, padding=15)
        self.fields = {}
        for i, (key, name) in enumerate([('work', '工作时长（分钟）'), ('rest', '短休息（分钟）'), ('long_rest', '长休息（分钟）'), ('long_every', '每几轮长休息')]):
            label(timer, name).grid(row=i, column=0, sticky='w', pady=8, padx=(0, 24))
            self.fields[key] = entry(timer, cfg[key], 14)
            self.fields[key].grid(row=i, column=1, sticky='w')
        self.auto = tk.BooleanVar(value=cfg['auto_next'])
        self.force_note = tk.BooleanVar(value=cfg.get('force_note', False))
        tk.Checkbutton(timer, text='自动开始休息和下一轮工作', variable=self.auto, bg=CREAM, font=(FONT, 10)).grid(row=4, column=0, columnspan=2, sticky='w', pady=8)
        tk.Checkbutton(timer, text='结束工作时要求填写备注', variable=self.force_note, bg=CREAM, font=(FONT, 10)).grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        label(timer, '计时设置从下一轮开始生效；暂停时间不计入工作。', size=9, color=MUTED).grid(row=6, column=0, columnspan=2, sticky='w', pady=10)
        label(planner, '滚动排程天数（1–90）').grid(row=0, column=0, sticky='w', pady=10)
        self.horizon = entry(planner, cfg['horizon_days'], 12)
        self.horizon.grid(row=0, column=1, padx=15)
        self.reserve = tk.BooleanVar(value=cfg.get('reserve_enabled', True))
        tk.Checkbutton(planner, text='保护每日休闲时间', variable=self.reserve, bg=CREAM, font=(FONT, 10)).grid(row=1, column=0, columnspan=2, sticky='w', pady=10)
        label(planner, '休闲时间开始 / 结束').grid(row=2, column=0, sticky='w')
        times = frame(planner, CREAM)
        times.grid(row=2, column=1, padx=15)
        self.rs = entry(times, cfg['reserve_start'], 6)
        self.rs.pack(side='left')
        self.re = entry(times, cfg['reserve_end'], 6)
        self.re.pack(side='left', padx=5)
        label(planner, '当前地点 / 环境').grid(row=3, column=0, sticky='w', pady=10)
        context = cfg.get('context') or '任意'
        contexts = CONTEXTS if context in CONTEXTS else (*CONTEXTS, context)
        self.context = ttk.Combobox(planner, values=contexts, state='readonly', width=17)
        self.context.set(context)
        self.context.grid(row=3, column=1, padx=15)
        label(planner, '地点已指定的任务只在相同环境下排程。\n截止日期无法满足时显示冲突，不占用休闲保护时间。\n应用时间：Europe/London（自动处理夏令时）。', size=9, color=MUTED, justify='left').grid(row=4, column=0, columnspan=2, sticky='w', pady=20)
        self.availability = []
        for i, name in enumerate(['周一', '周二', '周三', '周四', '周五', '周六', '周日']):
            label(avail, name).grid(row=i, column=0, padx=(0, 18), pady=8)
            w = entry(avail, ', '.join(f'{a}-{b}' for a,b in cfg['availability'].get(str(i), [])), 44)
            w.grid(row=i, column=1)
            self.availability.append(w)
        label(avail, '例如 08:00-12:00, 14:00-18:00；留空表示当天不可用。\n请将跨午夜时间拆成两天填写。', size=9, color=MUTED, justify='left').grid(row=7, column=0, columnspan=2, pady=10, sticky='w')
        self.footer(self.save)

    def save(self):
        try:
            for key, w in self.fields.items():
                value = int(w.get())
                if not 1 <= value <= (20 if key == 'long_every' else 480):
                    raise ValueError('计时长度应为 1–480 分钟，长休轮次应为 1–20')
                self.cfg[key] = value
            horizon = int(self.horizon.get())
            if not 1 <= horizon <= 90:
                raise ValueError('滚动排程天数应为 1–90')
            hm(self.rs.get())
            hm(self.re.get())
            if self.rs.get() >= self.re.get():
                raise ValueError('休闲时段结束应晚于开始')
            availability = {}
            for i, w in enumerate(self.availability):
                spans = []
                for text in w.get().replace('，', ',').split(','):
                    if not text.strip():
                        continue
                    a, b = [x.strip() for x in text.strip().split('-')]
                    start, end = hm(a), hm(b)
                    if end <= start:
                        raise ValueError('可用时间的结束必须晚于开始')
                    if any(max(start, hm(s)) < min(end, hm(e)) for s,e in spans):
                        raise ValueError('同一天的可用时段不能重叠')
                    spans.append([a, b])
                availability[str(i)] = spans
            self.cfg.update(horizon_days=horizon, auto_next=self.auto.get(), force_note=self.force_note.get(),
                            reserve_enabled=self.reserve.get(), reserve_start=self.rs.get(), reserve_end=self.re.get(),
                            context='' if self.context.get() == '任意' else self.context.get(), availability=availability)
            self.result = self.cfg
            self.destroy()
        except (ValueError, TypeError):
            mb.showwarning('检查设置', '请检查数值与时间格式。时段使用 HH:MM-HH:MM，多个时段用逗号分隔；结束应晚于开始，且不能重叠。', parent=self)


class EventDialog(Dialog):
    def __init__(self, owner, event=None):
        super().__init__(owner, '编辑固定日程' if event else '新增固定日程')
        e = event or {}
        self.name = self.field('日程名称', e.get('title', ''))
        self.day = self.field('日期（每周重复则留空）', e.get('date', str(now().date()) if not event else ''))
        self.start = self.field('开始时间', e.get('start', '09:00'))
        self.end = self.field('结束时间', e.get('end', '10:00'))
        self.week = self.field('每周重复（1=周一，逗号分隔）', ','.join(str(i+1) for i in e.get('weekdays', [])))
        label(self.body, '如结束早于开始，视为跨午夜。固定日程不会被自动移动。', size=9, color=MUTED).grid(row=self.column, column=0, columnspan=2, sticky='w', pady=12)
        self.footer(self.save)

    def save(self):
        try:
            if not self.name.get().strip():
                raise ValueError('请填写日程名称')
            day = self.day.get().strip()
            if day:
                day = str(parse_dt(day).date())
            hm(self.start.get())
            hm(self.end.get())
            if self.start.get() == self.end.get():
                raise ValueError('开始时间和结束时间不能相同')
            weekdays = [int(x.strip())-1 for x in self.week.get().replace('，', ',').split(',') if x.strip()]
            if any(i < 0 or i > 6 for i in weekdays) or (not day and not weekdays):
                raise ValueError('请填写日期或 1–7 的每周重复日期')
            self.result = dict(title=self.name.get().strip(), date=day, start=self.start.get(), end=self.end.get(), weekdays=weekdays)
            self.destroy()
        except ValueError as exc:
            mb.showwarning('检查日程', str(exc), parent=self)


class StartDialog(Dialog):
    def __init__(self, owner, task, block=None):
        super().__init__(owner, '开始这一次专注', 540)
        self.app = owner
        self.task = task
        self.block = block
        self.override = False
        self.headline = label(self.body, '选中的待办' if task and not block else '自动排程推荐', color=GREEN, size=10)
        self.headline.pack(anchor='w')
        self.task_label = label(self.body, task['title'] if task else '暂无可推荐任务', size=17, bold=True,
                                wraplength=450, justify='left')
        self.task_label.pack(anchor='w', pady=(12, 8))
        self.meta = label(self.body, '', color=MUTED, wraplength=450, justify='left')
        self.meta.pack(anchor='w', pady=(0, 15))
        r = frame(self.body, CREAM)
        r.pack(fill='x', pady=6)
        label(r, '本次专注（分钟）').pack(side='left')
        self.minutes = entry(r, 25, 10)
        self.minutes.pack(side='left', padx=15)
        self.update_task()
        row = frame(self, CREAM)
        row.pack(fill='x', padx=24, pady=(0, 22))
        button(row, '自定义任务', self.customize).pack(side='left')
        button(row, '确定开始', self.start, accent=True).pack(side='right')
        button(row, '取消', self.destroy, subtle=True).pack(side='right', padx=6)

    def update_task(self):
        if not self.task:
            self.meta.config(text='先新增待办，或点击“自定义任务”选择任务。')
            return
        self.task_label.config(text=self.task['title'])
        duration = min(self.app.cfg['work'], remaining(self.task))
        if self.block:
            duration = self.block.get('minutes', duration)
        duration = max(.1, duration)
        self.minutes.delete(0, 'end')
        self.minutes.insert(0, f'{duration:g}')
        detail = f'{self.task.get("category", "通用")}　剩余 {minutes_text(remaining(self.task))}'
        if self.block:
            detail += f'\n计划：{parse_dt(self.block["start"]):%m-%d %H:%M}–{parse_dt(self.block["end"]):%H:%M}'
        self.meta.config(text=detail)

    def customize(self):
        d = Dialog(self, '自定义任务', 530)
        items = [t for t in self.app.data['tasks'] if remaining(t) > 0]
        lb = tk.Listbox(d.body, width=58, height=12, font=(FONT, 10), bg=PAPER,
                        selectbackground=MINT, selectforeground=TEXT, exportselection=False)
        lb.pack()
        for t in items:
            lb.insert('end', f'[{t.get("category", "通用")}] {t["title"]}')
        if items:
            lb.selection_set(0)
        def choose():
            sel = lb.curselection()
            if not sel:
                return
            self.task, self.block, self.override = items[sel[0]], None, True
            self.headline.config(text='本次使用自定义任务')
            self.update_task()
            d.destroy()
        d.footer(choose, '使用这个任务')
        self.wait_window(d)
        if self.winfo_exists():
            self.grab_set()

    def start(self):
        try:
            if not self.task:
                raise ValueError('请先选择一个任务')
            mins = float(self.minutes.get())
            if not .1 <= mins <= 480:
                raise ValueError('本次时长应为 0.1–480 分钟')
            if mins > remaining(self.task) + .01:
                raise ValueError('本次时长超过任务剩余工时；请先编辑任务的预计工时')
            at, end = now(), now() + timedelta(minutes=mins)
            warnings = []
            if mins < self.task.get('min_session', 25):
                warnings.append('本次时长小于任务最短连续工作时长')
            if parse_dt(self.task.get('earliest')) and at < parse_dt(self.task['earliest']):
                warnings.append('任务尚未到最早开始时间')
            deadline = parse_dt(self.task.get('due'), True)
            if deadline and end > deadline:
                warnings.append('本次工作会超过任务截止日期')
            tasks = {t['id']: t for t in self.app.data['tasks']}
            if any(k not in tasks or remaining(tasks[k]) > 0 for k in self.task.get('dependencies', [])):
                warnings.append('前置任务尚未完成')
            for b in self.app.plan['blocks']:
                if b['kind'] in ('fixed', 'reserve') and max(at, parse_dt(b['start'])) < min(end, parse_dt(b['end'])):
                    warnings.append('本次工作与固定日程或休闲保护时间重叠')
                    break
            if warnings and not mb.askyesno('本次手动例外', '\n'.join(warnings) + '\n\n仍然开始本次专注？（仅影响本次，排程约束保持原设置）', parent=self):
                return
            self.result = (self.task, mins)
            self.destroy()
        except ValueError as exc:
            mb.showwarning('检查专注任务', str(exc), parent=self)


class FloatTimer(tk.Toplevel):
    def __init__(self, app, active):
        super().__init__(app)
        self.app = app
        self.active = active
        self.task = next((t for t in app.data['tasks'] if t['id'] == active['task_id']), None)
        self.clock = Clock(active['total_seconds'], elapsed=active.get('elapsed_seconds', 0))
        self.title('Parcel · 悬浮番茄钟')
        self.configure(bg=CREAM)
        self.attributes('-topmost', True)
        self.resizable(False, False)
        self.geometry(f'320x390+{max(20, self.winfo_screenwidth()-370)}+110')
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.last_draw_key = None
        self.last_save = -10
        self.phase_label = label(self, '', color=GREEN)
        self.phase_label.pack(anchor='w', padx=22, pady=(18, 5))
        self.task_label = label(self, self.task['title'] if self.task else '任务已移除', size=12, bold=True,
                                wraplength=278, justify='left')
        self.task_label.pack(anchor='w', padx=22)
        self.canvas = tk.Canvas(self, width=280, height=212, bg=CREAM, highlightthickness=0)
        self.canvas.pack(padx=20, pady=3)
        self.drag_offset = None
        self.canvas.bind('<ButtonPress-1>', lambda e: setattr(self, 'drag_offset', (e.x_root-self.winfo_x(), e.y_root-self.winfo_y())))
        self.canvas.bind('<B1-Motion>', self.drag)
        self.controls = frame(self, CREAM)
        self.controls.pack(fill='x', padx=20)
        self.toggle_button = button(self.controls, '暂停', self.toggle, accent=True)
        self.toggle_button.pack(side='left')
        button(self.controls, '结束本次', self.finish_early).pack(side='right')
        self.note = entry(self, active.get('note', ''), 31)
        self.note.pack(padx=20, pady=(12, 0), fill='x')
        label(self, '工作备注（可选）', size=8, color=MUTED).pack(anchor='w', padx=20, pady=(3, 13))
        self.bind('<space>', lambda e: self.toggle() if e.widget != self.note else None)
        self.after_id = self.after(200, self.tick)
        self.draw()
        self.update_idletasks()
        self.geometry(f'320x{max(390, self.winfo_reqheight())}')

    def drag(self, e):
        if self.drag_offset:
            self.geometry(f'+{e.x_root-self.drag_offset[0]}+{e.y_root-self.drag_offset[1]}')

    def hide(self):
        self.snapshot()
        self.app.persist()
        self.withdraw()
        if self.app.timer_only and not self.app.tray_ready:
            self.app.show()
        self.app.status.set('番茄钟仍在计时。点击“打开番茄钟”可再次显示。')

    def snapshot(self):
        self.active.update(elapsed_seconds=self.clock.elapsed, note=self.note.get(), running=self.clock.running)
        self.app.data['active'] = self.active

    def toggle(self):
        if self.clock.running:
            self.clock.pause()
        else:
            self.clock.start()
        self.snapshot()
        self.app.persist()
        self.draw()

    def draw(self):
        phase = self.active['phase']
        seconds = self.clock.left
        key = (phase, self.active.get('round', 0), seconds, self.clock.running)
        if key == self.last_draw_key:
            return False
        self.last_draw_key = key
        text = '专注' if phase == 'work' else '长休息' if phase == 'long_rest' else '休息'
        self.phase_label.config(text=f'{text}　/　第 {self.active.get("round", 0)+1} 轮')
        self.toggle_button.config(text='暂停' if self.clock.running else '继续' if self.clock.elapsed else '开始')
        c = self.canvas
        c.delete('all')
        color = ORANGE if phase == 'work' else GREEN
        c.create_oval(52, 18, 228, 194, outline=LINE, width=15)
        extent = 359.99 * seconds/max(1, self.clock.total)
        if extent:
            c.create_arc(52, 18, 228, 194, start=90, extent=-extent, style='arc', outline=color, width=15)
        c.create_text(140, 95, text=f'{seconds//60:02d}:{seconds%60:02d}', fill=TEXT, font=('Consolas', 25))
        c.create_text(140, 126, text='正在专注' if self.clock.running and phase == 'work' else '休息一下' if self.clock.running else '已暂停', fill=MUTED, font=(FONT, 9))
        return True

    def tick(self):
        if self.clock.running and self.clock.left <= 0:
            self.complete_phase()
            if not self.winfo_exists():
                return
        if self.draw():
            self.app.render_daily_progress()
        if self.clock.running and abs(self.clock.elapsed-self.last_save) >= 5:
            self.snapshot()
            if self.app.checkpoint():
                self.last_save = self.clock.elapsed
        self.after_id = self.after(250, self.tick)

    def obtain_note(self):
        if self.app.cfg.get('force_note') and self.active['phase'] == 'work' and not self.note.get().strip():
            text = sd.askstring('记录本次工作', '本次工作备注：', parent=self)
            if text and text.strip():
                self.note.insert(0, text.strip())
                return True
            return False
        return True

    def complete_phase(self):
        self.clock.pause()
        if not self.obtain_note():
            return
        cfg = self.app.cfg
        if self.active['phase'] == 'work':
            self.app.log_work(self.task, self.active, self.clock.elapsed/60, self.note.get(), completed=True)
            self.active['round'] = self.active.get('round', 0) + 1
            phase = 'long_rest' if self.active['round'] % cfg['long_every'] == 0 else 'rest'
            seconds = cfg[phase] * 60
        else:
            if not self.task or remaining(self.task) <= 0:
                self.stop_window()
                self.app.status.set('任务已完成。下次打开番茄钟会选择新的排程任务。')
                return
            phase = 'work'
            seconds = min(cfg['work'], remaining(self.task)) * 60
        self.active.update(phase=phase, total_seconds=seconds, elapsed_seconds=0, start=iso(now()), note='')
        if phase == 'work':
            self.active['session_id'] = uid()
        self.note.delete(0, 'end')
        self.clock = Clock(seconds)
        if cfg['auto_next']:
            self.clock.start()
        self.last_save = -10
        self.snapshot()
        self.app.replan()
        try:
            self.bell()
        except tk.TclError:
            pass
        self.deiconify()
        self.app.status.set('本次专注已记录，休息一下。' if phase != 'work' else '休息结束，可以开始下一轮。')

    def finish_early(self):
        was_running = self.clock.running
        self.clock.pause()
        if not self.obtain_note():
            if was_running:
                self.clock.start()
            return
        if self.active['phase'] == 'work' and self.clock.elapsed > 0:
            self.app.log_work(self.task, self.active, self.clock.elapsed/60, self.note.get(), completed=False)
        self.stop_window()
        self.app.replan()

    def stop_window(self):
        self.clock.pause()
        self.app.data['active'] = None
        self.app.timer = None
        if self.after_id:
            self.after_cancel(self.after_id)
        self.app.persist()
        self.destroy()


class App(tk.Tk):
    def __init__(self, store=None, tray=True, timer_only=False):
        super().__init__()
        self.title('Parcel · 日程与专注')
        self.configure(bg=CREAM)
        width, height, x, y = main_window_bounds(self)
        self.minsize(min(790, width), min(530, height))
        self.geometry(f'{width}x{height}{x:+d}{y:+d}')
        try:
            self.iconbitmap(str(ASSETS/'tomato.ico'))
        except tk.TclError:
            pass
        self.store = store or Store()
        self.data = self.store.data
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix='parcel-save')
        self.pending_write = None
        self.notion = NotionSync.load(self.store.path)
        self.notion_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='parcel-notion') if self.notion else None
        self.notion_future = None
        self.notion_upload_count = 0
        self.cfg = self.data['settings']
        self.energy_expiry = None
        self.courses = []
        self.timer = None
        self.timer_only = timer_only
        self.preparation = None
        self.bridge = None
        self.tray = None
        self.tray_ready = False
        self.actions = queue.Queue()
        self.selected_day = now().date()
        self.show_done = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='数据保存在本机；所有排程与计时均可离线使用。')
        self.style()
        self.build(width)
        self.bind_all('<MouseWheel>', ScrollArea.wheel)
        self.bind('<Control-n>', lambda e: self.add_task())
        self.bind('<Control-space>', lambda e: self.open_timer())
        self.bind('<F5>', lambda e: self.replan())
        self.protocol('WM_DELETE_WINDOW', self.close)
        if self.data.get('active'):
            # Recover saved progress paused. Offline time is not silently counted as work.
            self.data['active']['running'] = False
            self.timer = FloatTimer(self, self.data['active'])
            self.status.set('已恢复上次计时进度，当前为暂停状态。')
        self.replan()
        self.start_notion_sync()
        self.after(next_half_hour_delay(), self.refresh_tick)
        self.after(300, self.poll_actions)
        try:
            self.bridge = Bridge(self, self.store.path.parent)
        except OSError:
            self.status.set('Intercept 本地桥接端口不可用；Parcel 番茄钟仍可正常使用。')
        if tray:
            self.install_tray()

    def style(self):
        self.option_add('*Font', (FONT, 10))
        self.option_add('*selectBackground', MINT)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=PAPER, background=PALE, foreground=TEXT, padding=4)
        style.configure('Horizontal.TProgressbar', troughcolor=LINE, background=MINT, borderwidth=0, thickness=7)
        style.configure('TScrollbar', background=LINE, troughcolor=PAPER, borderwidth=0, arrowsize=11)
        style.configure('TNotebook', background=CREAM, borderwidth=0)
        style.configure('TNotebook.Tab', padding=[12, 8], background=PALE)
        style.map('TNotebook.Tab', background=[('selected', PAPER)])

    def build(self, width):
        top = frame(self, CREAM)
        top.pack(fill='x', padx=26, pady=(19, 6))
        label(top, 'Parcel', size=25, bold=True).pack(side='left')
        label(top, '日程与专注', size=11, color=MUTED).pack(side='left', padx=14, pady=(10, 0))
        self.date_label = label(top, '', color=MUTED)
        self.date_label.pack(side='right')
        actions = frame(self, CREAM)
        actions.pack(fill='x', padx=26, pady=(8, 8))
        for text, command, accent in [('打开番茄钟', self.open_timer, True), ('新增待办', self.add_task, False),
                                      ('大类设置', self.add_category, False), ('固定日程', self.events_window, False)]:
            button(actions, text, command, accent=accent).pack(side='left', padx=(0, 8))
        more = tk.Menubutton(actions, text='更多 ▾', bg=CREAM, fg=GREEN, font=(FONT, 10), relief='flat', padx=10, pady=7)
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label='计时 / 排程设置', command=self.settings)
        menu.add_command(label='工作规则', command=self.rules_window)
        menu.add_command(label='快捷文字录入（本地识别）', command=self.quick_add)
        menu.add_command(label='备份全部数据…', command=self.export)
        menu.add_command(label='打开数据文件夹', command=lambda: os.startfile(str(self.store.path.parent)))
        menu.add_separator()
        menu.add_command(label='退出 Parcel', command=self.quit_app)
        more.config(menu=menu)
        more.pack(side='left')
        self.stats = label(actions, '', size=9, color=MUTED)
        self.stats.pack(side='right')

        daily = frame(self, CREAM)
        daily.pack(fill='x', padx=26, pady=(0, 7))
        self.daily_label = label(daily, '今天：0 个番茄', size=9, color=GREEN)
        self.daily_label.pack(anchor='w', pady=(0, 4))
        self.daily_bar = ttk.Progressbar(daily, maximum=16, length=390)
        self.daily_bar.pack(anchor='w')

        self.recommend = frame(self, MINT)
        self.recommend.pack(fill='x', padx=26, pady=(4, 14))
        inner = frame(self.recommend, MINT)
        inner.pack(fill='x', padx=18, pady=12)
        title_row = frame(inner, MINT)
        title_row.pack(fill='x')
        self.recommend_title = label(title_row, '', size=14, bold=True, justify='left', wraplength=300)
        self.recommend_title.grid(row=0, column=0, sticky='w')
        start_button = button(title_row, '开始', self.open_timer, accent=True, small=True)
        start_button.grid(row=0, column=1, padx=(12, 0))

        def wrap_title(event):
            self.recommend_title.config(wraplength=max(40, event.width - start_button.winfo_reqwidth() - 12))

        title_row.bind('<Configure>', wrap_title)
        self.recommend_meta = label(inner, '', size=9, color=GREEN)
        self.recommend_meta.pack(anchor='w', pady=(3, 0))

        self.panes = tk.PanedWindow(self, orient='horizontal', bg=CREAM, bd=0, sashwidth=10, sashrelief='flat')
        self.panes.pack(fill='both', expand=True, padx=26)
        plan_slot = frame(self.panes, CREAM)
        task_panel, history_panel = [frame(self.panes) for _ in range(2)]
        panel_space = width - 72  # Side margins and both dividers.
        for panel, share, minimum in [(plan_slot, .38, 260), (task_panel, .35, 235), (history_panel, .27, 180)]:
            panel_width = round(panel_space * share)
            self.panes.add(panel, minsize=min(minimum, panel_width), width=panel_width, stretch='always')
        plan_slot.pack_propagate(False)
        plan_panel = frame(plan_slot)
        plan_panel.pack(fill='both', expand=True)
        expand_plan = button(plan_slot, '展开排程 ▸',
                             lambda: self.toggle_plan_panel(plan_panel, expand_plan), small=True)
        task_head = frame(task_panel)
        task_head.pack(fill='x', padx=16, pady=(15, 8))
        label(task_head, '待办事项', size=15, bold=True).pack(side='left')
        tk.Checkbutton(task_head, text='显示已完成', variable=self.show_done, command=self.render_tasks,
                       bg=PAPER, fg=MUTED, activebackground=PAPER, font=(FONT, 9)).pack(side='right')
        filter_host = frame(task_panel)
        filter_host.pack(fill='x', padx=16)
        filter_body = frame(filter_host)
        toggle = button(filter_host, '筛选 ▸', lambda: self.toggle_task_filters(filter_body, toggle), subtle=True, small=True)
        toggle.pack(anchor='w')
        self.task_filters = {}
        for i, (key, title) in enumerate([('高', '高精力'), ('低', '低精力'), ('输入', '输入式工作'),
                                          ('输出', '输出式工作'), ('休闲', '休闲')]):
            var = tk.BooleanVar(value=key != '休闲')
            self.task_filters[key] = var
            tk.Checkbutton(filter_body, text=title, variable=var, command=self.render_tasks,
                           bg=PAPER, fg=TEXT, activebackground=PAPER, font=(FONT, 9)
                           ).grid(row=i//2, column=i%2, sticky='w')
        self.task_area = ScrollArea(task_panel)
        self.task_area.pack(fill='both', expand=True, padx=12, pady=(8, 10))
        head = frame(history_panel)
        head.pack(fill='x', padx=16, pady=(15, 8))
        label(head, '历史记录', size=15, bold=True).pack(side='left')
        button(head, '补记', self.manual_log, subtle=True).pack(side='right')
        self.history_area = ScrollArea(history_panel)
        self.history_area.pack(fill='both', expand=True, padx=12, pady=(8, 10))
        head = frame(plan_panel)
        head.pack(fill='x', padx=16, pady=(15, 8))
        button(head, '◂', lambda: self.toggle_plan_panel(plan_panel, expand_plan),
               subtle=True, small=True).pack(side='left', padx=(0, 4))
        label(head, '自动排程', size=15, bold=True).pack(side='left')
        button(head, '重新安排', self.replan, subtle=True).pack(side='right')
        self.preferences = {}
        for key, title, states in [('energy', '精力状态', ENERGY_STATES), ('work_type', '工作类型', WORK_STATES)]:
            row = frame(plan_panel)
            row.pack(fill='x', padx=16, pady=(0, 5))
            label(row, title, size=9, color=MUTED).pack(side='left')
            combo = ttk.Combobox(row, state='readonly', width=8, values=states)
            combo.pack(side='left', padx=8)
            combo.bind('<<ComboboxSelected>>', lambda event, key=key: self.change_energy(key=key))
            hint = label(row, '', size=9, color=MUTED)
            hint.pack(side='left')
            self.preferences[key] = combo, hint
        self.energy = self.preferences['energy'][0]
        self.health = button(plan_panel, '', self.show_conflicts, subtle=True, anchor='w')
        self.health.pack(fill='x', padx=10)
        self.health_meta = label(plan_panel, '', size=8, color=MUTED, anchor='w')
        self.health_meta.pack(fill='x', padx=18, pady=(0, 8))
        nav = frame(plan_panel)
        nav.pack(fill='x', padx=12)
        button(nav, '‹', lambda: self.change_day(-1), subtle=True, width=1).pack(side='left')
        self.day_combo = ttk.Combobox(nav, state='readonly', width=17)
        self.day_combo.pack(side='left', fill='x', expand=True, padx=2)
        self.day_combo.bind('<<ComboboxSelected>>', self.select_day)
        button(nav, '›', lambda: self.change_day(1), subtle=True, width=1).pack(side='left')
        button(nav, '今天', self.today, subtle=True).pack(side='right')
        self.plan_area = ScrollArea(plan_panel)
        self.plan_area.pack(fill='both', expand=True, padx=12, pady=(10, 10))
        footer = frame(self, CREAM)
        footer.pack(fill='x', padx=26, pady=(6, 8))
        button(footer, '问题与反馈', self.feedback_window, subtle=True).pack(side='right')
        label(footer, textvariable=self.status, size=9, color=MUTED, anchor='w').pack(side='left', fill='x', expand=True)

    @staticmethod
    def toggle_plan_panel(panel, expand):
        if panel.winfo_manager():
            panel.pack_forget()
            expand.pack(anchor='nw', padx=8, pady=(15, 0))
        else:
            expand.pack_forget()
            panel.pack(fill='both', expand=True)

    def feedback_window(self):
        d = Dialog(self, '问题与反馈')
        label(d.body, '遇到了什么问题？也可以写下想调整的地方。').pack(anchor='w')
        text = tk.Text(d.body, width=54, height=7, wrap='word', bg=PAPER, fg=TEXT,
                       font=(FONT, 10), padx=10, pady=8)
        text.pack(fill='both', expand=True, pady=8)
        label(d.body, '反馈会先保存在本机，供晚间批量处理。', size=9, color=MUTED).pack(anchor='w')

        def submit():
            try:
                self.store.submit_feedback(text.get('1.0', 'end-1c'))
            except (ValueError, OSError) as exc:
                hint = str(exc) if isinstance(exc, ValueError) else '保存失败，请重试。'
                mb.showwarning('反馈未提交', hint, parent=d)
                return
            d.destroy()
            self.status.set('反馈已保存，等待晚间处理。')

        d.footer(submit, '提交')
        text.focus_set()

    def persist(self):
        # Serialize explicit changes with any in-flight timer snapshot.
        if self.pending_write:
            try:
                self.pending_write.result()
            except OSError:
                pass  # Retry the newest state with the explicit save below.
            self.pending_write = None
        self.store.save()

    def checkpoint(self):
        if self.pending_write and not self.pending_write.done():
            return False
        if self.pending_write:
            try:
                self.pending_write.result()
            except OSError:
                self.status.set('计时进度暂未保存，正在重试。')
        self.pending_write = self.writer.submit(self.store.save, copy.deepcopy(self.data))
        return True

    def replan(self, *, periodic=False):
        if self.timer and self.timer.winfo_exists():
            self.timer.snapshot()
        at = now()
        self.render_energy(at)
        try:
            self.courses = self.store.course_events()
        except (ValueError, OSError):
            self.status.set('课表暂时无法读取，保留已加载的课程。')
        self.plan = schedule(self.data, at, courses=self.courses)
        self.data['schedule'] = self.plan['blocks']
        if not periodic:
            self.persist()
            self.render_tasks()
            self.render_history()
        self.render_plan()
        self.render_summary()

    def refresh_tick(self):
        try:
            self.replan(periodic=True)
            self.start_notion_sync()
        finally:
            self.after(next_half_hour_delay(), self.refresh_tick)

    def start_notion_sync(self):
        if self.notion_worker and self.notion_future is None:
            state = self.data.get('notion_sync', {})
            databases = state.get('databases', {}) if state.get('parent') == self.notion.parent else {}
            self.notion_future = self.notion_worker.submit(self.notion.pull, databases)

    def poll_notion_sync(self):
        if self.notion_future is None or not self.notion_future.done() or self.grab_current():
            return
        future, self.notion_future = self.notion_future, None
        upload_count, self.notion_upload_count = self.notion_upload_count, 0
        try:
            result = future.result()
            if upload_count:
                if result:
                    state = copy.deepcopy(self.data['notion_sync'])
                    for (table, key), entry in result.items():
                        state[table][key] = entry
                    self.save_notion_changes({'notion_sync': state})
                self.notion.log(f'已上传 {len(result)}/{upload_count} 行，未成功的行将在下次重试。'
                                if len(result) != upload_count else f'同步完成，已上传 {upload_count} 行。')
                return
            changes, writes = reconcile(self.data, self.notion.parent, result, self.notion.log)
            changes['notion_sync']['databases'] = self.notion.databases
            self.save_notion_changes(changes)
            if writes:
                self.notion_upload_count = len(writes)
                self.notion_future = self.notion_worker.submit(self.notion.push, writes)
            else:
                self.notion.log('同步完成，没有待上传的改动。')
        except SyncError as exc:
            self.notion.log(str(exc))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.notion.log(f'同步暂未完成（{type(exc).__name__}），下次重试。')

    def save_notion_changes(self, changes):
        changes = {key: value for key, value in changes.items() if value != self.data.get(key)}
        if not changes:
            return
        previous = {key: self.data[key] for key in changes if key in self.data}
        plan, blocks = self.plan, self.data['schedule']
        self.data.update(changes)
        try:
            if changes.keys() - {'notion_sync'}:
                self.replan()
            else:
                self.persist()
        except OSError:
            for key in changes.keys() - previous.keys():
                self.data.pop(key, None)
            self.data.update(previous, schedule=blocks)
            self.plan = plan
            raise

    def render_daily_progress(self):
        today = str(now().date())
        records = self.data['history']
        minutes = sum(h.get('minutes', POMODORO_MINUTES) for h in records
                      if h.get('type') == 'work' and h.get('end', '').startswith(today))
        if self.timer and self.timer.active['phase'] == 'work':
            active = self.timer.active
            session = active.get('session_id')
            if active.get('start', '').startswith(today) and (not session or not any(h.get('session_id') == session for h in records)):
                minutes += self.timer.clock.elapsed / 60
        whole_pomodoros = int(minutes / POMODORO_MINUTES)
        self.daily_label.config(text=f'今天：{whole_pomodoros} 个番茄')
        self.daily_bar['value'] = min(16, whole_pomodoros)

    def render_summary(self):
        self.render_daily_progress()
        at = now()
        weekday = '一二三四五六日'[at.weekday()]
        self.date_label.config(text=f'{at:%Y.%m.%d}  周{weekday}　{at:%H:%M}　伦敦时间')
        self.stats.config(text=f'{sum(remaining(t)>0 for t in self.data["tasks"])} 项待办')
        task, block = self.recommended()
        if self.data.get('active'):
            task = next((t for t in self.data['tasks'] if t['id'] == self.data['active']['task_id']), task)
            self.recommend_title.config(text=f'当前专注　{task["title"]}' if task else '当前专注')
            self.recommend_meta.config(text='点击“打开番茄钟”回到悬浮计时窗口')
        elif task:
            current = block and parse_dt(block['start']) <= at < parse_dt(block['end'])
            self.recommend_title.config(text=('现在可以做　' if current else '接下来　') + task['title'])
            self.recommend_meta.config(text=f'{parse_dt(block["start"]):%m月%d日 %H:%M}–{parse_dt(block["end"]):%H:%M}　{minutes_text(block["minutes"])}')
        else:
            self.recommend_title.config(text='暂时没有可执行的排程任务')
            self.recommend_meta.config(text='新增待办，或查看排程冲突；也可以自定义选择本次专注任务。')

    def recommended(self):
        at = now()
        candidates = [b for b in self.plan['blocks'] if b['kind'] in ('work', 'pinned') and parse_dt(b['end']) > at]
        if not candidates:
            return None, None
        b = candidates[0]
        t = next((t for t in self.data['tasks'] if t['id'] == b['task_id'] and remaining(t) > 0), None)
        return t, b

    @staticmethod
    def toggle_task_filters(body, toggle):
        if body.winfo_manager():
            body.pack_forget()
            toggle.configure(text='筛选 ▸')
        else:
            body.pack(anchor='w')
            toggle.configure(text='筛选 ▾')

    def render_tasks(self):
        self.task_area.clear()
        hidden = {key for key, var in self.task_filters.items() if not var.get()}
        tasks = [t for t in self.data['tasks'] if (self.show_done.get() or remaining(t) > 0)
                 and t.get('energy') not in hidden and t.get('task_type') not in hidden]
        tasks.sort(key=lambda t: (not bool(remaining(t)), parse_dt(t.get('due'), True) or datetime.max,
                                  -category_priority(self.data, t)))
        for t in tasks:
            host = frame(self.task_area.body)
            host.pack(fill='x', padx=5, pady=(0, 6))
            title = f'[{t.get("category", "通用")}] {t["title"]}'
            wrapping_label(host, ('✓ ' if remaining(t) <= 0 else '') + title, size=10, bold=True).pack(fill='x', pady=(4, 4))
            due = t.get('due', '')
            expired = due and parse_dt(due, True) < now() and remaining(t) > 0
            label(host, f'截止 {due}' if due else '无截止日期', size=9, color=RED if expired else MUTED).pack(anchor='w')
            row = frame(host)
            row.pack(fill='x', pady=(5, 2))
            row.columnconfigure(0, weight=1)
            button(row, '编辑', lambda t=t: self.edit_task(t), small=True).grid(row=0, column=1, padx=4, sticky='ns')
            if t.get('done'):
                button(row, '重新打开', lambda t=t: self.complete_task(t), small=True).grid(row=0, column=2, sticky='ns')
            elif remaining(t) > 0:
                button(row, '开始', lambda t=t: self.open_timer(task=t), small=True).grid(row=0, column=2, sticky='ns')
            bar = ttk.Progressbar(row, maximum=max(.1, t['estimated_minutes']), value=min(t['worked_minutes'], t['estimated_minutes']))
            bar.grid(row=0, column=0, sticky='nsew')
            label(host, f'{pomodoro_text(t["worked_minutes"])} / {pomodoro_text(t["estimated_minutes"])} 个番茄', size=9, color=MUTED).pack(anchor='w')
            line(host)
        if not tasks:
            label(self.task_area.body, '没有符合筛选条件的待办。', size=11, color=MUTED).pack(anchor='w', padx=10, pady=20)

    def render_history(self):
        self.history_area.clear()
        records = [h for h in self.data['history'] if h.get('type') == 'work']
        tasks = {t['id']: t for t in self.data['tasks']}
        for h in sorted(records, key=lambda r: r.get('end', ''), reverse=True)[:200]:
            host = frame(self.history_area.body)
            host.pack(fill='x', padx=5, pady=(0, 10))
            start, end = parse_dt(h.get('start')), parse_dt(h.get('end'))
            when = f'{start:%m-%d %H:%M}—{end:%H:%M}' if start and end else h.get('end', '')[:16].replace('T', ' ') or '时间未知'
            task = tasks.get(h.get('todo_id'), {})
            title = h.get('title') or task.get('title', '')
            category = h.get('category') or task.get('category')
            task_text = f' - [{category}] {title}' if title and category else f' - {title}' if title else ''
            wrapping_label(host, f'{when} | 工作{task_text}', size=10).pack(fill='x')
            note = wrapping_label(host, f'备注：{h.get("note", "").strip() or "（工作备注，可选）"}', size=9, color=MUTED, cursor='hand2')
            note.pack(fill='x', pady=(2, 0))
            note.bind('<Double-Button-1>', lambda e, h=h: self.edit_note(h))
        if not records:
            label(self.history_area.body, '完成一次专注后，\n工作记录会出现在这里。', color=MUTED, justify='left').pack(anchor='w', padx=10, pady=20)

    def render_plan(self):
        self.plan_area.clear()
        status = {'green': ('安排可行', GREEN), 'orange': ('安排较紧', '#B76A24'), 'red': ('有排程冲突', RED)}[self.plan['health']]
        count = len(self.plan['overflow']) + len(self.plan['conflicts'])
        self.health.config(text=f'●  {status[0]}' + (f'　{count} 项 · 点击查看' if count else '　查看排程详情'), fg=status[1])
        spare = max(0, self.plan['capacity_minutes']-self.plan['scheduled_minutes'])
        self.health_meta.config(text=f'{self.cfg["horizon_days"]} 天内已排 {self.plan["scheduled_minutes"]/60:.1f}h · 剩余可用约 {spare/60:.1f}h')
        self.days = [now().date()+timedelta(days=i) for i in range(self.cfg['horizon_days'])]
        if self.selected_day not in self.days:
            self.selected_day = self.days[0]
        names = [f'{d:%m月%d日}  周{"一二三四五六日"[d.weekday()]}' for d in self.days]
        self.day_combo.config(values=names)
        self.day_combo.current(self.days.index(self.selected_day))
        at = now()
        blocks = [b for b in self.plan['blocks'] if parse_dt(b['start']).date() == self.selected_day and parse_dt(b['end']) > at]
        if not blocks:
            label(self.plan_area.body, '这一天暂无安排。\n可以添加任务或固定日程。', color=MUTED, justify='left').pack(anchor='w', padx=10, pady=20)
        now_drawn = False
        for b in blocks:
            start, end = parse_dt(b['start']), parse_dt(b['end'])
            if self.selected_day == at.date() and not now_drawn and start >= at:
                now_drawn = True
                label(self.plan_area.body, f'●  现在 {at:%H:%M}', size=9, color=ORANGE).pack(anchor='w', padx=6, pady=(3, 6))
            if b['kind'] == 'break':
                label(self.plan_area.body, f'{start:%H:%M}–{end:%H:%M}   {b["title"]}', size=8, color=MUTED).pack(anchor='w', padx=10, pady=(0, 6))
                continue
            label(self.plan_area.body, f'{start:%H:%M}', size=9, color=MUTED).pack(anchor='w', padx=6, pady=(0, 5))
            outer = frame(self.plan_area.body, LINE, bd=0)
            outer.pack(fill='x', padx=5, pady=(0, 8))
            color = ORANGE if b['kind'] in ('work', 'active', 'pinned') else MINT if b['kind'] == 'reserve' else MUTED
            if b.get('course'):
                color = COURSE_ORANGE
            frame(outer, color, width=3).pack(side='left', fill='y')
            card = frame(outer, PALE if b['kind'] == 'active' else PAPER)
            card.pack(side='left', fill='both', expand=True, padx=1, pady=1)
            header = '正在执行 · ' if b['kind'] == 'active' else '已锁定 · ' if b['kind'] == 'pinned' else ''
            if b['kind'] in ('work', 'pinned'):
                controls = frame(card, card.cget('bg'))
                controls.pack(side='right', padx=(0, 6), pady=4)
                button(controls, '开始', lambda b=b: self.open_timer(block=b), small=True).pack(fill='x')
                button(controls, '解锁' if b['kind'] == 'pinned' else '调整 / 锁定', lambda b=b: self.pin_block(b), subtle=True, small=True).pack(fill='x')
            details = frame(card, card.cget('bg'))
            details.pack(side='left', fill='x', expand=True, padx=10, pady=6)
            wrapping_label(details, header+b['title'], size=10, bold=True).pack(fill='x')
            label(details, f'{start:%H:%M} – {end:%H:%M}' + ('　固定' if b['kind'] == 'fixed' else ''), size=9, color=MUTED).pack(anchor='w', pady=(2, 0))

    def change_day(self, delta):
        index = self.days.index(self.selected_day)
        self.selected_day = self.days[max(0, min(len(self.days)-1, index+delta))]
        self.render_plan()
        self.plan_area.canvas.yview_moveto(0)

    def select_day(self, event=None):
        self.selected_day = self.days[self.day_combo.current()]
        self.render_plan()
        self.plan_area.canvas.yview_moveto(0)

    def today(self):
        self.selected_day = now().date()
        self.render_plan()

    def change_energy(self, *, key='energy'):
        mode = self.preferences[key][0].get()
        until = now() + timedelta(minutes=ENERGY_WINDOW_MINUTES)
        self.cfg[key] = mode
        self.cfg[f'{key}_until'] = iso(until) if mode != '正常' else ''
        self.replan()

    def render_energy(self, at):
        if self.energy_expiry is not None:
            self.after_cancel(self.energy_expiry)
            self.energy_expiry = None
        expiries = []
        for key, (combo, hint) in self.preferences.items():
            mode, until = current_preference(self.cfg, at, key)
            combo.set(mode)
            if until:
                clock = until.strftime('%H:%M' if until.date() == at.date() else '%m/%d %H:%M')
                hint.configure(text=f'生效至 {clock}')
                expiries.append(until)
            else:
                hint.configure(text='偏好持续 1 小时')
        if expiries:
            delay = math.ceil((min(expiries) - at).total_seconds() * 1000)
            self.energy_expiry = self.after(delay, self.expire_energy)

    def expire_energy(self):
        self.energy_expiry = None
        self.replan(periodic=True)

    def add_task(self):
        d = TaskDialog(self, self.data)
        self.wait_window(d)
        if d.result:
            task = dict(d.result, id=uid())
            self.data['tasks'].append(task)
            if task['category'] not in self.data['categories']:
                self.data['categories'].append(task['category'])
            self.replan()
            self.status.set(f'已添加：{task["title"]}')

    def quick_add(self):
        text = sd.askstring('快捷文字录入', '例如：明天 画草图 2小时\n本地识别名称、明确的时长和日期；下一步可以检查全部字段。', parent=self)
        if not text or not text.strip():
            return
        initial = quick_parse(text)
        d = TaskDialog(self, self.data, initial=initial)
        self.wait_window(d)
        if d.result:
            self.data['tasks'].append(dict(d.result, id=uid()))
            if d.result['category'] not in self.data['categories']:
                self.data['categories'].append(d.result['category'])
            self.replan()

    def edit_task(self, task):
        if self.data.get('active') and self.data['active']['task_id'] == task['id']:
            mb.showinfo('任务正在执行', '请先结束本次专注，再编辑工时或约束。', parent=self)
            return
        d = TaskDialog(self, self.data, task)
        self.wait_window(d)
        if d.delete_requested:
            self.delete_task(task)
        elif d.complete_requested:
            self.complete_task(task)
        elif d.result:
            task.update(d.result)
            if task['category'] not in self.data['categories']:
                self.data['categories'].append(task['category'])
            self.replan()

    def add_category(self):
        d = Dialog(self, '大类设置')
        categories = self.data['categories']
        priorities = self.data.get('category_priorities', {})
        existing = d.field('现有大类（留空可新增）', '', ['', *categories])
        existing.configure(state='readonly')
        name = d.field('大类名称')
        priority = d.field('优先级（5 最高）', 3, [1, 2, 3, 4, 5])
        priority.configure(state='readonly')

        def select_category(event):
            previous = existing.get()
            name.delete(0, 'end')
            name.insert(0, previous)
            priority.set(priorities.get(previous, 3))

        existing.bind('<<ComboboxSelected>>', select_category)

        def save():
            previous = existing.get()
            category = name.get().strip()
            if not category:
                mb.showwarning('大类设置', '请输入大类名称。', parent=d)
                return
            if category != previous and category in categories:
                mb.showwarning('大类设置', '这个名称已存在，请选择其他名称。', parent=d)
                return
            if not previous:
                categories.append(category)
            elif previous != category:
                categories[categories.index(previous)] = category
                priorities.pop(previous, None)
                for task in self.data['tasks']:
                    if task.get('category') == previous:
                        task['category'] = category
            priorities[category] = int(priority.get())
            self.data['category_priorities'] = priorities
            d.destroy()
            self.replan()
            self.status.set(f'已保存大类：{category}')

        d.footer(save)

    def complete_task(self, task):
        if self.data.get('active') and self.data['active']['task_id'] == task['id']:
            mb.showinfo('任务正在执行', '请先在番茄钟中结束本次专注。', parent=self)
            return
        task['done'] = not task.get('done', False)
        if not task['done'] and remaining(task) <= 0:
            task['estimated_minutes'] = task['worked_minutes'] + self.cfg['work']
        self.replan()

    def delete_task(self, task):
        if self.data.get('active') and self.data['active']['task_id'] == task['id']:
            mb.showinfo('任务正在执行', '请先结束本次专注。', parent=self)
            return
        if mb.askyesno('删除待办', f'删除“{task["title"]}”？历史记录会保留；依赖它的任务需要重新设置前置任务。', parent=self):
            for record in self.data['history']:
                if record.get('todo_id') == task['id']:
                    record.setdefault('title', task['title'])
                    record.setdefault('category', task.get('category', ''))
            self.data['tasks'].remove(task)
            self.data['pins'] = [p for p in self.data['pins'] if p['task_id'] != task['id']]
            self.replan()

    def open_timer(self, task=None, block=None):
        if self.timer and self.timer.winfo_exists():
            self.timer.deiconify()
            self.timer.lift()
            return
        if block:
            task = next((t for t in self.data['tasks'] if t['id'] == block['task_id']), None)
        elif not task:
            task, block = self.recommended()
        d = StartDialog(self, task, block)
        self.wait_window(d)
        if d.result:
            task, mins = d.result
            active = dict(task_id=task['id'], session_id=uid(), phase='work', round=0, total_seconds=mins*60,
                          elapsed_seconds=0, start=iso(now()), note='', running=True)
            self.data['active'] = active
            # A manual start consumes its scheduled/pinned work rather than duplicating it.
            if block and block.get('kind') == 'pinned':
                self.data['pins'] = [p for p in self.data['pins'] if p['id'] != block['id']]
            self.timer = FloatTimer(self, active)
            self.timer.clock.start()
            self.replan()

    def intercept_command(self, command):
        if self.timer and self.timer.winfo_exists():
            self.open_timer()
            return {'status': 'existing'}
        if command == 'show':
            self.start_recommended_intercept()
            return {'status': 'shown'}
        if self.preparation and self.preparation.winfo_exists():
            self.preparation.lift()
            return {'status': 'preparing'}
        self.start_preparation()
        return {'status': 'preparing'}

    def start_preparation(self):
        dialog = tk.Toplevel(self)
        self.preparation = dialog
        dialog.title('Parcel · 3 分钟准备')
        dialog.configure(bg=CREAM)
        dialog.attributes('-topmost', True)
        dialog.geometry('390x310')
        label(dialog, '开始专注前，准备一下', size=15, bold=True).pack(pady=(20, 8))
        countdown = label(dialog, '03:00', size=24, color=ORANGE)
        countdown.pack()
        for item in ('调整房间灯光', '站起来接温水或其他饮品', '给电脑接线', '带耳机播放工作 BGM'):
            tk.Checkbutton(dialog, text=item, bg=CREAM, font=(FONT, 10), anchor='w').pack(fill='x', padx=38)
        deadline = __import__('time').monotonic() + 180

        def tick():
            if not dialog.winfo_exists():
                return
            left = max(0, math.ceil(deadline - __import__('time').monotonic()))
            countdown.config(text=f'{left//60:02d}:{left%60:02d}')
            if left:
                dialog.after(250, tick)
            else:
                dialog.destroy()
                self.preparation = None
                self.start_recommended_intercept()
        tick()

    def start_recommended_intercept(self):
        if self.timer and self.timer.winfo_exists():
            self.open_timer()
            return
        self.replan()
        task, block = self.recommended()
        if not task:
            self.status.set('准备已结束；当前没有可推荐任务，请新增待办或手动选择。')
            self.show()
            return
        minutes = min(self.cfg['work'], remaining(task))
        active = dict(task_id=task['id'], session_id=uid(), phase='work', round=0,
                      total_seconds=minutes*60, elapsed_seconds=0, start=iso(now()), note='', running=True)
        self.data['active'] = active
        self.timer = FloatTimer(self, active)
        self.timer.clock.start()
        self.replan()

    def log_work(self, task, active, minutes, note, completed):
        if minutes <= 0:
            return
        session_id = active.setdefault('session_id', uid())
        if any(h.get('session_id') == session_id for h in self.data['history']):
            return
        record = dict(id=uid(), type='work', todo_id=task['id'] if task else None,
                      title=task['title'] if task else '临时任务', start=active['start'], end=iso(now()),
                      category=task.get('category', '') if task else '', minutes=round(minutes, 4),
                      note=note.strip(), partial=not completed, session_id=session_id)
        self.data['history'].append(record)
        if task:
            task['worked_minutes'] = round(task['worked_minutes']+minutes, 4)
            task['done'] = task['worked_minutes'] >= task['estimated_minutes']-.001
        self.persist()

    def edit_note(self, record):
        value = sd.askstring('修改工作备注', '备注：', initialvalue=record.get('note', ''), parent=self)
        if value is not None:
            record['note'] = value
            self.persist()
            self.render_history()

    def manual_log(self):
        d = Dialog(self, '补记工作')
        tasks = self.data['tasks']
        names = [f'{i+1}. {t["title"]}' for i,t in enumerate(tasks)]
        task = d.field('任务', names[0] if names else '', names)
        start = d.field('开始时间', f'{now():%Y-%m-%d %H:%M}')
        minutes = d.field('实际工作（分钟）', self.cfg['work'])
        note = d.field('备注', '')
        def save():
            try:
                selected = tasks[names.index(task.get())]
                mins = float(minutes.get())
                begin = parse_dt(start.get())
                end = begin + timedelta(minutes=mins)
                if mins <= 0 or end > now():
                    raise ValueError('请填写已经完成的工作，结束时间不能晚于现在')
                if self.data.get('active') and self.data['active']['task_id'] == selected['id']:
                    raise ValueError('任务正在计时，请先结束本次专注再补记')
                selected['worked_minutes'] += mins
                selected['done'] = selected['worked_minutes'] >= selected['estimated_minutes']
                self.data['history'].append(dict(id=uid(), type='work', todo_id=selected['id'], title=selected['title'], category=selected.get('category', ''), start=iso(begin), end=iso(end), minutes=mins, note=note.get(), manual=True))
                d.destroy()
                self.replan()
            except (ValueError, IndexError, TypeError) as exc:
                mb.showwarning('检查补记', str(exc), parent=d)
        d.footer(save, '保存记录')

    def settings(self):
        d = SettingsDialog(self, self.cfg)
        self.wait_window(d)
        if d.result:
            self.data['settings'] = self.cfg = d.result
            self.replan()

    def events_window(self):
        win = Dialog(self, '固定日程', 650)
        lb = tk.Listbox(win.body, width=76, height=13, font=(FONT, 10), bg=PAPER, fg=TEXT, exportselection=False)
        lb.pack()
        def refresh():
            lb.delete(0, 'end')
            for e in [*self.data['events'], *self.courses]:
                day = e.get('date') or ('每周 ' + ','.join(str(i+1) for i in e.get('weekdays', [])))
                suffix = '  ·  Google 课表' if e.get('course') else ''
                lb.insert('end', f'{day}  {e["start"]}–{e["end"]}  {e.get("title", e.get("name", "固定日程"))}{suffix}')
                if suffix:
                    lb.itemconfigure('end', foreground='#98620B')
        def edit(add=False):
            sel = lb.curselection()
            if not add and sel and sel[0] >= len(self.data['events']):
                mb.showinfo('Google 课表', '请在 Google 日历中调整这节课。', parent=win)
                return
            e = None if add else self.data['events'][sel[0]] if sel else None
            if not add and e is None:
                return
            d = EventDialog(win, e)
            win.wait_window(d)
            if d.result:
                if e:
                    e.update(d.result)
                else:
                    self.data['events'].append(dict(d.result, id=uid()))
                refresh()
                self.replan()
            win.grab_set()
        def delete():
            sel = lb.curselection()
            if sel and sel[0] >= len(self.data['events']):
                mb.showinfo('Google 课表', '请在 Google 日历中调整这节课。', parent=win)
                return
            if sel and mb.askyesno('删除固定日程', '删除这个固定日程？', parent=win):
                self.data['events'].pop(sel[0])
                refresh()
                self.replan()
        row = frame(win, CREAM)
        row.pack(fill='x', padx=24, pady=(0, 20))
        button(row, '新增', lambda: edit(True), accent=True).pack(side='left')
        button(row, '编辑', edit).pack(side='left', padx=8)
        button(row, '删除', delete, subtle=True).pack(side='left')
        button(row, '关闭', win.destroy).pack(side='right')
        refresh()

    def show_conflicts(self):
        d = Dialog(self, '排程详情与冲突', 690)
        text = tk.Text(d.body, width=76, height=23, wrap='word', font=(FONT, 10), bg=PAPER, fg=TEXT, relief='flat', padx=14, pady=14)
        text.pack()
        text.insert('end', f'未来 {self.cfg["horizon_days"]} 天\n可用约 {self.plan["capacity_minutes"]/60:.1f} 小时，已排工作 {self.plan["scheduled_minutes"]/60:.1f} 小时。\n')
        if self.plan['busiest']:
            text.insert('end', f'工作最集中的一天：{self.plan["busiest"]}\n')
        for conflict in self.plan['conflicts']:
            text.insert('end', f'\n● {conflict}\n')
        for item in self.plan['overflow']:
            text.insert('end', f'\n● {item["title"]}\n尚未排入 {minutes_text(item["minutes"])}\n{item["reason"]}\n')
        if not self.plan['conflicts'] and not self.plan['overflow']:
            text.insert('end', '\n所有待办均已排入，未发现截止日期或容量冲突。\n')
        text.insert('end', '\n可以调整任务的截止日期、预计工时或最短连续时长，增加可用时间，或解锁不再适用的固定工作时段。\n\n工作时长不包含休息。优先满足截止日期，再考虑精力与输入/输出交替。')
        text.config(state='disabled')
        button(d, '关闭', d.destroy).pack(pady=(0, 16))

    def pin_block(self, block):
        if block['kind'] == 'pinned':
            self.data['pins'] = [p for p in self.data['pins'] if p['id'] != block['id']]
            self.replan()
            return
        d = Dialog(self, '调整并锁定工作时段')
        start_e = d.field('开始时间', parse_dt(block['start']).strftime('%Y-%m-%d %H:%M'))
        label(d.body, '保留本段工作时长。锁定后，自动重排会保留这个时段。\n新约束使它不可执行时，将显示冲突。', size=9, color=MUTED, justify='left').grid(row=d.column, column=0, columnspan=2, pady=15, sticky='w')
        def save():
            try:
                start = parse_dt(start_e.get())
                if not start or start < now():
                    raise ValueError('请选择未来的时间')
                end = start+timedelta(minutes=block['minutes'])
                pin = dict(id=uid(), task_id=block['task_id'], start=iso(start), end=iso(end))
                candidate = copy.deepcopy(self.data)
                candidate['pins'].append(pin)
                plan = schedule(candidate, courses=self.courses)
                if not any(b.get('id') == pin['id'] for b in plan['blocks']):
                    raise ValueError('这个时段不满足可用时间、截止日期、前置任务或休息约束；请选择其他时间')
                self.data['pins'].append(pin)
                d.destroy()
                self.replan()
            except ValueError as exc:
                mb.showwarning('无法锁定', str(exc), parent=d)
        d.footer(save, '锁定这个时段')

    def rules_window(self):
        d = Dialog(self, '工作规则', 680)
        text = tk.Text(d.body, width=72, height=15, wrap='word', bg=PAPER, fg=TEXT, font=(FONT, 10), relief='flat', padx=12, pady=12)
        text.pack()
        for r in self.data['rule_notes']:
            text.insert('end', f'{r.get("ts", "")}\n{r.get("text", "")}\n\n')
        text.config(state='disabled')
        new = entry(d.body, '', 60)
        new.pack(fill='x', pady=(12, 0))
        def append():
            if new.get().strip():
                self.data['rule_notes'].append(dict(ts=f'{now():%Y-%m-%d %H:%M}', text=new.get().strip()))
                self.persist()
                d.destroy()
        button(d, '追加规则', append).pack(pady=(0, 15))

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title='备份全部数据', initialfile=f'Parcel-备份-{now():%Y%m%d}.json', defaultextension='.json', filetypes=[('JSON 数据', '*.json')])
        if path:
            if self.timer:
                self.timer.snapshot()
            Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.status.set('已备份全部任务、历史和设置。')

    def show(self):
        self.deiconify()
        self.state('normal')
        self.lift()

    def install_tray(self):
        try:
            import pystray
            from PIL import Image
            icon = Image.open(ASSETS/'tomato.ico')
            self.tray = pystray.Icon('Parcel', icon, 'Parcel · 日程与专注', pystray.Menu(
                pystray.MenuItem('打开 Parcel', lambda *a: self.actions.put('show'), default=True),
                pystray.MenuItem('打开番茄钟', lambda *a: self.actions.put('timer')),
                pystray.MenuItem('退出', lambda *a: self.actions.put('quit'))))
            def ready(icon):
                icon.visible = True
                self.actions.put('tray_ready')
            threading.Thread(target=lambda: self.tray.run(ready), daemon=True).start()
        except Exception:
            self.tray = None

    def poll_actions(self):
        if self.bridge:
            self.bridge.poll()
        while not self.actions.empty():
            action = self.actions.get_nowait()
            if action == 'show':
                self.show()
            elif action == 'timer':
                self.show()
                self.open_timer()
            elif action == 'quit':
                self.quit_app()
                return
            elif action == 'tray_ready':
                self.tray_ready = True
        self.poll_notion_sync()
        self.after(300, self.poll_actions)

    def close(self):
        if self.tray_ready:
            self.withdraw()
        else:
            self.quit_app()

    def quit_app(self):
        if self.bridge:
            self.bridge.close()
        if self.notion_worker:
            self.notion.stop.set()
            self.notion_worker.shutdown(wait=False, cancel_futures=True)
        if self.timer and self.timer.winfo_exists():
            self.timer.clock.pause()
            self.timer.snapshot()
        self.persist()
        self.writer.shutdown()
        if self.tray:
            self.tray.stop()
        for callback in self.tk.call('after', 'info'):
            # Leave Tcl command ownership with the widget that registered it.
            self.tk.call('after', 'cancel', callback)
        self.destroy()

    def report_callback_exception(self, exc_type, exc, tb):
        error = ''.join(traceback.format_exception(exc_type, exc, tb))
        (BASE/'data'/'error.log').write_text(error, encoding='utf-8')
        mb.showerror('Parcel 遇到问题', f'{exc}\n\n详细信息已保存到 data/error.log，当前数据仍保留。', parent=self)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Parcel desktop planner')
    parser.add_argument('--data-file', help='Use a separate local data file')
    parser.add_argument('--timer', action='store_true', help='Open the floating recommended timer directly')
    args = parser.parse_args()
    mutex = None
    if sys.platform == 'win32':
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\ParcelDesktopPlanner')
        if ctypes.windll.kernel32.GetLastError() == 183:
            mb.showinfo('Parcel 已在运行', '请从任务栏或右下角托盘打开 Parcel。')
            return
    try:
        app = App(Store(args.data_file) if args.data_file else None, timer_only=args.timer)
        if args.timer:
            app.withdraw()
            app.after(0, lambda: app.open_timer() if app.timer else app.start_recommended_intercept())
        app.mainloop()
    except Exception:
        (BASE/'data').mkdir(exist_ok=True)
        (BASE/'data'/'error.log').write_text(traceback.format_exc(), encoding='utf-8')
        mb.showerror('无法启动 Parcel', traceback.format_exc())
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
