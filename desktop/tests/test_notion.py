import copy
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from parcel_notion import NotionSync, RemoteRow, SyncError, decode, encode, project, reconcile
from test_core import dataset, task


PARENT = str(UUID(int=1))
TASK_PAGE = str(UUID(int=2))
NEW_PAGE = str(UUID(int=3))
HISTORY_PAGE = str(UUID(int=4))


def row(table, record, page, **edits):
    return RemoteRow(page, 'rev', dict(project(table, record, {}), id=record['id']) | edits)


def local_data(*tasks):
    return dict(dataset(*tasks), history=[], categories=['学习'])


class NotionTests(unittest.TestCase):
    def test_additions_merge_without_removing_or_duplicating_records(self):
        local = task()
        data = local_data(local)
        online = row('tasks', task(id='', title='线上任务'), NEW_PAGE)
        history = row('history', dict(id='', note='线上新增'), HISTORY_PAGE)
        snapshot = {'tasks': [online], 'history': [history]}
        changes, writes = reconcile(data, PARENT, snapshot, self.fail)
        online_key = UUID(NEW_PAGE).hex
        self.assertEqual([entry['id'] for entry in changes['tasks']], ['a', online_key])
        self.assertEqual(changes['history'][0]['note'], '线上新增')
        self.assertEqual({write.key for write in writes}, {'a', online_key, UUID(HISTORY_PAGE).hex})

        data.update(changes)
        remote_after_upload = {'tasks': [online, row('tasks', local, TASK_PAGE)], 'history': [history]}
        changes, _ = reconcile(data, PARENT, remote_after_upload, self.fail)
        self.assertEqual(len(changes['tasks']), 2)
        self.assertEqual(len(changes['history']), 1)

    def test_three_way_merge_preserves_parallel_edits_and_retries_upload(self):
        local = task(description='旧描述')
        data = local_data(local)
        initial = {'tasks': [row('tasks', local, TASK_PAGE)], 'history': []}
        changes, _ = reconcile(data, PARENT, initial, self.fail)
        data.update(changes)
        data['tasks'][0].update(description='本地新增', worked_minutes=25)
        online = copy.deepcopy(initial)
        online['tasks'][0].values.update(description='线上新增', priority=5)
        changes, writes = reconcile(data, PARENT, online, lambda _: None)
        self.assertEqual(changes['tasks'][0]['priority'], 5)
        self.assertEqual(writes[0].values, {'description': '本地新增\n\n线上新增', 'worked_minutes': 25})
        data.update(changes)
        _, retried = reconcile(data, PARENT, online, self.fail)
        self.assertEqual(retried, writes)

    def test_invalid_online_row_and_active_task_leave_local_data_alone(self):
        local, active = task(), task(id='active')
        data = local_data(local, active)
        data['active'] = {'task_id': 'active'}
        snapshot = {'tasks': [row('tasks', local, TASK_PAGE, due='无效日期'),
                              row('tasks', active, NEW_PAGE, title='线上改名')], 'history': []}
        messages = []
        changes, writes = reconcile(data, PARENT, snapshot, messages.append)
        self.assertIs(changes['tasks'][0], local)
        self.assertIs(changes['tasks'][1], active)
        self.assertFalse(writes)
        self.assertIn('跳过无效行', messages[0])

    def test_notions_properties_round_trip_and_query_pagination(self):
        record = dict(project('tasks', task(dependencies=['a', 'b'], description='测'*2100), {}), id='task')
        self.assertEqual(decode('tasks', {'properties': encode('tasks', record)}), record)
        sync = NotionSync(PARENT, 'test', Path('unused'))
        path = 'data_sources/example/query'
        with patch.object(sync, 'request', side_effect=[
            {'results': [1], 'has_more': True, 'next_cursor': 'next'},
            {'results': [2], 'has_more': False},
        ]) as request:
            self.assertEqual(list(sync.pages('POST', path)), [1, 2])
            request.assert_called_with('POST', path, {'page_size': 100, 'start_cursor': 'next'})
        with patch.object(sync, 'request', return_value={'request_status': {'type': 'incomplete'}}):
            with self.assertRaises(SyncError):
                list(sync.pages('POST', path))

    def test_remote_edit_during_upload_is_left_for_next_sync(self):
        local = task()
        data = local_data(local)
        snapshot = {'tasks': [row('tasks', local, TASK_PAGE)], 'history': []}
        changes, _ = reconcile(data, PARENT, snapshot, self.fail)
        data.update(changes)
        data['tasks'][0]['title'] = '本地改名'
        _, writes = reconcile(data, PARENT, snapshot, self.fail)
        sync = NotionSync(PARENT, 'test', Path('unused'))
        with patch.object(sync, 'log'), patch.object(sync, 'request', return_value={'last_edited_time': 'new'}) as request:
            self.assertFalse(sync.push(writes))
            request.assert_called_once_with('GET', f'pages/{TASK_PAGE}')
