"""Exercise native widget lifecycle and persisted timer boundaries with isolated data."""
import gc
import json
import tempfile
import unittest
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from parcel import App, Dialog, FloatTimer, StartDialog, TaskDialog, SettingsDialog, EventDialog
from parcel_core import Store, Clock, iso, now
from parcel_notion import NotionSync, RemoteRow, project
from test_core import task


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name)/'parcel.json')
        self.store.data['tasks'] = [task(minutes=50)]
        self.app = App(self.store, tray=False)
        self.app.withdraw()
        self.app.update()

    def tearDown(self):
        if self.app.winfo_exists():
            self.app.quit_app()
        self.app = None
        gc.collect()  # Release Tk objects on their owning thread, before the next test's writer starts.
        self.temp.cleanup()

    def active(self, phase='work', seconds=1500, elapsed=0):
        a = dict(task_id='a', phase=phase, round=0, total_seconds=seconds,
                 elapsed_seconds=elapsed, start=iso(now()), running=False, note='')
        self.app.data['active'] = a
        self.app.timer = FloatTimer(self.app, a)
        self.app.timer.withdraw()
        return self.app.timer

    def test_all_forms_construct_and_duration_field_is_visible(self):
        self.app.deiconify()
        self.app.update()
        d = StartDialog(self.app, self.app.data['tasks'][0])
        d.update()
        self.assertEqual(d.minutes.get(), '25')
        self.assertEqual(d.minutes.master.winfo_class(), 'Frame')
        self.assertTrue(d.minutes.winfo_ismapped())
        d.destroy()
        for form in [TaskDialog(self.app, self.app.data), SettingsDialog(self.app, self.app.cfg), EventDialog(self.app)]:
            form.update()
            form.destroy()

    def test_feedback_button_saves_multiline_feedback_separately(self):
        footer = self.app.winfo_children()[-1]
        next(widget for widget in footer.winfo_children() if widget.cget('text') == '问题与反馈').invoke()
        dialog = next(widget for widget in self.app.winfo_children() if isinstance(widget, Dialog))
        controls = dialog.winfo_children()[-1]
        submit = next(widget for widget in controls.winfo_children() if widget.cget('text') == '提交')
        with patch('parcel.mb.showwarning') as warning:
            submit.invoke()
            warning.assert_called_once()
        folder = self.store.path.with_suffix('.feedback')
        self.assertFalse(folder.exists())

        text = next(widget for widget in dialog.body.winfo_children() if widget.winfo_class() == 'Text')
        text.insert('1.0', '建议增加快捷键\n第二行说明')
        submit.invoke()
        feedback, = folder.glob('*.json')
        record = json.loads(feedback.read_text(encoding='utf-8'))
        self.assertEqual(record['message'], '建议增加快捷键\n第二行说明')
        self.assertTrue(record['created_at'])
        self.assertFalse(dialog.winfo_exists())

    def test_task_form_suggests_a_type_and_respects_manual_selection(self):
        form = TaskDialog(self.app, self.app.data)
        form.title_e.insert(0, '看几篇论文')
        self.assertIn('输入', form.kind_hint.cget('text'))
        form.save()
        self.assertEqual(form.result['task_type'], '输入')

        form = TaskDialog(self.app, self.app.data, initial={'title': '完善项目概念'})
        form.kind.set('输入')
        form.save()
        self.assertEqual(form.result['task_type'], '输入')

        form = TaskDialog(self.app, self.app.data, task=task(title='看论文', task_type='普通'))
        form.save()
        self.assertEqual(form.result['task_type'], '普通')

    def test_energy_status_expires_in_ui_and_survives_replanning(self):
        start = now().replace(microsecond=0)
        expires = start + timedelta(hours=1)
        with patch('parcel.now', return_value=start):
            self.app.energy.set('高精力')
            self.app.change_energy()
        self.assertEqual(Store(self.store.path).data['settings']['energy_until'], iso(expires))
        with patch('parcel.now', return_value=start + timedelta(minutes=30)):
            self.app.replan(periodic=True)
            self.assertEqual(self.app.energy.get(), '高精力')
            self.assertEqual(self.app.cfg['energy_until'], iso(expires))
        with patch('parcel.now', return_value=expires):
            self.app.replan(periodic=True)
            self.assertEqual(self.app.energy.get(), '正常')
            self.assertIsNone(self.app.energy_expiry)

    def test_notion_cycle_persists_online_rows_and_keeps_local_tasks(self):
        parent = '00000000-0000-0000-0000-000000000001'
        page = '00000000-0000-0000-0000-000000000002'
        online = dict(project('tasks', task(title='线上任务'), {}), id='')
        incoming, outgoing = Future(), Future()
        incoming.set_result({'tasks': [RemoteRow(page, 'revision', online)], 'history': []})
        outgoing.set_result({})
        self.app.notion = NotionSync(parent, 'test', self.store.path.with_suffix('.notion.log'))
        self.app.notion_worker = Mock()
        self.app.notion_worker.submit.side_effect = [incoming, outgoing]

        self.app.start_notion_sync()
        self.app.poll_notion_sync()
        self.app.poll_notion_sync()

        saved = Store(self.store.path).data
        self.assertEqual([record['title'] for record in saved['tasks']], ['a', '线上任务'])
        self.assertEqual(saved['notion_sync']['parent'], parent)
        _, writes = self.app.notion_worker.submit.call_args.args
        self.assertEqual({write.key for write in writes}, {'a', page.replace('-', '')})

    def test_task_row_starts_selected_timer_and_edit_marks_task_done(self):
        self.app.data['tasks'][0]['due'] = '2099-01-01'
        selected = task(id='b', worked_minutes=10)
        self.app.data['tasks'].append(selected)
        self.app.replan()

        def row_button(text):
            task_row = self.app.task_area.body.winfo_children()[1]
            controls = task_row.winfo_children()[2]
            return next(widget for widget in controls.winfo_children()
                        if widget.winfo_class() == 'Button' and widget.cget('text') == text)

        with patch.object(self.app, 'wait_window', side_effect=StartDialog.start), patch('parcel.mb.askyesno', return_value=True):
            row_button('开始').invoke()
        self.assertEqual(self.app.data['active']['task_id'], selected['id'])
        self.assertTrue(self.app.timer.clock.running)
        self.app.timer.stop_window()

        def mark_complete(dialog):
            footer = dialog.winfo_children()[-1]
            next(widget for widget in footer.winfo_children() if widget.cget('text') == '已完成').invoke()

        with patch.object(self.app, 'wait_window', side_effect=mark_complete):
            row_button('编辑').invoke()
        saved = Store(self.store.path).data['tasks']
        self.assertFalse(saved[0]['done'])
        self.assertTrue(saved[1]['done'])
        self.assertEqual(saved[1]['worked_minutes'], 10)
        self.assertEqual(len(self.app.task_area.body.winfo_children()), 1)

    def test_full_round_records_once_then_long_rest_on_fourth_work(self):
        timer = self.active(elapsed=1500)
        timer.active['round'] = 3
        timer.complete_phase()
        self.assertEqual(len(self.app.data['history']), 1)
        self.assertEqual(self.app.data['tasks'][0]['worked_minutes'], 25)
        self.assertEqual(timer.active['phase'], 'long_rest')
        self.assertEqual(timer.clock.total, 15*60)
        self.assertFalse(timer.clock.running)

    def test_partial_finish_records_elapsed_not_full_pomodoro(self):
        timer = self.active(elapsed=120)
        timer.finish_early()
        self.assertEqual(self.app.data['tasks'][0]['worked_minutes'], 2)
        self.assertEqual(self.app.data['history'][0]['minutes'], 2)
        self.assertTrue(self.app.data['history'][0]['partial'])
        self.assertIsNone(self.app.data['active'])

    def test_finished_task_rest_completion_destroys_timer_safely(self):
        self.app.data['tasks'][0].update(done=True, worked_minutes=50)
        timer = self.active(phase='rest', seconds=300, elapsed=300)
        timer.clock.start()
        timer.tick()
        self.assertIsNone(self.app.timer)
        self.assertIsNone(self.app.data['active'])

    def test_saved_progress_can_be_recovered_without_offline_work(self):
        timer = self.active(elapsed=123)
        timer.snapshot()
        self.app.persist()
        restored = Store(self.store.path)
        self.assertEqual(restored.data['active']['elapsed_seconds'], 123)
        self.assertEqual(restored.data['tasks'][0]['worked_minutes'], 0)
        self.assertEqual(restored.data['history'], [])

    def test_timer_height_fits_all_controls(self):
        timer = self.active()
        timer.deiconify()
        timer.update()
        self.assertGreaterEqual(timer.winfo_height(), timer.winfo_reqheight())

    def test_daily_progress_counts_whole_pomodoros_without_rounding(self):
        timer = self.active(elapsed=20*60)
        self.app.render_daily_progress()
        self.assertEqual(self.app.daily_label.cget('text'), '今天：0 个番茄')
        self.assertEqual(float(self.app.daily_bar['value']), 0)
        timer.finish_early()
        self.assertEqual(self.app.data['history'][0]['minutes'], 20)
        self.app.data['history'].append(dict(type='work', end=iso(now()), minutes=7))
        self.app.render_daily_progress()
        self.assertEqual(self.app.daily_label.cget('text'), '今天：1 个番茄')
        self.assertEqual(float(self.app.daily_bar['value']), 1)
        self.assertAlmostEqual(float(self.app.daily_bar['maximum']), 16)

    def test_periodic_replan_does_not_rebuild_lists_or_write(self):
        with (patch.object(self.app, 'render_tasks') as tasks,
              patch.object(self.app, 'render_history') as history,
              patch.object(self.app.store, 'save') as save):
            self.app.refresh_tick()
            tasks.assert_not_called()
            history.assert_not_called()
            save.assert_not_called()

    def test_explicit_save_is_newer_than_background_checkpoint(self):
        self.active(elapsed=120).snapshot()
        self.assertTrue(self.app.checkpoint())
        self.app.data['tasks'][0]['title'] = 'updated task'
        self.app.persist()
        self.assertEqual(Store(self.store.path).data['tasks'][0]['title'], 'updated task')

    def test_recovered_completed_session_is_not_logged_twice(self):
        timer = self.active(elapsed=1500)
        self.app.log_work(timer.task, timer.active, 25, '', True)
        self.app.log_work(timer.task, timer.active, 25, '', True)
        self.assertEqual(len(self.app.data['history']), 1)
        self.assertEqual(timer.task['worked_minutes'], 25)


if __name__ == '__main__':
    unittest.main()
