"""Local data, deterministic scheduling, and elapsed-time accounting for Parcel."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import sys
import time as wall_time
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Europe/London')
BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
DATA_DIR = Path.home() / 'Library' / 'Application Support' / 'Tomato' if sys.platform == 'darwin' else BASE / 'data'
POMODORO_MINUTES = 25


def now():
    return datetime.now(ZONE).replace(tzinfo=None)


def next_half_hour_delay(timestamp=None):
    """Milliseconds until London's next :00 or :30, including DST changes."""
    timestamp = wall_time.time() if timestamp is None else timestamp
    return math.ceil((1800 - timestamp % 1800) * 1000)


def uid():
    return uuid.uuid4().hex


def parse_dt(value, end_of_day=False):
    if not value:
        return None
    text = str(value).strip().replace('/', '-').replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            result = datetime.strptime(text, fmt)
            return result.replace(hour=23, minute=59, second=59) if end_of_day and fmt == '%Y-%m-%d' else result
        except ValueError:
            pass
    raise ValueError('日期请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM')


def iso(value):
    return value.isoformat(timespec='seconds')


def hm(value):
    return datetime.strptime(value, '%H:%M').time()


def remaining(task):
    if task.get('done'):
        return 0
    return max(0, float(task.get('estimated_minutes', 25)) - float(task.get('worked_minutes', 0)))


def default_settings():
    return {
        'work': 25, 'rest': 5, 'long_rest': 15, 'long_every': 4,
        'auto_next': False, 'force_note': False, 'horizon_days': 21,
        'availability': {str(i): [['08:00', '12:00'], ['19:00', '22:00']] if i < 5 else
                         [['10:00', '12:00'], ['14:00', '17:00'], ['19:00', '22:00']] for i in range(7)},
        'reserve_enabled': True, 'reserve_start': '21:00', 'reserve_end': '22:00',
        'energy': '正常', 'context': '',
    }


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else DATA_DIR / 'parcel.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.new = not self.path.exists()
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as exc:
                # Never silently replace a damaged user file with empty data.
                raise RuntimeError(f'无法读取数据文件：{self.path}\n请从 data/backups 恢复。\n{exc}') from exc
        else:
            self.data = {'version': 1, 'tasks': [], 'history': [], 'events': [],
                         'pins': [], 'schedule': [], 'categories': ['作品集'],
                         'rule_notes': [], 'settings': default_settings(), 'active': None}
            if path is None:
                self.import_legacy()
        defaults = default_settings()
        defaults.update(self.data.get('settings', {}))
        self.data['settings'] = defaults
        if self.new:
            self.save()

    def import_legacy(self):
        source = BASE / 'legacy' / 'tm_data.original.json'
        if not source.exists():
            source = Path(os.environ.get('APPDATA', '')) / 'TomatoManager' / 'tm_data.json'
        if not source.exists():
            return
        old = json.loads(source.read_text(encoding='utf-8'))
        self.data['settings'].update(old.get('rules', {}))
        work = self.data['settings']['work']
        for item in old.get('todos', []):
            t = copy.deepcopy(item)
            t.update(estimated_minutes=max(1, t.get('pomodoros_planned', 1)) * work,
                     worked_minutes=max(0, t.get('pomodoros_done', 0)) * work,
                     priority=3, earliest='', dependencies=[], task_type='普通', energy='正常',
                     context='', min_session=min(25, work), description='')
            self.data['tasks'].append(t)
        self.data['categories'] = old.get('categories', ['作品集'])
        self.data['rule_notes'] = old.get('rule_notes', [])
        self.data['history'] = old.get('history', [])
        for record in self.data['history']:
            record.setdefault('minutes', work if record.get('type') == 'work' else 0)
            record.setdefault('legacy', True)
        cfg_path = BASE / 'legacy' / 'scheduler.original.json'
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            self.data['settings']['availability'] = cfg.get('availability', self.data['settings']['availability'])
            self.data['settings']['horizon_days'] = cfg.get('horizon_days', 21)
            reserve = cfg.get('daily_leisure_reserve', {})
            self.data['settings']['reserve_start'] = reserve.get('start', '21:00')
            self.data['settings']['reserve_end'] = reserve.get('end', '22:00')
            for event in cfg.get('fixed_events', []):
                e = copy.deepcopy(event)
                e.setdefault('id', uid())
                self.data['events'].append(e)
        self.data['imported_from'] = str(source)

    def save(self, snapshot=None):
        if self.path.exists():
            backups = self.path.parent / 'backups'
            backups.mkdir(exist_ok=True)
            target = backups / f'parcel-{now():%Y-%m-%d}.json'
            if not target.exists():
                shutil.copy2(self.path, target)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as f:
            json.dump(self.data if snapshot is None else snapshot, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)


def subtract(intervals, blocks):
    result = list(intervals)
    for bs, be in sorted(blocks):
        updated = []
        for start, end in result:
            if be <= start or bs >= end:
                updated.append((start, end))
            else:
                if start < bs:
                    updated.append((start, bs))
                if be < end:
                    updated.append((be, end))
        result = updated
    return result


def merge_intervals(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        elif start < end:
            result.append((start, end))
    return result


def event_instances(events, start_day, days):
    output = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        for e in events:
            if e.get('date'):
                if parse_dt(e['date']).date() != day:
                    continue
            elif day.weekday() not in e.get('weekdays', []):
                continue
            start = datetime.combine(day, hm(e['start']))
            end = datetime.combine(day, hm(e['end']))
            if end <= start:
                end += timedelta(days=1)
            output.append({'id': e['id'] + ':' + str(day), 'event_id': e['id'],
                           'kind': 'fixed', 'title': e.get('title', e.get('name', '固定日程')),
                           'start': iso(start), 'end': iso(end)})
    return output


def schedule(data, at=None):
    """Schedule EDF with dependency urgency propagation. Never schedule past a deadline."""
    at = (at or now()).replace(microsecond=0)
    at = at.replace(second=0) + (timedelta(minutes=1) if at.second else timedelta())
    cfg = data['settings']
    days = int(cfg['horizon_days'])
    horizon = datetime.combine(at.date() + timedelta(days=days), time())
    all_tasks = {t['id']: t for t in data['tasks']}
    tasks = {k: t for k, t in all_tasks.items() if remaining(t) > 0}
    rem = {k: remaining(t) for k, t in tasks.items()}
    deadlines, errors = {}, {}
    for k, t in tasks.items():
        deadlines[k] = parse_dt(t.get('due'), True) or horizon
        if deadlines[k] <= at:
            errors[k] = '截止日期已过；请修改截止日期后重新排程'
        if any(d not in all_tasks for d in t.get('dependencies', [])):
            errors[k] = '依赖任务不存在；请编辑依赖关系'

    # Detect cycles without relying on task insertion order.
    visiting, visited = set(), set()
    def visit(k, trail):
        if k in visiting:
            for cyclic in trail[trail.index(k):]:
                errors[cyclic] = '依赖关系形成循环；请编辑依赖关系'
            return
        if k in visited:
            return
        visiting.add(k)
        for dep in tasks[k].get('dependencies', []):
            if dep in tasks:
                visit(dep, trail + [k])
        visiting.remove(k)
        visited.add(k)
    for k in tasks:
        visit(k, [])

    urgency = dict(deadlines)
    for _ in range(len(tasks)):
        changed = False
        for k, t in tasks.items():
            for dep in t.get('dependencies', []):
                if dep in tasks and urgency[dep] > urgency[k]:
                    urgency[dep] = urgency[k]
                    changed = True
        if not changed:
            break

    fixed = event_instances(data.get('events', []), at.date() - timedelta(days=1), days + 1)
    fixed = [b for b in fixed if parse_dt(b['end']) > at and parse_dt(b['start']) < horizon]
    blocks = list(fixed)
    conflicts = []
    fixed_intervals = [(parse_dt(b['start']), parse_dt(b['end'])) for b in fixed]
    for i, a in enumerate(fixed):
        for b in fixed[i+1:]:
            if max(parse_dt(a['start']), parse_dt(b['start'])) < min(parse_dt(a['end']), parse_dt(b['end'])):
                conflicts.append(f'固定日程重叠：{a["title"]} / {b["title"]}')
    completion = {k: at for k, t in all_tasks.items() if not remaining(t)}
    latest_work_end = {}
    slots, reserve = [], []
    for offset in range(days):
        day = at.date() + timedelta(days=offset)
        daily = []
        for a, b in cfg['availability'].get(str(day.weekday()), []):
            start, end = datetime.combine(day, hm(a)), datetime.combine(day, hm(b))
            if end > max(start, at):
                daily.append((max(start, at), end))
        if cfg.get('reserve_enabled'):
            rs, re_ = datetime.combine(day, hm(cfg['reserve_start'])), datetime.combine(day, hm(cfg['reserve_end']))
            if re_ > rs:
                reserve.append((rs, re_))
                blocks.append({'kind': 'reserve', 'title': '休闲 / 自由时间', 'start': iso(rs), 'end': iso(re_)})
        slots.extend(merge_intervals(daily))
    slots = subtract(slots, fixed_intervals + reserve)
    capacity = sum((e-s).total_seconds()/60 for s,e in slots)

    # Running work is reserved even if a new event now conflicts; the conflict is visible.
    active = data.get('active')
    active_intervals = []
    if active and active.get('phase') == 'work' and active.get('task_id') in tasks:
        k = active['task_id']
        # Elapsed active work is credited here provisionally; it becomes a history
        # record only at a phase boundary or when explicitly ended.
        rem[k] = max(0, rem[k] - active.get('elapsed_seconds', 0)/60)
        mins = min(rem[k], max(0, active.get('total_seconds', 0)-active.get('elapsed_seconds', 0))/60)
        if mins > 0:
            end = at + timedelta(minutes=mins)
            for fs, fe in fixed_intervals + reserve:
                if max(at, fs) < min(end, fe):
                    conflicts.append('正在执行的任务与固定日程或休闲保护时段重叠')
            if end > deadlines[k]:
                conflicts.append('正在执行的任务超过截止日期')
            blocks.append({'id': 'active', 'kind': 'active', 'task_id': k, 'title': tasks[k]['title'],
                           'start': iso(at), 'end': iso(end), 'minutes': mins})
            active_intervals.append((at, end + timedelta(minutes=cfg['rest'])))
            latest_work_end[k] = end
            rem[k] -= mins
            if rem[k] < .01:
                completion[k] = end
    slots = subtract(slots, active_intervals)

    # Pins are accepted only when still feasible; invalid pins remain visible as conflicts.
    accepted_pins = []
    for p in sorted(data.get('pins', []), key=lambda b: b['start']):
        start, end = parse_dt(p['start']), parse_dt(p['end'])
        k = p['task_id']
        if end <= at or k not in tasks:
            continue
        minutes = (end-start).total_seconds()/60
        deps = tasks[k].get('dependencies', [])
        valid = (start >= at and end <= deadlines[k] and start >= (parse_dt(tasks[k].get('earliest')) or at)
                 and minutes >= tasks[k].get('min_session', 25) and minutes <= rem[k] + .01
                 and all(d in completion and completion[d] <= start for d in deps)
                 and any(s <= start and end <= e for s,e in slots))
        if not valid:
            conflicts.append(f'锁定时段不可执行：{tasks[k]["title"]}；请解锁或调整')
            continue
        block = dict(p, kind='pinned', title=tasks[k]['title'], minutes=minutes)
        blocks.append(block)
        accepted_pins.append((start-timedelta(minutes=cfg['rest']), end + timedelta(minutes=cfg['rest'])))
        slots = subtract(slots, [accepted_pins[-1]])
        rem[k] -= minutes
        latest_work_end[k] = max(end, latest_work_end.get(k, end))
        if rem[k] < .01:
            completion[k] = latest_work_end[k]

    previous_type, previous_energy, previous_task = '', '', ''
    work_count = 0
    next_work_start = at
    old = {(b.get('task_id'), b['start']) for b in data.get('schedule', []) if b['kind'] == 'work'}
    for slot_start, slot_end in slots:
        cursor = max(slot_start, next_work_start)
        while cursor < slot_end:
            candidates, next_release = [], []
            for k, t in tasks.items():
                if rem[k] < .01 or k in errors:
                    continue
                deps = t.get('dependencies', [])
                if any(d not in completion for d in deps):
                    continue
                release = max([parse_dt(t.get('earliest')) or at] + [completion[d] for d in deps])
                if release > cursor:
                    next_release.append(release)
                    continue
                if t.get('context') and cfg.get('context') and t['context'] != cfg['context']:
                    continue
                limit = min(slot_end, deadlines[k])
                available = (limit-cursor).total_seconds()/60
                minimum = float(t.get('min_session', 25))
                if available + .001 < minimum or rem[k] + .001 < minimum:
                    continue
                # The final segment is never smaller than the explicit minimum.
                duration = min(max(float(cfg['work']), minimum), rem[k], available)
                tail = rem[k] - duration
                if .001 < tail < minimum:
                    if rem[k] <= available:
                        duration = rem[k]
                    elif duration-tail >= minimum:
                        duration -= minimum-tail
                    else:
                        continue
                if duration < minimum:
                    continue
                soft = (int(t.get('energy') == '高' and previous_energy == '高') +
                        int(cfg.get('energy') == '低精力' and t.get('energy') == '高') +
                        int(t.get('task_type') == previous_type and previous_type in ('输入', '输出')) +
                        int(cfg.get('energy') == '适合输入' and t.get('task_type') == '输出') +
                        int(cfg.get('energy') == '适合输出' and t.get('task_type') == '输入'))
                # Deadline and priority always outrank energy/type alternation.
                rank = (urgency[k], -int(t.get('priority', 3)), soft,
                        0 if (k, iso(cursor)) in old else 1, 0 if k == previous_task else 1, k)
                candidates.append((rank, k, duration))
            if not candidates:
                releases = [r for r in next_release if cursor < r < slot_end]
                if releases:
                    cursor = min(releases)
                    continue
                break
            _, k, duration = min(candidates)
            end = cursor + timedelta(minutes=duration)
            blocks.append({'id': f'{k}:{iso(cursor)}', 'kind': 'work', 'task_id': k,
                           'title': tasks[k]['title'], 'start': iso(cursor), 'end': iso(end), 'minutes': duration})
            rem[k] -= duration
            latest_work_end[k] = max(end, latest_work_end.get(k, end))
            if rem[k] < .01:
                completion[k] = latest_work_end[k]
            previous_type, previous_energy, previous_task = tasks[k].get('task_type'), tasks[k].get('energy'), k
            work_count += 1
            rest = cfg['long_rest'] if work_count % cfg['long_every'] == 0 else cfg['rest']
            rest_end = min(end + timedelta(minutes=rest), slot_end)
            if rest_end > end:
                blocks.append({'kind': 'break', 'title': '长休息' if rest == cfg['long_rest'] else '休息',
                               'start': iso(end), 'end': iso(rest_end)})
            cursor = end + timedelta(minutes=rest)
            next_work_start = cursor

    overflow = []
    for k, t in tasks.items():
        if rem[k] < .01:
            continue
        reason = errors.get(k)
        if not reason and any(d not in completion for d in t.get('dependencies', [])):
            reason = '前置任务未能完成；请先处理前置任务的冲突'
        if not reason and t.get('context') and cfg.get('context') and t['context'] != cfg['context']:
            reason = '当前地点 / 环境不符合任务要求'
        if not reason and rem[k] < t.get('min_session', 25):
            reason = '剩余工时小于最短连续时长；请调整最短时长'
        if not reason:
            reason = '截止日期前的可用时间不足，或时段短于最短连续时长' if t.get('due') else '滚动窗口容量不足，或时段短于最短连续时长'
        overflow.append({'task_id': k, 'title': t['title'], 'minutes': round(rem[k], 1), 'reason': reason})
    blocks.sort(key=lambda b: (b['start'], b['kind']))
    load = {}
    for b in blocks:
        if b['kind'] in ('work', 'active', 'pinned'):
            load[b['start'][:10]] = load.get(b['start'][:10], 0) + b['minutes']
    scheduled = sum(load.values())
    health = 'red' if overflow or conflicts else ('orange' if capacity and scheduled/capacity > .8 else 'green')
    return {'blocks': blocks, 'overflow': overflow, 'conflicts': conflicts, 'health': health,
            'capacity_minutes': round(capacity), 'scheduled_minutes': round(scheduled),
            'busiest': max(load, key=load.get) if load else '', 'load': load}


class Clock:
    """Wall elapsed time includes sleep; paused time never counts as work."""
    def __init__(self, total, source=None, elapsed=0):
        import time as time_module
        self.source = source or time_module.time
        self.total = float(total)
        self.accumulated = float(elapsed)
        self.started = None

    @property
    def elapsed(self):
        return min(self.total, max(0, self.accumulated + (max(0, self.source()-self.started) if self.started is not None else 0)))

    @property
    def left(self):
        return max(0, math.ceil(self.total-self.elapsed))

    def start(self):
        if self.started is None:
            self.started = self.source()

    def pause(self):
        self.accumulated = self.elapsed
        self.started = None

    @property
    def running(self):
        return self.started is not None


def quick_parse(text, at=None):
    """Explicit offline shorthand, not an AI model or an implicit constraint inference."""
    at = at or now()
    result = {'title': text.strip(), 'estimated_minutes': 25, 'priority': 3, 'due': ''}
    match = re.search(r'(\d+(?:\.\d+)?)\s*(小时|h|分钟|min|m)(?![a-z])', text, re.I)
    if match:
        result['estimated_minutes'] = float(match[1]) * (60 if match[2].lower() in ('小时', 'h') else 1)
        result['title'] = result['title'].replace(match[0], '').strip(' ，,')
    match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})(?:\s+(\d{1,2}:\d{2}))?', text)
    if match:
        result['due'] = match[0]
        result['title'] = result['title'].replace(match[0], '').strip(' ，,')
    else:
        for word, offset in [('后天', 2), ('明天', 1), ('今天', 0)]:
            if word in text:
                result['due'] = str(at.date() + timedelta(days=offset))
                result['title'] = result['title'].replace(word, '').strip(' ，,')
                break
    return result
