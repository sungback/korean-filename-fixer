import os
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from gui import App, should_run_startup_scan, startup_scan_skip_reason


class GuiTests(unittest.TestCase):
    def make_worker_app(self):
        app = object.__new__(App)
        app._cmd_queue = queue.Queue()
        app.after = Mock(side_effect=AssertionError("worker must not call Tk"))
        return app

    def test_should_run_startup_scan_requires_existing_folder_and_enabled_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(should_run_startup_scan(tmp, True))
            self.assertFalse(should_run_startup_scan(tmp, False))

    def test_should_run_startup_scan_rejects_missing_folder(self):
        self.assertFalse(should_run_startup_scan("", True))
        self.assertFalse(should_run_startup_scan("/path/does/not/exist", True))

    def test_should_run_startup_scan_skips_likely_sync_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive_root = os.path.join(
                tmp,
                "Library",
                "CloudStorage",
                "GoogleDrive-user@example.com",
                "\ub0b4 \ub4dc\ub77c\uc774\ube0c",
            )
            nested_folder = os.path.join(drive_root, "Project")
            os.makedirs(nested_folder)

            self.assertFalse(should_run_startup_scan(drive_root, True))
            self.assertIn("동기화", startup_scan_skip_reason(drive_root, True))
            self.assertTrue(should_run_startup_scan(nested_folder, True))

    def test_should_run_startup_scan_skips_large_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(3):
                open(os.path.join(tmp, f"file-{index}.txt"), "w").close()

            with patch("gui.STARTUP_SCAN_ENTRY_LIMIT", 2):
                self.assertFalse(should_run_startup_scan(tmp, True))
                self.assertIn("항목", startup_scan_skip_reason(tmp, True))

    def test_run_preview_queues_completion_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        results = [object()]

        with patch("gui.preview_folder", return_value=results):
            app._run_preview("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("preview_done", results, "folder", True),
        )
        app.after.assert_not_called()

    def test_run_preview_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        with patch("gui.preview_folder", side_effect=RuntimeError("boom")):
            app._run_preview("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("preview_failed", "folder", True, "boom"),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_completion_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        results = [object()]

        with patch("gui.convert_folder", return_value=results):
            app._run_startup_scan("folder", ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_done", results, "folder"),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        with patch("gui.convert_folder", side_effect=RuntimeError("boom")):
            app._run_startup_scan("folder", ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_failed", "folder", "boom"),
        )
        app.after.assert_not_called()

    def test_run_batch_convert_queues_completion_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        results = [object()]

        with patch("gui.convert_folder", return_value=results):
            app._run_batch_convert("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_done", results, "folder", True),
        )
        app.after.assert_not_called()

    def test_run_batch_convert_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        with patch("gui.convert_folder", side_effect=RuntimeError("boom")):
            app._run_batch_convert("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_failed", "folder", True, "boom"),
        )
        app.after.assert_not_called()

    def test_poll_queue_dispatches_worker_completion_commands(self):
        app = object.__new__(App)
        app._queue = queue.Queue()
        app._cmd_queue = queue.Queue()
        app._poll_after_id = None
        app._shutting_down = False
        app.after = Mock(return_value="after-id")
        app._log_result = Mock()
        app._on_preview_done = Mock()
        results = [object()]
        app._cmd_queue.put(("preview_done", results, "folder", True))

        app._poll_queue()

        app._on_preview_done.assert_called_once_with(results, "folder", True)
        self.assertEqual(app._poll_after_id, "after-id")


if __name__ == "__main__":
    unittest.main()
