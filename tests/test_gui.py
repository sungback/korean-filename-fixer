import os
import queue
import tempfile
import threading
import unittest
from unittest.mock import ANY, Mock, patch

from converter import ConvertResult
from gui import App


class GuiTests(unittest.TestCase):
    def make_worker_app(self):
        app = object.__new__(App)
        app._cmd_queue = queue.Queue()
        app.after = Mock(side_effect=AssertionError("worker must not call Tk"))
        return app

    def make_poll_app(self):
        app = object.__new__(App)
        app._queue = queue.Queue()
        app._cmd_queue = queue.Queue()
        app._poll_after_id = None
        app._shutting_down = False
        app.after = Mock(return_value="after-id")
        app._log_result = Mock()
        return app

    def test_load_config_starts_watch_when_startup_scan_is_skipped(self):
        app = object.__new__(App)
        app.exclude_var = Mock()
        app.scan_on_startup_var = Mock()
        app.scan_on_startup_var.get.return_value = True
        app.notify_on_convert_var = Mock()
        app.remember_var = Mock()
        app._folders = []
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

        self.assertEqual(app._get_folders(), [os.path.abspath("/Users/back/내 드라이브")])
        app._start_startup_scan.assert_not_called()
        app._start_watch.assert_called_once_with()
        app.status_var.set.assert_any_call("시작 시 자동 스캔 건너뜀 — 감시는 정상적으로 시작합니다.")

    def test_load_config_migrates_folders_list(self):
        app = object.__new__(App)
        app.exclude_var = Mock()
        app.scan_on_startup_var = Mock()
        app.scan_on_startup_var.get.return_value = False
        app.notify_on_convert_var = Mock()
        app.remember_var = Mock()
        app._folders = []
        app.status_var = Mock()
        app._format_exclude_patterns = Mock(return_value=".git")
        app._sync_autostart_state = Mock()
        app._start_startup_scan = Mock()
        app._start_watch = Mock()
        app._log = Mock()

        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as config:
                config.write('{"folders": ["/tmp/a", "/tmp/b"]}')

            with patch("gui.CONFIG_PATH", config_path):
                with patch("gui.os.path.isdir", return_value=True):
                    app._load_config()

        self.assertEqual(app._get_folders(), [os.path.abspath("/tmp/a"), os.path.abspath("/tmp/b")])
        app._start_watch.assert_called_once_with()

    def test_run_preview_queues_completion_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        results = [object()]

        with patch("gui.preview_folder", return_value=results):
            app._run_preview("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("preview_done", results, ["folder"], True),
        )
        app.after.assert_not_called()

    def test_run_preview_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        with patch("gui.preview_folder", side_effect=RuntimeError("boom")):
            app._run_preview("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("preview_failed", ["folder"], True, "boom"),
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
            ("startup_scan_done", results, ["folder"]),
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
            ("operation_progress", "시작 스캔(1/1)", "collect", 1200, None),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "시작 스캔(1/1)", "convert", 300, 1200),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_done", [], ["folder"]),
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
            ("startup_scan_cancelled", results, ["folder"]),
        )
        app.after.assert_not_called()

    def test_run_startup_scan_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        cancel_event = threading.Event()

        with patch("gui.convert_folder", side_effect=RuntimeError("boom")):
            app._run_startup_scan("folder", ["node_modules"], cancel_event)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("startup_scan_failed", ["folder"], "boom"),
        )
        app.after.assert_not_called()

    def test_run_batch_convert_queues_completion_without_calling_tk_from_worker(self):
        app = self.make_worker_app()
        results = [object()]

        with patch("gui.convert_folder", return_value=results):
            app._run_batch_convert("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_done", results, ["folder"], True),
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
            ("operation_progress", "일괄 변환(1/1)", "collect", 500, None),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("operation_progress", "일괄 변환(1/1)", "convert", 10, 500),
        )
        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_done", [], ["folder"], False),
        )
        app.after.assert_not_called()

    def test_run_batch_convert_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_worker_app()

        with patch("gui.convert_folder", side_effect=RuntimeError("boom")):
            app._run_batch_convert("folder", True, ["node_modules"])

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("batch_failed", ["folder"], True, "boom"),
        )
        app.after.assert_not_called()

    def test_poll_queue_dispatches_worker_completion_commands(self):
        app = self.make_poll_app()
        app._on_preview_done = Mock()
        results = [object()]
        app._cmd_queue.put(("preview_done", results, ["folder"], True))

        app._poll_queue()

        app._on_preview_done.assert_called_once_with(results, ["folder"], True)
        self.assertEqual(app._poll_after_id, "after-id")

    def test_poll_queue_dispatches_progress_commands(self):
        app = self.make_poll_app()
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
        app._folders = ["/tmp/example"]
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

        app.watcher.start_many.assert_not_called()
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


class CallbackTests(unittest.TestCase):
    def _make_app(self, extra_attrs=None):
        app = object.__new__(App)
        app._log_result = Mock()
        app._log = Mock()
        app._resume_watch = Mock()
        app._sync_folder_after_conversion = Mock(side_effect=lambda f, r: f)
        app._sync_folders_after_conversion = Mock(side_effect=lambda f, r: App._as_folder_list(f))
        app._folders = []
        app.status_var = Mock()
        app.notify_on_convert_var = Mock()
        app._send_notification = Mock()
        if extra_attrs:
            for k, v in extra_attrs.items():
                setattr(app, k, v)
        return app

    def test_on_batch_done_logs_results_sets_status_and_enables_button(self):
        app = self._make_app({"btn_once": Mock()})
        results = [
            ConvertResult("a", "a", "a", "converted"),
            ConvertResult("b", "b", "b", "conflict"),
        ]

        app._on_batch_done(results, ["/tmp/folder"], resume_watch=False)

        self.assertEqual(app._log_result.call_count, 2)
        app.btn_once.config.assert_called_with(state="normal")
        status = app.status_var.set.call_args.args[0]
        self.assertIn("완료", status)
        self.assertIn("1", status)  # 변환 1개

    def test_on_batch_done_resumes_watch_when_flag_set(self):
        app = self._make_app({"btn_once": Mock()})
        app._on_batch_done([], ["/tmp/folder"], resume_watch=True)
        app._resume_watch.assert_called_once_with(["/tmp/folder"])

    def test_on_batch_done_clears_paused_flag_when_no_resume(self):
        app = self._make_app({"btn_once": Mock()})
        app._watch_paused_for_operation = True
        app._on_batch_done([], ["/tmp/folder"], resume_watch=False)
        app._resume_watch.assert_not_called()
        self.assertFalse(app._watch_paused_for_operation)

    def test_on_batch_failed_sets_error_status_and_enables_button(self):
        app = self._make_app({"btn_once": Mock()})
        app._on_batch_failed(["/tmp/folder"], resume_watch=False, error="디스크 오류")
        status = app.status_var.set.call_args.args[0]
        self.assertIn("디스크 오류", status)
        app.btn_once.config.assert_called_with(state="normal")

    def test_on_preview_done_logs_results_sets_status_and_enables_button(self):
        app = self._make_app({"btn_preview": Mock()})
        results = [
            ConvertResult("a", "a", "a", "preview"),
            ConvertResult("b", "b", "b", "skipped"),
        ]

        app._on_preview_done(results, ["/tmp/folder"], resume_watch=False)

        self.assertEqual(app._log_result.call_count, 2)
        app.btn_preview.config.assert_called_with(state="normal")
        status = app.status_var.set.call_args.args[0]
        self.assertIn("미리보기 완료", status)

    def test_on_preview_done_resumes_watch_when_flag_set(self):
        app = self._make_app({"btn_preview": Mock()})
        app._on_preview_done([], ["/tmp/folder"], resume_watch=True)
        app._resume_watch.assert_called_once_with(["/tmp/folder"])

    def test_on_preview_failed_sets_error_status_and_enables_button(self):
        app = self._make_app({"btn_preview": Mock()})
        app._on_preview_failed(["/tmp/folder"], resume_watch=False, error="권한 없음")
        status = app.status_var.set.call_args.args[0]
        self.assertIn("권한 없음", status)
        app.btn_preview.config.assert_called_with(state="normal")

    def test_on_startup_scan_done_logs_results_stops_scan_and_starts_watch(self):
        app = self._make_app()
        app._set_startup_scan_running = Mock()
        app._start_watch = Mock()
        results = [ConvertResult("a", "a", "a", "converted")]

        app._on_startup_scan_done(results, ["/tmp/folder"])

        self.assertEqual(app._log_result.call_count, 1)
        app._set_startup_scan_running.assert_called_once_with(False)
        app._sync_folders_after_conversion.assert_called_once_with(["/tmp/folder"], results)
        app._start_watch.assert_called_once_with()
        status = app.status_var.set.call_args.args[0]
        self.assertIn("완료", status)

    def test_on_startup_scan_cancelled_logs_partial_results_and_starts_watch(self):
        app = self._make_app({
            "_set_startup_scan_running": Mock(),
            "_start_watch": Mock(),
        })
        results = [
            ConvertResult("a", "a", "a", "converted"),
            ConvertResult("b", "b", "b", "skipped"),
        ]

        app._on_startup_scan_cancelled(results, ["folder"])

        self.assertEqual(app._log_result.call_count, 2)
        app._log_result.assert_any_call(results[0], notify=False)
        app._log_result.assert_any_call(results[1], notify=False)
        app.status_var.set.assert_called_once()
        self.assertIn("건너뜀", app.status_var.set.call_args.args[0])
        app._set_startup_scan_running.assert_called_once_with(False)
        app._sync_folders_after_conversion.assert_called_once_with(["folder"], results)
        app._start_watch.assert_called_once_with()

    def test_on_startup_scan_cancelled_syncs_converted_root_before_starting_watch(self):
        app = self._make_app({
            "_set_startup_scan_running": Mock(),
            "_start_watch": Mock(),
            "_sync_folders_after_conversion": Mock(return_value=["new-folder"]),
        })
        results = [ConvertResult("new-folder", "old-folder", "new-folder", "converted")]

        app._on_startup_scan_cancelled(results, ["old-folder"])

        app._sync_folders_after_conversion.assert_called_once_with(["old-folder"], results)
        app._start_watch.assert_called_once_with()


class UpdateCheckTests(unittest.TestCase):
    def make_update_app(self, extra_attrs=None):
        app = object.__new__(App)
        app._cmd_queue = queue.Queue()
        app.after = Mock(side_effect=AssertionError("worker must not call Tk"))
        app.status_var = Mock()
        app.btn_update = Mock()
        app._log = Mock()
        app.remember_var = Mock()
        app.remember_var.get.return_value = False
        app._save_config = Mock()
        app._last_update_check = 0.0
        if extra_attrs:
            for k, v in extra_attrs.items():
                setattr(app, k, v)
        return app

    def test_run_update_check_queues_newer_without_calling_tk_from_worker(self):
        app = self.make_update_app()

        with patch("gui.fetch_latest_release",
                   return_value=("v9.9.9", "https://example.com/r")):
            app._run_update_check(False)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("update_check_done", True, "v9.9.9", "https://example.com/r", False),
        )
        app.after.assert_not_called()

    def test_run_update_check_queues_failure_without_calling_tk_from_worker(self):
        app = self.make_update_app()

        with patch("gui.fetch_latest_release", return_value=None):
            app._run_update_check(True)

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("update_check_done", False, "", "", True),
        )
        app.after.assert_not_called()

    def test_on_update_check_done_prompts_download_when_newer(self):
        app = self.make_update_app()

        with patch("gui.messagebox.askyesno", return_value=True) as ask:
            with patch("gui.webbrowser.open") as open_browser:
                app._on_update_check_done(True, "v9.9.9", "https://example.com/r", False)

        self.assertIn("v9.9.9", app.status_var.set.call_args.args[0])
        ask.assert_called_once()
        open_browser.assert_called_once_with("https://example.com/r")
        app.btn_update.config.assert_called_with(state="normal")

    def test_on_update_check_done_skips_browser_when_declined(self):
        app = self.make_update_app()

        with patch("gui.messagebox.askyesno", return_value=False):
            with patch("gui.webbrowser.open") as open_browser:
                app._on_update_check_done(True, "v9.9.9", "https://example.com/r", True)

        open_browser.assert_not_called()

    def test_on_update_check_done_shows_latest_for_manual_check(self):
        app = self.make_update_app()

        with patch("gui.messagebox.showinfo") as showinfo:
            with patch("gui.APP_VERSION", "v1.13.1"):
                app._on_update_check_done(False, "v1.13.1", "https://example.com/r", True)

        showinfo.assert_called_once()
        self.assertIn("최신", app.status_var.set.call_args.args[0])

    def test_on_update_check_done_shows_warning_for_failed_manual_check(self):
        app = self.make_update_app()

        with patch("gui.messagebox.showwarning") as showwarning:
            app._on_update_check_done(False, "", "", True)

        showwarning.assert_called_once()
        app.status_var.set.assert_called_once_with("업데이트 확인 실패")

    def test_on_update_check_done_stays_silent_for_failed_auto_check(self):
        app = self.make_update_app()

        with patch("gui.messagebox.askyesno") as ask:
            with patch("gui.messagebox.showinfo") as showinfo:
                with patch("gui.messagebox.showwarning") as showwarning:
                    app._on_update_check_done(False, "", "", False)

        ask.assert_not_called()
        showinfo.assert_not_called()
        showwarning.assert_not_called()

    def test_poll_queue_dispatches_update_check_commands(self):
        app = object.__new__(App)
        app._queue = queue.Queue()
        app._cmd_queue = queue.Queue()
        app._poll_after_id = None
        app._shutting_down = False
        app.after = Mock(return_value="after-id")
        app._log_result = Mock()
        app._on_update_check_done = Mock()
        app._cmd_queue.put(("update_check_done", True, "v9.9.9", "https://example.com/r", False))

        app._poll_queue()

        app._on_update_check_done.assert_called_once_with(
            True, "v9.9.9", "https://example.com/r", False)


class UpdateDownloadTests(unittest.TestCase):
    def make_download_app(self, extra_attrs=None):
        app = object.__new__(App)
        app._cmd_queue = queue.Queue()
        app.after = Mock(side_effect=AssertionError("worker must not call Tk"))
        app.status_var = Mock()
        app.btn_update = Mock()
        app._log = Mock()
        app._quit_app = Mock()
        if extra_attrs:
            for k, v in extra_attrs.items():
                setattr(app, k, v)
        return app

    def test_run_update_download_queues_failed_when_no_target(self):
        app = self.make_download_app()

        with patch("gui.App._self_update_target", return_value=None):
            app._run_update_download("v9.9.9")

        action, reason = app._cmd_queue.get_nowait()
        self.assertEqual(action, "update_download_failed")
        self.assertIn("설치 위치", reason)
        app.after.assert_not_called()

    def test_run_update_download_queues_failed_on_checksum_mismatch(self):
        app = self.make_download_app()

        with patch("gui.App._self_update_target",
                   return_value=("/install", "/install/app.exe")):
            with patch("gui.download_update", return_value="/tmp/staging/a.zip"):
                with patch("gui.fetch_text", return_value="badhash  a.zip"):
                    with patch("gui.verify_sha256", return_value=False):
                        app._run_update_download("v9.9.9")

        action, reason = app._cmd_queue.get_nowait()
        self.assertEqual(action, "update_download_failed")
        self.assertIn("체크섬", reason)
        app.after.assert_not_called()

    def test_run_update_download_queues_done_when_verified(self):
        app = self.make_download_app()

        with patch("gui.App._self_update_target",
                   return_value=("/install", "/install/app.exe")):
            with patch("gui.staging_dir", return_value="/tmp/staging"):
                with patch("gui.download_update", return_value="/tmp/staging/a.zip"):
                    with patch("gui.fetch_text", return_value="good  a.zip"):
                        with patch("gui.verify_sha256", return_value=True):
                            with patch("gui.extract_update", return_value="/tmp/staging/app"):
                                app._run_update_download("v9.9.9")

        self.assertEqual(
            app._cmd_queue.get_nowait(),
            ("update_download_done", "v9.9.9", "/tmp/staging/app",
             "/install", "/tmp/staging"),
        )
        app.after.assert_not_called()

    def test_on_update_download_progress_shows_megabytes(self):
        app = self.make_download_app()

        app._on_update_download_progress(2097152, 4194304)

        status = app.status_var.set.call_args.args[0]
        self.assertIn("2.0/4.0 MB", status)

    def test_on_update_download_failed_sets_status_and_enables_button(self):
        app = self.make_download_app()
        app._update_check_in_progress = True

        app._on_update_download_failed("다운로드 실패: boom")

        self.assertFalse(app._update_check_in_progress)
        app.btn_update.config.assert_called_with(state="normal")
        self.assertIn("boom", app.status_var.set.call_args.args[0])

    def test_on_update_download_done_cancels_and_cleans_up_when_declined(self):
        app = self.make_download_app()
        app._update_check_in_progress = True

        with patch("gui.App._self_update_target",
                   return_value=("/install", "/install/app.exe")):
            with patch("gui.messagebox.askyesno", return_value=False):
                with patch("gui.cleanup_staging") as cleanup:
                    app._on_update_download_done("v9.9.9", "/new", "/install", "/staging")

        cleanup.assert_called_once_with("/staging")
        self.assertIn("취소", app.status_var.set.call_args.args[0])

    def test_on_update_download_done_writes_batch_and_quits_when_accepted(self):
        app = self.make_download_app()
        app._update_check_in_progress = True

        with patch("gui.App._self_update_target",
                   return_value=("/install", "/install/app.exe")):
            with patch("gui.messagebox.askyesno", return_value=True):
                with patch("gui.write_update_batch") as write_batch:
                    with patch("gui.subprocess") as subprocess_mock:
                        app._on_update_download_done(
                            "v9.9.9", "/new", "/install", "/staging")

        write_batch.assert_called_once()
        args, _kwargs = write_batch.call_args
        self.assertEqual(args[2], "/install")
        self.assertEqual(args[3], "/new")
        subprocess_mock.Popen.assert_called_once()
        app._quit_app.assert_called_once()

    def test_on_update_check_done_offers_auto_install_on_windows(self):
        app = self.make_download_app()
        app._update_check_in_progress = True
        app._start_update_download = Mock()

        with patch("gui.messagebox.askyesnocancel", return_value=True) as ask:
            with patch("gui.webbrowser.open") as open_browser:
                with patch("gui.App._can_self_update", return_value=True):
                    app._on_update_check_done(
                        True, "v9.9.9", "https://example.com/r", False)

        ask.assert_called_once()
        app._start_update_download.assert_called_once_with("v9.9.9")
        open_browser.assert_not_called()

    def test_on_update_check_done_opens_page_when_auto_install_declined(self):
        app = self.make_download_app()
        app._update_check_in_progress = True
        app._start_update_download = Mock()

        with patch("gui.messagebox.askyesnocancel", return_value=False):
            with patch("gui.webbrowser.open") as open_browser:
                with patch("gui.App._can_self_update", return_value=True):
                    app._on_update_check_done(
                        True, "v9.9.9", "https://example.com/r", False)

        app._start_update_download.assert_not_called()
        open_browser.assert_called_once_with("https://example.com/r")

    def test_poll_queue_dispatches_update_download_commands(self):
        app = object.__new__(App)
        app._queue = queue.Queue()
        app._cmd_queue = queue.Queue()
        app._poll_after_id = None
        app._shutting_down = False
        app.after = Mock(return_value="after-id")
        app._log_result = Mock()
        app._on_update_download_done = Mock()
        app._on_update_download_failed = Mock()
        app._on_update_download_progress = Mock()
        app._cmd_queue.put(("update_download_progress", 100, 200))
        app._cmd_queue.put(("update_download_done", "v9", "/new", "/i", "/s"))
        app._cmd_queue.put(("update_download_failed", "boom"))

        app._poll_queue()

        app._on_update_download_progress.assert_called_once_with(100, 200)
        app._on_update_download_done.assert_called_once_with("v9", "/new", "/i", "/s")
        app._on_update_download_failed.assert_called_once_with("boom")


if __name__ == "__main__":
    unittest.main()
