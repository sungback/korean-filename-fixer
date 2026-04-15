"""
gui.py
tkinter 기반 GUI 모듈

tkinter는 모든 UI 수정을 메인 스레드에서만 허용한다.
백그라운드 작업(변환 등)은 별도 스레드로 실행하고,
결과는 Queue에 넣어 메인 스레드가 100ms마다 꺼내 표시한다.
"""

import json
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

try:
    from AppKit import (NSStatusBar, NSVariableStatusItemLength,
                        NSMenu, NSMenuItem, NSObject)
    _APPKIT = True
except ImportError:
    _APPKIT = False


if _APPKIT:
    class _TrayDelegate(NSObject):
        """NSMenuItem 액션을 Python 콜백으로 연결하는 ObjC 델리게이트."""
        _show_cb = None
        _quit_cb = None
        _start_cb = None
        _stop_cb = None

        def showWindow_(self, sender):
            if self._show_cb:
                self._show_cb()

        def quitApp_(self, sender):
            if self._quit_cb:
                self._quit_cb()

        def startWatch_(self, sender):
            if self._start_cb:
                self._start_cb()

        def stopWatch_(self, sender):
            if self._stop_cb:
                self._stop_cb()


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".korean_filename_fixer.json")


def should_run_startup_scan(folder: str, scan_on_startup: bool) -> bool:
    """유효한 저장 폴더가 있고 설정이 켜져 있으면 시작 시 자동 스캔을 실행한다."""
    return bool(scan_on_startup and folder and os.path.isdir(folder))


def setup_logging():
    """로그를 홈 디렉토리 파일과 콘솔에 동시에 출력한다."""
    log_path = os.path.join(os.path.expanduser("~"), "KoreanFilenameFixer.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ]
    )


from converter import (
    DEFAULT_EXCLUDE_PATTERNS,
    ConvertResult,
    clean_exclude_patterns,
    convert_folder,
    nfd_to_visual,
    preview_folder,
)
from watcher import FolderWatcher


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Korean Filename Fixer")
        self.resizable(True, True)
        self.configure(padx=16, pady=16)

        self._queue: queue.Queue = queue.Queue()
        self._cmd_queue: queue.Queue = queue.Queue()
        self.watcher = FolderWatcher(callback=self._queue.put)
        self._dark = self._is_dark_mode()
        self._poll_after_id = None
        self._shutting_down = False
        self._startup_scan_in_progress = False

        self._build_ui()
        self._apply_window_constraints()
        self._load_config()
        self._poll_queue()
        self._setup_tray()
        # 독 아이콘 클릭(창이 숨겨진 상태) 시 창을 복원한다
        self.createcommand("::tk::mac::ReopenApplication", self._show_window)

    def _is_dark_mode(self) -> bool:
        """시스템 테마가 다크 모드인지 감지한다."""
        try:
            bg = self.tk.call("ttk::style", "lookup", "TFrame", "-background")
            r, g, b = [x >> 8 for x in self.winfo_rgb(bg)]
            return (0.299 * r + 0.587 * g + 0.114 * b) < 128
        except Exception:
            return False

    def _get_theme_colors(self) -> dict:
        """다크/라이트 모드에 따른 로그 색상을 반환한다."""
        if self._dark:
            return {"bg": "#1e1e1e", "fg": "#dddddd",
                    "converted": "#66ff66", "preview": "#66ccff",
                    "conflict": "#ffb366", "error": "#ff6666"}
        return {"bg": "#ffffff", "fg": "#333333",
                "converted": "#007700", "preview": "#005fcc",
                "conflict": "#b35a00", "error": "#cc0000"}

    # ─── UI 구성 ──────────────────────────────────────────────

    def _build_ui(self):
        self._build_folder_row()
        self._build_exclude_row()
        self._build_option_row()
        self._build_button_row()
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(0, 8))
        self._build_status_label()
        self._build_log_area()

    def _build_folder_row(self):
        """감시 폴더 선택 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        tk.Label(frame, text="감시 폴더:").pack(side="left")

        # 고정 너비 위젯을 먼저 오른쪽에 배치하고, Entry가 남은 공간을 채운다
        right_frame = tk.Frame(frame)
        right_frame.pack(side="right")

        self.remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(right_frame, text="기억", variable=self.remember_var,
                       command=self._on_remember_toggle).pack(side="right")
        tk.Button(right_frame, text="선택",
                  command=self._choose_folder).pack(side="right", padx=(4, 0))

        self.folder_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.folder_var,
                 state="readonly").pack(side="left", padx=(6, 4), fill="x", expand=True)

    def _build_exclude_row(self):
        """제외할 디렉터리 패턴 입력 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        tk.Label(frame, text="제외 패턴:").pack(side="left")

        self.exclude_var = tk.StringVar(
            value=self._format_exclude_patterns(DEFAULT_EXCLUDE_PATTERNS)
        )
        entry = tk.Entry(frame, textvariable=self.exclude_var)
        entry.pack(side="left", padx=(6, 4), fill="x", expand=True)
        entry.bind("<FocusOut>", self._on_exclude_patterns_changed)

        tk.Label(frame, text="쉼표로 구분", fg="gray").pack(side="right")

    def _build_option_row(self):
        """시작 동작 옵션 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        self.scan_on_startup_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            frame,
            text="시작 시 누락분 자동 스캔",
            variable=self.scan_on_startup_var,
            command=self._on_scan_on_startup_toggle,
        ).pack(side="left")

    def _build_button_row(self):
        """감시 제어 버튼 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        self.btn_start = tk.Button(frame, text="▶ 폴더 감시 시작",
                                   command=self._start_watch)
        self.btn_start.pack(side="left", padx=(0, 6))

        self.btn_stop = tk.Button(frame, text="■ 중지", width=8,
                                  state="disabled", command=self._stop_watch)
        self.btn_stop.pack(side="left", padx=(0, 6))

        self.btn_preview = tk.Button(frame, text="변환 미리보기",
                                     command=self._preview_once)
        self.btn_preview.pack(side="left", padx=(0, 6))

        self.btn_once = tk.Button(frame, text="기존 파일들 한 번에 변환",
                                  command=self._convert_once)
        self.btn_once.pack(side="left", padx=(0, 6))

        tk.Button(frame, text="로그 지우기",
                  command=self._clear_log).pack(side="left", padx=(0, 6))

    def _build_status_label(self):
        self.status_var = tk.StringVar(value="폴더를 선택하세요.")
        tk.Label(self, textvariable=self.status_var,
                 anchor="w", fg="gray").pack(fill="x", pady=(0, 4))

    def _build_log_area(self):
        """스크롤 가능한 로그 텍스트 영역과 색상 태그를 구성한다."""
        colors = self._get_theme_colors()

        self.log = scrolledtext.ScrolledText(
            self, width=60, height=18, state="disabled", wrap="none",
            font=("Menlo", 11),
            bg=colors["bg"], fg=colors["fg"], insertbackground=colors["fg"],
        )
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("converted", foreground=colors["converted"])
        self.log.tag_config("preview",   foreground=colors["preview"])
        self.log.tag_config("conflict",  foreground=colors["conflict"])
        self.log.tag_config("error",     foreground=colors["error"])
        self.log.tag_config("info",      foreground=colors["fg"])

    def _apply_window_constraints(self):
        """
        렌더된 UI의 실제 요구 크기를 기준으로 창 최소 크기를 고정한다.
        Tk/macOS에서는 위젯이 모두 배치되기 전에 minsize를 주면
        기대보다 작게 줄어드는 경우가 있어 idle 이후 한 번 더 적용한다.
        """
        self.update_idletasks()

        min_width = max(620, self.winfo_reqwidth())
        min_height = max(430, self.winfo_reqheight())

        self.minsize(min_width, min_height)
        self.after_idle(lambda: self.minsize(
            max(620, self.winfo_reqwidth()),
            max(430, self.winfo_reqheight()),
        ))

        if self.winfo_width() < min_width or self.winfo_height() < min_height:
            self.geometry(f"{min_width}x{min_height}")

    # ─── 설정 저장/불러오기 ───────────────────────────────────

    def _load_config(self):
        """저장된 폴더 경로를 불러온다. 폴더가 실제로 존재할 때만 적용한다."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            exclude_patterns = clean_exclude_patterns(
                data.get("exclude_patterns", DEFAULT_EXCLUDE_PATTERNS)
            )
            self.exclude_var.set(self._format_exclude_patterns(exclude_patterns))
            self.scan_on_startup_var.set(bool(data.get("scan_on_startup", True)))
            folder = data.get("folder", "")
            if folder and os.path.isdir(folder):
                self.folder_var.set(folder)
                self.remember_var.set(True)
                self.status_var.set("저장된 설정을 불러왔습니다.")
                if should_run_startup_scan(folder, self.scan_on_startup_var.get()):
                    self._start_startup_scan(folder)
                else:
                    self._start_watch()
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_config(self):
        """체크박스 ON이면 폴더 경로를 저장하고, OFF이면 config 파일을 삭제한다."""
        if self.remember_var.get() and self.folder_var.get():
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "folder": self.folder_var.get(),
                    "exclude_patterns": self._get_exclude_patterns(),
                    "scan_on_startup": self.scan_on_startup_var.get(),
                }, f, ensure_ascii=False)
        else:
            try:
                os.remove(CONFIG_PATH)
            except FileNotFoundError:
                pass

    def _on_remember_toggle(self):
        self._save_config()

    def _on_scan_on_startup_toggle(self):
        if self.remember_var.get() and self.folder_var.get():
            self._save_config()

    def _get_exclude_patterns(self) -> list[str]:
        return clean_exclude_patterns(self.exclude_var.get().split(","))

    def _format_exclude_patterns(self, patterns) -> str:
        return ", ".join(clean_exclude_patterns(patterns))

    def _exclude_patterns_text(self) -> str:
        patterns = self._get_exclude_patterns()
        return ", ".join(patterns) if patterns else "없음"

    def _on_exclude_patterns_changed(self, _event=None):
        normalized = self._format_exclude_patterns(self._get_exclude_patterns())
        if self.exclude_var.get().strip() != normalized:
            self.exclude_var.set(normalized)

        if self.remember_var.get() and self.folder_var.get():
            self._save_config()

        if self.watcher.is_running:
            folder = self.folder_var.get()
            try:
                self.watcher.start(folder, self._get_exclude_patterns())
                self.status_var.set(f"감시 중: {folder}")
                self._log(f"제외 패턴 적용: {self._exclude_patterns_text()}", "info")
            except Exception as e:
                self.status_var.set(f"제외 패턴 적용 실패: {e}")
                self._log(f"제외 패턴 적용 실패: {e}", "error")

    def _set_startup_scan_running(self, running: bool):
        """시작 시 자동 스캔 중에는 수동 작업 버튼을 잠시 잠근다."""
        self._startup_scan_in_progress = running
        self.btn_start.config(
            state="disabled" if running or self.watcher.is_running else "normal"
        )
        self.btn_preview.config(state="disabled" if running else "normal")
        self.btn_once.config(state="disabled" if running else "normal")

    # ─── 폴더 선택 및 감시 제어 ──────────────────────────────

    def _choose_folder(self):
        folder = filedialog.askdirectory(title="감시할 폴더를 선택하세요")
        if folder:
            self.folder_var.set(folder)
            self._log(f"폴더 선택: {folder}", "info")
            self.status_var.set("폴더가 선택되었습니다.")
            if self.remember_var.get():
                self._save_config()

    def _start_watch(self):
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중입니다.")
            return
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        exclude_patterns = self._get_exclude_patterns()
        try:
            self.watcher.start(folder, exclude_patterns)
        except Exception as e:
            self.status_var.set(f"감시 시작 실패: {e}")
            self._log(f"오류: {e}", "error")
            return
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set(f"감시 중: {folder}")
        self._log(f"감시를 시작했습니다. (제외: {self._exclude_patterns_text()})", "info")
        self._update_tray_title(watching=True)
        self._update_tray_menu_state(watching=True)

    def _stop_watch(self):
        self.watcher.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("감시가 중지되었습니다.")
        self._log("감시를 중지했습니다.", "info")
        self._update_tray_title(watching=False)
        self._update_tray_menu_state(watching=False)

    # ─── 일괄 변환 ────────────────────────────────────────────

    def _start_startup_scan(self, folder: str):
        """저장된 폴더가 있으면 앱 시작 직후 누락분을 한 번 정리한다."""
        self._set_startup_scan_running(True)
        self.status_var.set("시작 시 누락분 확인 중...")
        self._log(f"시작 시 누락분 스캔 시작... (제외: {self._exclude_patterns_text()})", "info")

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_startup_scan,
            args=(folder, exclude_patterns),
            daemon=True,
        ).start()

    def _run_startup_scan(self, folder: str, exclude_patterns: list[str]):
        """백그라운드 스레드에서 시작 시 자동 스캔을 실행한다."""
        try:
            results = convert_folder(folder, exclude_patterns=exclude_patterns)
            self.after(0, self._on_startup_scan_done, results, folder)
        except Exception as e:
            self.after(0, self._on_startup_scan_failed, folder, str(e))

    def _preview_once(self):
        """폴더 전체를 스캔해 변환 예정 결과만 표시한다."""
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중에는 실행할 수 없습니다.")
            return
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return

        self.status_var.set("미리보기 중...")
        self.btn_preview.config(state="disabled")

        was_watching = self.watcher.is_running
        if was_watching:
            self.watcher.stop()

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_preview,
            args=(folder, was_watching, exclude_patterns),
            daemon=True,
        ).start()

    def _run_preview(self, folder: str, resume_watch: bool, exclude_patterns: list[str]):
        """백그라운드 스레드에서 미리보기를 계산하고 결과를 메인 스레드에 전달한다."""
        results = preview_folder(folder, exclude_patterns=exclude_patterns)
        self.after(0, self._on_preview_done, results, folder, resume_watch)

    def _convert_once(self):
        """폴더 전체를 한 번 스캔해서 변환한다.

        감시 중이면 레이스 컨디션 방지를 위해 변환 동안 감시를 일시 중단한다.
        """
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중에는 실행할 수 없습니다.")
            return
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return

        self.status_var.set("변환 중...")
        self.btn_once.config(state="disabled")

        was_watching = self.watcher.is_running
        if was_watching:
            self.watcher.stop()

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_batch_convert,
            args=(folder, was_watching, exclude_patterns),
            daemon=True,
        ).start()

    def _run_batch_convert(self, folder: str, resume_watch: bool, exclude_patterns: list[str]):
        """백그라운드 스레드에서 일괄 변환을 실행하고 결과를 메인 스레드에 전달한다."""
        results = convert_folder(folder, exclude_patterns=exclude_patterns)
        self.after(0, self._on_batch_done, results, folder, resume_watch)

    def _on_batch_done(self, results: list, folder: str, resume_watch: bool):
        """일괄 변환 완료 후 결과를 표시하고 필요하면 감시를 재개한다."""
        converted = [r for r in results if r.status == "converted"]
        conflicts = [r for r in results if r.status == "conflict"]
        errors    = [r for r in results if r.status == "error"]
        skipped   = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r)

        summary = (f"완료 — 변환: {len(converted)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self.btn_once.config(state="normal")

        if resume_watch:
            self._resume_watch(folder)

    def _on_preview_done(self, results: list, folder: str, resume_watch: bool):
        """미리보기 완료 후 결과를 표시하고 필요하면 감시를 재개한다."""
        previews = [r for r in results if r.status == "preview"]
        conflicts = [r for r in results if r.status == "conflict"]
        skipped = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r)

        summary = (f"미리보기 완료 — 예정: {len(previews)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self.btn_preview.config(state="normal")

        if resume_watch:
            self._resume_watch(folder)

    def _on_startup_scan_done(self, results: list, folder: str):
        """시작 시 자동 스캔 완료 후 결과를 기록하고 감시를 시작한다."""
        converted = [r for r in results if r.status == "converted"]
        conflicts = [r for r in results if r.status == "conflict"]
        errors    = [r for r in results if r.status == "error"]
        skipped   = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r)

        summary = (f"시작 시 누락분 처리 완료 — 변환: {len(converted)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self._set_startup_scan_running(False)
        self._start_watch()

    def _on_startup_scan_failed(self, folder: str, error: str):
        """시작 시 자동 스캔 실패 시에도 앱은 계속 실행하고 감시는 시작한다."""
        self.status_var.set(f"시작 시 누락분 스캔 실패: {error}")
        self._log(f"시작 시 누락분 스캔 실패: {error}", "error")
        self._set_startup_scan_running(False)
        self._start_watch()

    def _resume_watch(self, folder: str):
        try:
            self.watcher.start(folder, self._get_exclude_patterns())
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.status_var.set(f"감시 중: {folder}")
            self._log(f"감시 재개 (제외: {self._exclude_patterns_text()})", "info")
            self._update_tray_title(watching=True)
            self._update_tray_menu_state(watching=True)
        except Exception as e:
            self._log(f"감시 재개 실패: {e}", "error")

    # ─── 로그 출력 ────────────────────────────────────────────

    def _poll_queue(self):
        """100ms마다 큐를 비워 감시 스레드의 변환 결과를 로그에 표시한다."""
        if self._shutting_down:
            self._poll_after_id = None
            return
        try:
            while True:
                self._log_result(self._queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                cmd = self._cmd_queue.get_nowait()
                if cmd == "start":
                    self._start_watch()
                elif cmd == "stop":
                    self._stop_watch()
                elif cmd == "show":
                    self._show_window()
                elif cmd == "quit":
                    self._quit_app()
        except queue.Empty:
            pass
        self._poll_after_id = self.after(100, self._poll_queue)

    def _log_result(self, result: ConvertResult):
        if result.status == "converted":
            visual = nfd_to_visual(result.original)
            self._log(f"✓ {visual}  →  {result.converted}", "converted")
        elif result.status == "preview":
            visual = nfd_to_visual(result.original)
            self._log(f"→ {visual}  →  {result.converted}", "preview")
        elif result.status == "conflict":
            visual = nfd_to_visual(result.original)
            self._log(f"! {visual}  충돌: {result.error}", "conflict")
        elif result.status == "error":
            visual = nfd_to_visual(result.original)
            self._log(f"✗ {visual}  오류: {result.error}", "error")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _log(self, msg: str, tag: str = "info"):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        # 1000줄 초과 시 앞 200줄 삭제해 메모리 누수를 방지한다
        if int(self.log.index("end-1c").split(".")[0]) > 1000:
            self.log.delete("1.0", "201.0")
        self.log.see("end")
        self.log.config(state="disabled")

    # ─── 시스템 트레이 (macOS 메뉴바) ────────────────────────

    def _setup_tray(self):
        """AppKit NSStatusBar로 메뉴바 아이콘을 등록한다."""
        if not _APPKIT:
            logging.warning("AppKit 없음 — 트레이 아이콘 비활성")
            return
        try:
            self._tray_delegate = _TrayDelegate.alloc().init()
            self._tray_delegate._show_cb = lambda: self._cmd_queue.put("show")
            self._tray_delegate._quit_cb = lambda: self._cmd_queue.put("quit")
            # ObjC 콜백은 별도 스레드에서 실행되므로 큐에만 넣고 메인 스레드가 처리한다
            self._tray_delegate._start_cb = lambda: self._cmd_queue.put("start")
            self._tray_delegate._stop_cb = lambda: self._cmd_queue.put("stop")

            show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "창 열기", "showWindow:", "")
            show_item.setTarget_(self._tray_delegate)

            self._tray_start_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "감시 시작", "startWatch:", "")
            self._tray_start_item.setTarget_(self._tray_delegate)

            self._tray_stop_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "감시 중지", "stopWatch:", "")
            self._tray_stop_item.setTarget_(self._tray_delegate)

            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "종료", "quitApp:", "")
            quit_item.setTarget_(self._tray_delegate)

            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)
            menu.addItem_(show_item)
            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(self._tray_start_item)
            menu.addItem_(self._tray_stop_item)
            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(quit_item)

            status_bar = NSStatusBar.systemStatusBar()
            self._status_item = status_bar.statusItemWithLength_(
                NSVariableStatusItemLength)
            title = "K●" if self.watcher.is_running else "K"
            self._status_item.button().setTitle_(title)
            self._status_item.setMenu_(menu)
            self._update_tray_menu_state(self.watcher.is_running)
        except Exception:
            logging.exception("트레이 아이콘 설정 실패")

    def _update_tray_title(self, watching: bool):
        """감시 상태에 따라 메뉴바 아이콘 텍스트를 변경한다."""
        if not _APPKIT or not hasattr(self, "_status_item"):
            return
        title = "K●" if watching else "K"
        self._status_item.button().setTitle_(title)

    def _update_tray_menu_state(self, watching: bool):
        """감시 상태에 따라 트레이 메뉴 항목 활성/비활성을 갱신한다."""
        if not _APPKIT or not hasattr(self, "_tray_start_item"):
            return
        self._tray_start_item.setEnabled_(not watching)
        self._tray_stop_item.setEnabled_(watching)

    def _show_window(self):
        """메뉴바 또는 독 아이콘 클릭 시 창을 복원한다."""
        self.after(0, self.deiconify)
        self.after(0, self.lift)
        self.after(0, self.focus_force)

    def _quit_app(self):
        """감시 중지 → 메뉴바 아이콘 제거 → 창 종료."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.watcher.stop()
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        try:
            if hasattr(self, "_status_item"):
                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
        except Exception:
            pass
        self.destroy()

    def on_close(self):
        """창 닫기(X) 시 메뉴바로 숨긴다. 종료는 메뉴바 '종료' 사용."""
        self.withdraw()
