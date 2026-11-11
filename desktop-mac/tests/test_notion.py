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

class AutomaticSyncSafetyTests(unittest.TestCase):
    def test_new_device_preserves_windows_ids_and_history_links(self):
        data = local_data()
        remote_task = task(id='windows-task')
        history = dict(id='history-one', todo_id='windows-task', minutes=25)
        changes, writes = reconcile(data, PARENT, {
            'tasks': [row('tasks', remote_task, TASK_PAGE)],
            'history': [row('history', history, HISTORY_PAGE)],
        }, self.fail)
        self.assertEqual(changes['tasks'][0]['id'], 'windows-task')
        self.assertEqual(changes['history'][0]['todo_id'], 'windows-task')
        self.assertFalse(writes)

    def test_parallel_title_edits_are_saved_and_never_uploaded_until_resolved(self):
        original = task(title='original')
        data = local_data(original)
        initial = {'tasks': [row('tasks', original, TASK_PAGE)], 'history': []}
        changes, _ = reconcile(data, PARENT, initial, self.fail)
        data.update(changes)
        data['tasks'][0]['title'] = 'Mac title'
        cloud = copy.deepcopy(initial)
        cloud['tasks'][0].values['title'] = 'Windows title'
        changes, writes = reconcile(data, PARENT, cloud, lambda _: None)
        self.assertFalse(writes)
        conflict = changes['notion_sync']['conflicts']['tasks/a/title']
        self.assertEqual((conflict['local'], conflict['remote']), ('Mac title', 'Windows title'))
        data.update(changes)
        changes, writes = reconcile(data, PARENT, cloud, self.fail)
        self.assertFalse(writes)
        self.assertEqual(changes['tasks'][0]['title'], 'Mac title')
        data.update(changes)
        data['notion_sync']['conflicts'].clear()  # User explicitly chooses local.
        _, writes = reconcile(data, PARENT, cloud, self.fail)
        self.assertEqual(writes[0].values, {'title': 'Mac title'})

    def test_active_timer_is_never_replaced_by_cloud_edits(self):
        local = task(id='active')
        data = local_data(local)
        data['active'] = {'task_id':'active'}
        changes, writes = reconcile(data, PARENT, {'tasks':[row('tasks', local, TASK_PAGE, worked_minutes=50)],'history':[]}, self.fail)
        self.assertEqual(changes['tasks'][0]['worked_minutes'], local['worked_minutes'])
        self.assertFalse(writes)

    def test_mac_keychain_lookup_does_not_expose_secret_in_config(self):
        import tempfile
        import json
        from parcel_notion import notion_token, save_notion_config
        with tempfile.TemporaryDirectory() as folder, patch('parcel_notion.sys.platform', 'darwin'), patch('parcel_notion.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'secret-value\n'
            with patch.dict('os.environ', {'PARCEL_NOTION_TOKEN':''}):
                self.assertEqual(notion_token(PARENT), 'secret-value')
            path = Path(folder)/'parcel.json'
            save_notion_config(path, PARENT, 'secret-value')
            config = path.with_suffix('.notion.json')
            self.assertEqual(json.loads(config.read_text()), {'parent_page_id':PARENT})
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('secret-value', config.read_text())

    def test_keychain_rejection_leaves_existing_configuration_untouched(self):
        import tempfile
        from parcel_notion import save_notion_config
        with tempfile.TemporaryDirectory() as folder, patch('parcel_notion.sys.platform', 'darwin'), patch('parcel_notion.subprocess.run') as run:
            path = Path(folder)/'parcel.json'
            config = path.with_suffix('.notion.json')
            config.write_text('original')
            run.return_value.returncode = 1
            with self.assertRaises(SyncError):
                save_notion_config(path, PARENT, 'test')
            self.assertEqual(config.read_text(), 'original')

class NewestEditTests(unittest.TestCase):
    def merge_conflict(self, local_time, remote_time):
        from parcel_notion import remember_local_edits
        original = task(title='old')
        data = local_data(original)
        remote = {'tasks':[row('tasks', original, TASK_PAGE)],'history':[]}
        changes,_ = reconcile(data,PARENT,remote,self.fail)
        data.update(changes)
        before = copy.deepcopy(data)
        data['tasks'][0]['title'] = 'Mac'
        remember_local_edits(data,before,local_time)
        remote['tasks'][0].values['title'] = 'Windows'
        remote['tasks'][0].revision = remote_time
        return reconcile(data,PARENT,remote,self.fail)

    def test_newer_cloud_value_wins_and_both_versions_are_archived(self):
        changes,writes = self.merge_conflict('2026-11-06T10:00:00+00:00','2026-11-06T10:01:00Z')
        self.assertEqual(changes['tasks'][0]['title'],'Windows')
        self.assertFalse(writes)
        archive = changes['notion_sync']['conflict_archive']['tasks/a/title'][0]
        self.assertEqual((archive['local'],archive['remote']),('Mac','Windows'))
        self.assertFalse(changes['notion_sync']['conflicts'])

    def test_newer_local_value_is_uploaded_without_changing_other_fields(self):
        changes,writes = self.merge_conflict('2026-11-06T10:02:00+00:00','2026-11-06T10:01:00Z')
        self.assertEqual(changes['tasks'][0]['title'],'Mac')
        self.assertEqual(writes[0].values,{'title':'Mac'})

    def test_timer_only_save_does_not_mark_task_fields_as_new_edits(self):
        from parcel_notion import remember_local_edits
        data = local_data(task())
        before = copy.deepcopy(data)
        data['active'] = {'elapsed_seconds':5}
        self.assertFalse(remember_local_edits(data,before))
        self.assertEqual(data['sync_local_edits'],{})
