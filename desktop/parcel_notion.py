"""Optional Notion transport and additive, field-level synchronization."""
from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from parcel_core import now, parse_dt


TABLES = {'tasks': 'Parcel · 待办事项', 'history': 'Parcel · 专注历史'}
FIELDS = {
    'tasks': {
        'title': ('任务', 'title', ''),
        'category': ('大类', 'rich_text', '通用'),
        'estimated_minutes': ('预计分钟', 'number', 25),
        'worked_minutes': ('已投入分钟', 'number', 0),
        'min_session': ('最短连续分钟', 'number', None),
        'done': ('已完成', 'checkbox', False),
        'due': ('截止日期', 'rich_text', ''),
        'earliest': ('最早开始', 'rich_text', ''),
        'priority': ('优先级', 'number', 3),
        'task_type': ('任务类型', 'rich_text', '普通'),
        'energy': ('所需精力', 'rich_text', '正常'),
        'context': ('地点或环境', 'rich_text', ''),
        'description': ('任务描述', 'rich_text', ''),
        'dependencies': ('前置任务 ID', 'rich_text', []),
    },
    'history': {
        'title': ('任务', 'title', ''),
        'category': ('大类', 'rich_text', ''),
        'todo_id': ('关联任务 ID', 'rich_text', ''),
        'type': ('类型', 'rich_text', 'work'),
        'start': ('开始时间', 'rich_text', ''),
        'end': ('结束时间', 'rich_text', ''),
        'minutes': ('实际分钟', 'number', 25),
        'note': ('备注', 'rich_text', ''),
    },
}
ID_COLUMN = 'Parcel ID'


class SyncError(Exception):
    pass


def sync_log(path, message):
    try:
        with path.open('a', encoding='utf-8') as log:
            log.write(f'{now():%Y-%m-%d %H:%M:%S} {message}\n')
    except OSError:
        pass


def notion_token():
    token = os.environ.get('PARCEL_NOTION_TOKEN', '').strip()
    if not token and sys.platform == 'win32':
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                token = winreg.QueryValueEx(key, 'PARCEL_NOTION_TOKEN')[0].strip()
        except FileNotFoundError:
            pass
    return token


def rich_text(value):
    return [{'type': 'text', 'text': {'content': value[offset:offset+1000]}}
            for offset in range(0, len(value), 1000)]


def text_value(parts):
    return ''.join(part.get('plain_text', part.get('text', {}).get('content', '')) for part in parts)


def schema(table):
    return {ID_COLUMN: 'rich_text', **{name: kind for name, kind, _ in FIELDS[table].values()}}


def encode(table, values):
    properties = {}
    for field, value in values.items():
        name, kind, _ = (ID_COLUMN, 'rich_text', '') if field == 'id' else FIELDS[table][field]
        if field == 'dependencies':
            value = ', '.join(value)
        properties[name] = {kind: rich_text(value) if kind in ('title', 'rich_text') else value}
    return properties


def decode(table, page):
    properties = page['properties']
    values = {'id': text_value(properties[ID_COLUMN]['rich_text'])}
    for field, (name, kind, _) in FIELDS[table].items():
        value = properties[name][kind]
        if kind in ('title', 'rich_text'):
            value = text_value(value)
        if field == 'dependencies':
            value = [part.strip() for part in value.split(',') if part.strip()]
        values[field] = value
    return values


def project(table, record, tasks):
    values = {field: record.get(field, default) for field, (_, _, default) in FIELDS[table].items()}
    if table == 'tasks' and values['min_session'] is None:
        values['min_session'] = min(25, values['estimated_minutes'])
    if table == 'history':
        task = tasks.get(values['todo_id'], {})
        values['title'] = values['title'] or task.get('title') or ('休息' if values['type'] == 'rest' else '专注记录')
        values['category'] = values['category'] or task.get('category', '')
    return {field: '' if value is None else value for field, value in values.items()}


def normalize(table, values):
    values = dict(values)
    for field, (_, kind, default) in FIELDS[table].items():
        if kind == 'number':
            value = values[field]
            if value is None:
                value = min(25, values['estimated_minutes']) if field == 'min_session' else default
            if not math.isfinite(value) or not 0 <= value <= 10**7:
                raise ValueError(field)
            values[field] = value
    if not values['title'].strip():
        raise ValueError('title')
    if table == 'tasks':
        if not .1 <= values['min_session'] <= values['estimated_minutes']:
            raise ValueError('min_session / estimated_minutes')
        if values['priority'] not in range(1, 6):
            raise ValueError('priority')
        values['priority'] = int(values['priority'])
        dates = ('earliest', 'due')
    else:
        values['type'] = values['type'] or 'work'
        if values['type'] not in ('work', 'rest'):
            raise ValueError('type')
        dates = ('start', 'end')
    try:
        first = parse_dt(values[dates[0]])
        second = parse_dt(values[dates[1]], end_of_day=table == 'tasks')
    except ValueError:
        raise ValueError(' / '.join(dates)) from None
    if first and second and first > second:
        raise ValueError(' / '.join(dates))
    return values


@dataclass
class RemoteRow:
    page_id: str
    revision: str
    values: dict


@dataclass
class PendingWrite:
    table: str
    key: str
    row: RemoteRow | None
    values: dict


def reconcile(data, parent, remote, log):
    previous = data.get('notion_sync', {})
    if previous.get('parent') != parent:
        previous = {}
    state = {'parent': parent}
    tasks = {str(task['id']): task for task in data['tasks']}
    active = (data.get('active') or {}).get('task_id')
    changes = {'notion_sync': state}
    writes = []
    for table in TABLES:
        records = {str(record['id']): record for record in data[table]}
        base = previous.get(table, {})
        state[table] = dict(base)
        known_pages = {entry['page']: key for key, entry in base.items()}
        seen = set()
        for row in sorted(remote[table], key=lambda row: row.page_id not in known_pages):
            page_key = uuid.UUID(row.page_id).hex
            claimed = row.values['id']
            key = known_pages.get(row.page_id)
            if key is None:
                key = claimed if (claimed in records or claimed in base) and claimed not in seen else page_key
            if key in seen:
                key = page_key
            seen.add(key)
            if table == 'tasks' and key == active:
                continue
            record = records.get(key)
            raw = {field: row.values[field] for field in FIELDS[table]}
            try:
                online = normalize(table, raw)
                values = online.copy()
                if record is not None:
                    local = project(table, record, tasks)
                    baseline = base.get(key, {}).get('values', {})
                    for field, local_value in local.items():
                        remote_value = online[field]
                        old = baseline.get(field)
                        if local_value == remote_value or local_value == old:
                            continue
                        if remote_value == old:
                            values[field] = local_value
                        else:
                            log(f'合并冲突 {table}/{key} {field}')
                            if field in ('note', 'description') and local_value and remote_value:
                                values[field] = local_value + '\n\n' + remote_value
                            elif local_value not in ('', [], None) or remote_value in ('', [], None):
                                values[field] = local_value
                    values = normalize(table, values)
            except ValueError as exc:
                log(f'跳过无效行 {table}/{row.page_id}，请检查 {exc}')
                continue
            if record is None:
                records[key] = dict(values, id=key)
            else:
                updates = {field: value for field, value in values.items() if value != local[field]}
                if updates:
                    records[key] = dict(record, **updates)
            state[table][key] = {'page': row.page_id, 'values': online}
            patch = {field: value for field, value in values.items() if value != raw[field]}
            if claimed != key:
                patch['id'] = key
            if patch:
                writes.append(PendingWrite(table, key, row, patch))
        for key, record in records.items():
            if key not in seen and not (table == 'tasks' and key == active):
                writes.append(PendingWrite(table, key, None, dict(project(table, record, tasks), id=key)))
        changes[table] = list(records.values())
        if table == 'tasks':
            tasks = records
    categories = list(data['categories'])
    for task in changes['tasks']:
        category = task.get('category') or '通用'
        if category not in categories:
            categories.append(category)
    changes['categories'] = categories
    return changes, writes


class NotionSync:
    def __init__(self, parent, token, log_path):
        self.parent = parent
        self.token = token
        self.log_path = log_path
        self.stop = Event()
        self.next_request = 0
        self.sources = {}
        self.databases = {}

    @classmethod
    def load(cls, data_path):
        config_path = data_path.with_suffix('.notion.json')
        log_path = data_path.with_suffix('.notion.log')
        try:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
            parent = str(uuid.UUID(config['parent_page_id']))
            token = notion_token()
            if not token:
                raise SyncError('缺少 PARCEL_NOTION_TOKEN')
            return cls(parent, token, log_path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SyncError):
            sync_log(log_path, '配置未启用：请检查页面 ID、配置文件和 PARCEL_NOTION_TOKEN。')
            return None

    def log(self, message):
        sync_log(self.log_path, message)

    def request(self, method, path, body=None):
        payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') if body is not None else None
        request = Request('https://api.notion.com/v1/' + path, data=payload, method=method, headers={
            'Authorization': 'Bearer ' + self.token,
            'Notion-Version': '2026-03-11',
            'Content-Type': 'application/json',
        })
        for attempt in range(3):
            if self.stop.wait(max(0, self.next_request - time.monotonic())):
                raise SyncError('同步已停止')
            self.next_request = time.monotonic() + .36
            try:
                with urlopen(request, timeout=12) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    retry_after = exc.headers.get('Retry-After', '')
                    self.next_request = time.monotonic() + (int(retry_after) if retry_after.isdecimal() else 2)
                    exc.close()
                    continue
                exc.close()
                raise SyncError(f'Notion HTTP {exc.code}（{method} {path.split("/")[0]}）') from None
            except (OSError, URLError):
                raise SyncError('Notion 连接失败或超时') from None

    def pages(self, method, path):
        cursor = None
        while True:
            options = {'page_size': 100}
            if cursor:
                options['start_cursor'] = cursor
            response = (self.request(method, path, options) if method == 'POST'
                        else self.request(method, path + '?' + urlencode(options)))
            if response.get('request_status', {}).get('type') == 'incomplete':
                raise SyncError('Notion 返回的查询结果不完整')
            yield from response['results']
            if not response.get('has_more'):
                return
            next_cursor = response.get('next_cursor')
            if not next_cursor or next_cursor == cursor:
                raise SyncError('Notion 分页结果不完整')
            cursor = next_cursor

    def pull(self, known_databases):
        children = list(self.pages('GET', f'blocks/{self.parent}/children'))
        sources = {}
        databases = {}
        remote = {}
        for table, title in TABLES.items():
            known_id = known_databases.get(table)
            matches = [child['id'] for child in children if child['type'] == 'child_database'
                       and child['child_database']['title'] == title and not child.get('in_trash')]
            if not known_id and len(matches) > 1:
                raise SyncError(f'页面下有多个同名表：{title}')
            expected = schema(table)
            if known_id or matches:
                database = self.request('GET', f'databases/{known_id or matches[0]}')
                if database.get('in_trash'):
                    raise SyncError(f'请从 Notion 废纸篓恢复表格：{title}')
            else:
                database = self.request('POST', 'databases', {
                    'parent': {'type': 'page_id', 'page_id': self.parent},
                    'title': rich_text(title), 'is_inline': True,
                    'initial_data_source': {'properties': {name: {kind: {}} for name, kind in expected.items()}},
                })
            if len(database['data_sources']) != 1:
                raise SyncError(f'表格必须只有一个数据源：{title}')
            source = database['data_sources'][0]['id']
            properties = self.request('GET', f'data_sources/{source}')['properties']
            if any(properties.get(name, {}).get('type') != kind for name, kind in expected.items()):
                raise SyncError(f'表格列名或类型已变更：{title}')
            sources[table] = source
            databases[table] = database['id']
            remote[table] = [
                RemoteRow(page['id'], page['last_edited_time'], decode(table, page))
                for page in self.pages('POST', f'data_sources/{source}/query')
                if not page.get('in_trash') and not page.get('archived')
            ]
        self.sources = sources
        self.databases = databases
        return remote

    def push(self, writes):
        receipts = {}
        for write in writes:
            if self.stop.is_set():
                break
            table, key, row = write.table, write.key, write.row
            properties = encode(table, write.values)
            try:
                if row is None:
                    page = self.request('POST', 'pages', {
                        'parent': {'type': 'data_source_id', 'data_source_id': self.sources[table]},
                        'properties': properties,
                    })
                else:
                    path = f'pages/{row.page_id}'
                    current = self.request('GET', path)
                    if (current.get('in_trash') or current['last_edited_time'] != row.revision
                            or decode(table, current) != row.values):
                        self.log(f'行在同步期间被修改，下次重试 {table}/{key}')
                        continue
                    page = self.request('PATCH', path, {'properties': properties})
                values = (normalize(table, decode(table, page)) if row is None
                          else normalize(table, {**row.values, **write.values}))
                values.pop('id')
                receipts[table, key] = {'page': page['id'], 'values': values}
            except (SyncError, ValueError, KeyError, TypeError) as exc:
                message = str(exc) if isinstance(exc, SyncError) else '请检查列值'
                self.log(f'未能上传 {table}/{key}：{message}')
                if isinstance(exc, SyncError) and 'HTTP 400' not in message:
                    break
        return receipts
