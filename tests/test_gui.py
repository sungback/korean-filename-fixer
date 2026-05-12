import os
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from converter import (
    ConvertResult,
    should_run_startup_scan,
    startup_scan_skip_reason,
)
from gui import App


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

            with patch("converter.STARTUP_SCAN_ENTRY_LIMIT", 2):
                self.assertFalse(should_run_startup_scan(tmp, True))
                self.assertIn("항목", startup_scan_skip_reason(tmp, True))

    def test_load_config_starts_watch_when_startup_scan_is_skipped(self):
        app = object.__new__(App)
        app.exclude_var = Mock()
        app.scan_on_startup_var = Mock()
        app.scan_on_startup_var.get.return_value = True
        app.remember_var = Mock()
        app.folder_var = Mock()
        app.status_var = Mock()
        app._format_exclude_patterns = Mock(return_value=".git")
        app._sync_autostart_state = Mock()
        app._start_startup_scan = Mock()
        app._start_watch = Mock()
        app._log = Mock()

        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as config:
                config.write('{"folder": "/Users/back/내 드라이브"}')

            with patch("gui.CONFIG_PATH", config_path):
                with patch("gui.os.path.isdir", return_value=True):
                    with patch(
                        "gui.startup_scan_skip_reason",
                        return_value="동기화 루트는 시작 자동 스캔만 건너뜁니다.",
                    ):
                        app._load_config()

        app.folder_var.set.assert_called_once_with("/Users/back/내 드라이브")
        app._start_startup_scan.assert_not_called()
        app._start_watch.assert_called_once_with()
        app.status_var.set.assert_any_call("시작 시 자동 스캔 건너뜀 — 감시는 정상적으로 시작합니다.")

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
        cancel_event = threading.Event()

        with patch("gui.convert_folder", return_value=results):
            app._run_startup_scan("folder", ["node_modules"], cancel_event)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_done", results, "folder"),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_progress_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        cancel_event = threading.Event()

        def convert_with_progress(*_args, progress_callback=None, **_kwargs):
            progress_callback(("collect", 1200, None))
            progress_callback(("convert", 300, 1200))
            return []

        with patch("gui.convert_folder", side_effect=convert_with_progress):
            app._run_startup_scan("folder", ["node_modules"], cancel_event)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "시작 스캔", "collect", 1200, None),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "시작 스캔", "convert", 300, 1200),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_done", [], "folder"),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_cancelled_when_event_is_set(self):
        app = self.make_worker_app()
        results = [object()]
        cancel_event = threading.Event()

        def convert_and_cancel(*_args, **_kwargs):
            cancel_event.set()
            return results

        with patch("gui.convert_folder", side_effect=convert_and_cancel):
            app._run_startup_scan("folder", ["node_modules"], cancel_event)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_cancelled", results, "folder"),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        cancel_event = threading.Event()

        with patch("gui.convert_folder", side_effect=RuntimeError("boom")):
            app._run_startup_scan("folder", ["node_modules"], cancel_event)

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

    def test_run_batch_convert_queues_progress_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        def convert_with_progress(*_args, progress_callback=None, **_kwargs):
            progress_callback(("collect", 500, None))
            progress_callback(("convert", 10, 500))
            return []

        with patch("gui.convert_folder", side_effect=convert_with_progress):
            app._run_batch_convert("folder", False, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "일괄 변환", "collect", 500, None),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "일괄 변환", "convert", 10, 500),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_done", [], "folder", False),
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

    def test_poll_queue_dispatches_progress_commands(self):
        app = object.__new__(App)
        app._queue = queue.Queue()
        app._cmd_queue = queue.Queue()
        app._poll_after_id = None
        app._shutting_down = False
        app.after = Mock(return_value="after-id")
        app._log_result = Mock()
        app._on_operation_progress = Mock()
        app._cmd_queue.put(("operation_progress", "시작 스캔", "convert", 25, 100))

        app._poll_queue()

        app._on_operation_progress.assert_called_once_with("시작 스캔", "convert", 25, 100)
        self.assertEqual(app._poll_after_id, "after-id")

    def test_operation_progress_updates_status_for_collection(self):
        app = object.__new__(App)
        app.status_var = Mock()

        app._on_operation_progress("시작 스캔", "collect", 1234, None)

        app.status_var.set.assert_called_once_with("시작 스캔: 항목 수집 중... 1,234개 발견")

    def test_operation_progress_updates_status_for_conversion(self):
        app = object.__new__(App)
        app.status_var = Mock()

        app._on_operation_progress("일괄 변환", "convert", 25, 100)

        app.status_var.set.assert_called_once_with("일괄 변환: 25/100개 처리 중...")

    def test_health_check_does_not_restart_when_watch_is_paused_for_operation(self):
        app = object.__new__(App)
        app._shutting_down = False
        app._startup_scan_in_progress = False
        app._watch_paused_for_operation = True
        app.btn_stop = {"state": "normal"}
        app.folder_var = Mock()
        app.folder_var.get.return_value = "/tmp/example"
        app._get_exclude_patterns = Mock(return_value=[])
        app.watcher = Mock()
        app.watcher.is_running = False
        app._log = Mock()
        app.status_var = Mock()
        app._update_tray_title = Mock()
        app._update_tray_menu_state = Mock()
        app.after = Mock(return_value="after-id")

        with patch("gui._APPKIT", False):
            app._health_check()

        app.watcher.start.assert_not_called()
        self.assertEqual(app._health_check_after_id, "after-id")

    def test_button_options_keep_text_readable_in_dark_mode(self):
        app = object.__new__(App)
        app._dark = True

        options = app._button_options()

        self.assertNotEqual(options["fg"], options["bg"])
        self.assertNotEqual(options["activeforeground"], options["activebackground"])
        self.assertNotEqual(options["disabledforeground"], options["bg"])
        self.assertEqual(options["fg"], "#111111")
        self.assertEqual(options["activeforeground"], "#111111")
        self.assertEqual(options["disabledforeground"], "#4a4a4a")

    def test_button_helper_applies_readable_options_to_tk_buttons(self):
        app = object.__new__(App)
        app._button_options = Mock(return_value={
            "fg": "#111111",
            "bg": "#f2f2f7",
            "activeforeground": "#111111",
            "activebackground": "#e5e5ea",
            "disabledforeground": "#8e8e93",
        })
        parent = Mock()
        button = Mock()

        with patch("gui.tk.Button", return_value=button) as tk_button:
            result = app._button(parent, text="변환", command="callback")

        self.assertEqual(result, button)
        tk_button.assert_called_once_with(
            parent,
            fg="#111111",
            bg="#f2f2f7",
            activeforeground="#111111",
            activebackground="#e5e5ea",
            disabledforeground="#8e8e93",
            text="변환",
            command="callback",
        )

    def test_startup_scan_running_turns_stop_button_into_skip_button(self):
        app = object.__new__(App)
        app._startup_scan_cancel_event = None
        app.watcher = Mock(is_running=False)
        app.btn_start = Mock()
        app.btn_stop = Mock()
        app.btn_preview = Mock()
        app.btn_once = Mock()

        app._set_startup_scan_running(True)

        app.btn_start.config.assert_called_once_with(state="disabled")
        app.btn_stop.config.assert_called_once_with(state="normal", text="스캔 건너뛰기")
        app.btn_preview.config.assert_called_once_with(state="disabled")
        app.btn_once.config.assert_called_once_with(state="disabled")

    def test_startup_scan_running_false_restores_stop_button_for_watcher_state(self):
        app = object.__new__(App)
        app._startup_scan_cancel_event = threading.Event()
        app.watcher = Mock(is_running=False)
        app.btn_start = Mock()
        app.btn_stop = Mock()
        app.btn_preview = Mock()
        app.btn_once = Mock()

        app._set_startup_scan_running(False)

        app.btn_start.config.assert_called_once_with(state="normal")
        app.btn_stop.config.assert_called_once_with(state="disabled", text="■ 중지")
        app.btn_preview.config.assert_called_once_with(state="normal")
        app.btn_once.config.assert_called_once_with(state="normal")
        self.assertIsNone(app._startup_scan_cancel_event)

    def test_skip_startup_scan_sets_cancel_event_and_updates_ui(self):
        app = object.__new__(App)
        app._startup_scan_in_progress = True
        app._startup_scan_cancel_event = threading.Event()
        app.status_var = Mock()
        app.btn_stop = Mock()
        app._log = Mock()

        app._skip_startup_scan()

        self.assertTrue(app._startup_scan_cancel_event.is_set())
        app.status_var.set.assert_called_once_with("시작 스캔을 건너뛰는 중...")
        app.btn_stop.config.assert_called_once_with(state="disabled", text="건너뛰는 중...")
        app._log.assert_called_once_with("시작 시 누락분 스캔 건너뛰기 요청", "info")

    def test_startup_scan_cancelled_logs_partial_results_and_starts_watch(self):
        app = object.__new__(App)
        app._set_startup_scan_running = Mock()
        app._start_watch = Mock()
        app._sync_folder_after_conversion = Mock(return_value="folder")
        app._log_result = Mock()
        app._log = Mock()
        app.status_var = Mock()
        results = [
            ConvertResult("a", "a", "a", "converted"),
            ConvertResult("b", "b", "b", "skipped"),
        ]

        app._on_startup_scan_cancelled(results, "folder")

        self.assertEqual(app._log_result.call_count, 2)
        app._log_result.assert_any_call(results[0])
        app._log_result.assert_any_call(results[1])
        app.status_var.set.assert_called_once()
        self.assertIn("건너뜀", app.status_var.set.call_args.args[0])
        app._set_startup_scan_running.assert_called_once_with(False)
        app._sync_folder_after_conversion.assert_called_once_with("folder", results)
        app._start_watch.assert_called_once_with()

    def test_startup_scan_cancelled_syncs_converted_root_before_starting_watch(self):
        app = object.__new__(App)
        app._set_startup_scan_running = Mock()
        app._start_watch = Mock()
        app._sync_folder_after_conversion = Mock(return_value="new-folder")
        app._log_result = Mock()
        app._log = Mock()
        app.status_var = Mock()
        results = [ConvertResult("new-folder", "old-folder", "new-folder", "converted")]

        app._on_startup_scan_cancelled(results, "old-folder")

        app._sync_folder_after_conversion.assert_called_once_with("old-folder", results)
        app._start_watch.assert_called_once_with()

    def test_log_area_wraps_lines_to_visible_width(self):
        app = object.__new__(App)
        app._get_theme_colors = Mock(return_value={
            "bg": "#ffffff",
            "fg": "#333333",
            "converted": "#007700",
            "preview": "#005fcc",
            "conflict": "#b35a00",
            "error": "#cc0000",
        })
        log_widget = Mock()

        with patch("gui.scrolledtext.ScrolledText", return_value=log_widget) as scrolled_text:
            app._build_log_area()

        scrolled_text.assert_called_once()
        self.assertEqual(scrolled_text.call_args.kwargs["wrap"], "word")
        log_widget.pack.assert_called_once_with(fill="both", expand=True)

    def test_log_area_binds_copy_shortcuts_for_disabled_log_widget(self):
        app = object.__new__(App)
        app._get_theme_colors = Mock(return_value={
            "bg": "#ffffff",
            "fg": "#333333",
            "converted": "#007700",
            "preview": "#005fcc",
            "conflict": "#b35a00",
            "error": "#cc0000",
        })
        log_widget = Mock()

        with patch("gui.scrolledtext.ScrolledText", return_value=log_widget):
            app._build_log_area()

        log_widget.bind.assert_any_call("<Command-c>", app._copy_log_selection)
        log_widget.bind.assert_any_call("<Control-c>", app._copy_log_selection)

    def test_copy_log_selection_copies_selected_text_to_clipboard(self):
        app = object.__new__(App)
        app.log = Mock()
        app.log.get.return_value = "선택한 로그"
        app.clipboard_clear = Mock()
        app.clipboard_append = Mock()

        result = app._copy_log_selection(None)

        app.log.get.assert_called_once_with("sel.first", "sel.last")
        app.clipboard_clear.assert_called_once_with()
        app.clipboard_append.assert_called_once_with("선택한 로그")
        self.assertEqual(result, "break")


if __name__ == "__main__":
    unittest.main()
