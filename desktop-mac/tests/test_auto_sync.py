"""Exercise synchronization scheduling without opening native windows."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from parcel import App

class AutoSyncTests(unittest.TestCase):
    def test_disconnected_app_does_not_start_network_work(self):
        app = SimpleNamespace(notion=None, notion_worker=Mock(), notion_future=None, notion_setup_future=None)
        App.start_notion_sync(app)
        app.notion_worker.submit.assert_not_called()

    def test_connected_app_uses_existing_databases_and_never_overlaps(self):
        notion = SimpleNamespace(parent='page', pull=Mock())
        worker = Mock()
        app = SimpleNamespace(notion=notion, notion_worker=worker, notion_future=None,
                              notion_setup_future=None, data={'notion_sync':{'parent':'page','databases':{'tasks':'db'}}})
        App.start_notion_sync(app)
        worker.submit.assert_called_once_with(notion.pull, {'tasks':'db'})
        App.start_notion_sync(app)
        worker.submit.assert_called_once()

    def test_poll_is_rescheduled_even_if_attempt_fails(self):
        app = SimpleNamespace(start_notion_sync=Mock(side_effect=RuntimeError()), after=Mock(), auto_sync_tick=Mock())
        with patch('parcel.next_half_hour_delay', return_value=1234), self.assertRaises(RuntimeError):
            App.auto_sync_tick(app)
        app.after.assert_called_once_with(1234, app.auto_sync_tick)
