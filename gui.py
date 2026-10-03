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
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    _TKINTER_AVAILABLE = True
    _TKINTER_IMPORT_ERROR = None
except ImportError as e:
    tk = None
    filedialog = messagebox = scrolledtext = ttk = None
    _TKINTER_AVAILABLE = False
    _TKINTER_IMPORT_ERROR = e

try:
    import tkinterdnd2 as _tkdnd
    _TKDND_AVAILABLE = True
except ImportError:
    _tkdnd = None
    _TKDND_AVAILABLE = False

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


def setup_logging():
    """로그를 홈 디렉토리 파일과 콘솔에 동시에 출력한다."""
    log_path = os.path.join(os.path.expanduser("~"), "KoreanFilenameFixer.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ]
    )


from converter import (
    DEFAULT_EXCLUDE_PATTERNS,
    ConvertResult,
    clean_exclude_patterns,
    convert_folder,
    folder_after_results,
    nfd_to_visual,
    preview_folder,
    startup_scan_skip_reason,
)
from autostart import (
    disable_autostart,
    enable_autostart,
    get_autostart_executable_path,
    is_autostart_enabled,
    needs_autostart_refresh,
)
from watcher import FolderWatcher
from version import APP_VERSION, GITHUB_REPO
from updater import (
    fetch_latest_release,
    is_newer,
    should_check,
)
from selfupdate import (
    MACOS_ZIP_NAME,
    MAC_UPDATE_SCRIPT_NAME,
    UPDATE_BATCH_NAME,
    WINDOWS_ZIP_NAME,
    checksum_url,
    cleanup_staging,
    current_exe_path,
    current_install_dir,
    current_macos_app,
    download_update,
    extract_mac_app,
    extract_update,
    fetch_text,
    is_writable_dir,
    parse_checksum,
    release_asset_url,
    same_drive,
    sibling_instances,
    staging_dir,
    verify_bundle,
    verify_sha256,
    write_mac_update_script,
    write_update_batch,
)


_AppBase = (
    _tkdnd.TkinterDnD.Tk if _TKDND_AVAILABLE and _TKINTER_AVAILABLE
    else tk.Tk if _TKINTER_AVAILABLE
    else object
)


class App(_AppBase):
    def __init__(self):
        if not _TKINTER_AVAILABLE:
            raise RuntimeError(
                "tkinter를 사용할 수 없습니다. Tk가 포함된 Python으로 실행하세요."
            ) from _TKINTER_IMPORT_ERROR

        super().__init__()
        self.title(f"Korean Filename Fixer {APP_VERSION}")
        self.resizable(True, True)
        self.configure(padx=16, pady=16)

        self._queue: queue.Queue = queue.Queue()
        self._cmd_queue: queue.Queue = queue.Queue()
        self.watcher = FolderWatcher(callback=self._queue.put)
        self._folders: list[str] = []
        self._dark = self._is_dark_mode()
        self._poll_after_id = None
        self._health_check_after_id = None
        self._shutting_down = False
        self._startup_scan_in_progress = False
        self._startup_scan_cancel_event: threading.Event | None = None
        self._watch_paused_for_operation = False
        self._stats = {"converted": 0, "error": 0, "conflict": 0}
        self._watcher_notify_count = 0
        self._watcher_notify_after_id = None
        self._autostart_path = get_autostart_executable_path()
        self._last_update_check: float = 0.0
        self._update_check_in_progress = False

        self._build_ui()
        self._setup_drop()
        self._apply_window_constraints()
        self._load_config()
        self._poll_queue()
        self._health_check()
        self._setup_tray()
        self._register_reopen_command()
        self._maybe_auto_update_check()

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

    def _register_reopen_command(self):
        """macOS 독 아이콘 클릭(창이 숨겨진 상태) 시 창을 복원한다."""
        if sys.platform != "darwin":
            return
        try:
            self.createcommand("::tk::mac::ReopenApplication", self._show_window)
        except tk.TclError:
            logging.warning("macOS reopen command 등록 실패", exc_info=True)

    def _button_options(self) -> dict:
        """macOS/Tk 테마와 무관하게 버튼 텍스트가 읽히도록 색상을 고정한다."""
        if self._dark:
            return {
                "fg": "#111111",
                "bg": "#3a3a3c",
                "activeforeground": "#111111",
                "activebackground": "#48484a",
                "disabledforeground": "#4a4a4a",
            }
        return {
            "fg": "#111111",
            "bg": "#f2f2f7",
            "activeforeground": "#111111",
            "activebackground": "#e5e5ea",
            "disabledforeground": "#4a4a4a",
        }

    def _button(self, parent, **kwargs):
        options = self._button_options()
        options.update(kwargs)
        return tk.Button(parent, **options)

    # ─── UI 구성 ──────────────────────────────────────────────

    def _build_ui(self):
        self._build_folder_row()
        self._build_exclude_row()
        self._build_option_row()
        self._build_button_row()
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(0, 8))
        self._build_status_label()
        self._build_stats_label()
        self._build_log_area()

    def _build_folder_row(self):
        """감시 폴더 목록 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        tk.Label(frame, text="감시 폴더:").pack(side="left", anchor="n")

        # 고정 너비 위젯을 먼저 오른쪽에 배치하고, Listbox가 남은 공간을 채운다
        right_frame = tk.Frame(frame)
        right_frame.pack(side="right", anchor="n")

        self.remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(right_frame, text="기억", variable=self.remember_var,
                       command=self._on_remember_toggle).pack(side="top", anchor="e")
        self._button(right_frame, text="추가",
                     command=self._add_folder).pack(side="top", fill="x", pady=(4, 0))
        self._button(right_frame, text="선택 삭제",
                     command=self._remove_selected_folders).pack(side="top", fill="x", pady=(4, 0))
        self._button(right_frame, text="전체 삭제",
                     command=self._clear_folders).pack(side="top", fill="x", pady=(4, 0))

        list_frame = tk.Frame(frame)
        list_frame.pack(side="left", padx=(6, 4), fill="x", expand=True)
        self.folder_listbox = tk.Listbox(list_frame, height=3, selectmode="extended")
        self.folder_listbox.pack(side="left", fill="x", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                 command=self.folder_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.folder_listbox.config(yscrollcommand=scrollbar.set)

    def _normalize_folder(self, folder: str) -> str:
        return os.path.abspath(os.path.expanduser(folder))

    def _get_folders(self) -> list[str]:
        return list(self.__dict__.get("_folders", []))

    def _set_folders(self, folders: list[str], save: bool = False):
        """중복 제거 후 내부 목록과 Listbox를 갱신한다."""
        unique: list[str] = []
        seen: set[str] = set()
        for folder in folders or []:
            if not folder:
                continue
            normalized = self._normalize_folder(folder)
            if normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        self._folders = unique
        self._refresh_folder_listbox()
        if save:
            remember = self.__dict__.get("remember_var")
            if remember is not None and remember.get():
                self._save_config()

    def _refresh_folder_listbox(self):
        if "folder_listbox" not in self.__dict__:
            return
        try:
            self.folder_listbox.delete(0, "end")
            for folder in self.__dict__.get("_folders", []):
                self.folder_listbox.insert("end", folder)
        except Exception:
            pass

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

        self.launch_on_login_var = tk.BooleanVar(value=False)
        self.chk_launch_on_login = tk.Checkbutton(
            frame,
            text="로그인 시 자동 시작",
            variable=self.launch_on_login_var,
            command=self._on_launch_on_login_toggle,
        )
        self.chk_launch_on_login.pack(side="left", padx=(12, 0))
        if not self._autostart_path:
            self.chk_launch_on_login.config(state="disabled")

        self.notify_on_convert_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            frame,
            text="변환 시 알림",
            variable=self.notify_on_convert_var,
            command=self._on_notify_toggle,
        ).pack(side="left", padx=(12, 0))

    def _build_button_row(self):
        """감시 제어 버튼 행을 구성한다."""
        frame = tk.Frame(self)
        frame.pack(fill="x", pady=(0, 10))

        self.btn_start = self._button(frame, text="▶ 폴더 감시 시작",
                                      command=self._start_watch)
        self.btn_start.pack(side="left", padx=(0, 6))

        self.btn_stop = self._button(frame, text="■ 중지", width=12,
                                     state="disabled", command=self._stop_current_activity)
        self.btn_stop.pack(side="left", padx=(0, 6))

        self.btn_preview = self._button(frame, text="변환 미리보기",
                                        command=self._preview_once)
        self.btn_preview.pack(side="left", padx=(0, 6))

        self.btn_once = self._button(frame, text="기존 파일들 한 번에 변환",
                                     command=self._convert_once)
        self.btn_once.pack(side="left", padx=(0, 6))

        self.btn_update = self._button(frame, text="업데이트 확인",
                                       command=self._check_update_manual)
        self.btn_update.pack(side="left", padx=(0, 6))

        self._button(frame, text="로그 지우기",
                     command=self._clear_log).pack(side="left", padx=(0, 6))

    def _build_status_label(self):
        self.status_var = tk.StringVar(value="폴더를 선택하세요.")
        tk.Label(self, textvariable=self.status_var,
                 anchor="w", fg="gray").pack(fill="x", pady=(0, 4))

    def _build_stats_label(self):
        self.stats_var = tk.StringVar(value="이번 세션: 변환 0 | 오류 0 | 충돌 0")
        tk.Label(self, textvariable=self.stats_var,
                 anchor="w", fg="gray").pack(fill="x", pady=(0, 4))

    def _refresh_stats(self):
        c = self._stats["converted"]
        e = self._stats["error"]
        cf = self._stats["conflict"]
        self.stats_var.set(f"이번 세션: 변환 {c} | 오류 {e} | 충돌 {cf}")

    def _build_log_area(self):
        """스크롤 가능한 로그 텍스트 영역과 색상 태그를 구성한다."""
        colors = self._get_theme_colors()

        self.log = scrolledtext.ScrolledText(
            self, width=60, height=18, state="disabled", wrap="word",
            font=("Menlo", 11),
            bg=colors["bg"], fg=colors["fg"], insertbackground=colors["fg"],
        )
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("converted", foreground=colors["converted"])
        self.log.tag_config("preview",   foreground=colors["preview"])
        self.log.tag_config("conflict",  foreground=colors["conflict"])
        self.log.tag_config("error",     foreground=colors["error"])
        self.log.tag_config("info",      foreground=colors["fg"])
        for shortcut in ("<Command-c>", "<Command-C>", "<Control-c>", "<Control-C>"):
            self.log.bind(shortcut, self._copy_log_selection)

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
        """저장된 폴더 목록을 불러온다. 존재하는 폴더만 적용하고旧 단일 경로도 마이그레이션한다."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            exclude_patterns = clean_exclude_patterns(
                data.get("exclude_patterns", DEFAULT_EXCLUDE_PATTERNS)
            )
            self.exclude_var.set(self._format_exclude_patterns(exclude_patterns))
            self.scan_on_startup_var.set(bool(data.get("scan_on_startup", True)))
            self.notify_on_convert_var.set(bool(data.get("notify_on_convert", True)))
            self._last_update_check = float(data.get("last_update_check", 0.0) or 0.0)
            self._sync_autostart_state()
            raw_folders = data.get("folders")
            if raw_folders is None and data.get("folder"):
                raw_folders = [data.get("folder")]
            folders = [f for f in (raw_folders or []) if f and os.path.isdir(f)]
            if folders:
                self._set_folders(folders)
                self.remember_var.set(True)
                self.status_var.set("저장된 설정을 불러왔습니다.")
                scan_targets: list[str] = []
                for folder in self._get_folders():
                    skip_reason = startup_scan_skip_reason(
                        folder,
                        self.scan_on_startup_var.get(),
                        exclude_patterns,
                    )
                    if self.scan_on_startup_var.get() and not skip_reason:
                        scan_targets.append(folder)
                    else:
                        if skip_reason:
                            self._log(
                                f"시작 시 자동 스캔 건너뜀 ({folder}): {skip_reason}",
                                "info",
                            )
                if scan_targets:
                    self._start_startup_scan(scan_targets)
                else:
                    if self.scan_on_startup_var.get():
                        self.status_var.set("시작 시 자동 스캔 건너뜀 — 감시는 정상적으로 시작합니다.")
                    self._start_watch()
        except (FileNotFoundError, json.JSONDecodeError):
            self._sync_autostart_state()

    def _save_config(self):
        """체크박스 ON이면 폴더 목록을 저장하고, OFF이면 config 파일을 삭제한다."""
        if self.remember_var.get() and self._get_folders():
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "folders": self._get_folders(),
                    "exclude_patterns": self._get_exclude_patterns(),
                    "scan_on_startup": self.scan_on_startup_var.get(),
                    "notify_on_convert": self.notify_on_convert_var.get(),
                    "last_update_check": self.__dict__.get("_last_update_check", 0.0),
                }, f, ensure_ascii=False)
        else:
            try:
                os.remove(CONFIG_PATH)
            except FileNotFoundError:
                pass

    def _on_remember_toggle(self):
        self._save_config()

    def _on_scan_on_startup_toggle(self):
        if self.remember_var.get() and self._get_folders():
            self._save_config()

    def _sync_autostart_state(self):
        """현재 앱 경로와 자동 시작 상태를 동기화한다."""
        if not self._autostart_path:
            self.launch_on_login_var.set(False)
            return
        try:
            if needs_autostart_refresh(self._autostart_path):
                enable_autostart(self._autostart_path)
                self._log("로그인 시 자동 시작 경로 갱신", "info")
            self.launch_on_login_var.set(is_autostart_enabled(self._autostart_path))
        except Exception as e:
            self.launch_on_login_var.set(False)
            self._log(f"로그인 시 자동 시작 상태 확인 실패: {e}", "error")

    def _on_launch_on_login_toggle(self):
        if self.launch_on_login_var.get():
            if not self._autostart_path:
                self.launch_on_login_var.set(False)
                self._log("로그인 시 자동 시작은 지원되는 배포 실행 파일에서만 사용할 수 있습니다.", "error")
                return
            try:
                enable_autostart(self._autostart_path)
                self._log("로그인 시 자동 시작 활성화", "info")
            except Exception as e:
                self.launch_on_login_var.set(False)
                self._log(f"로그인 시 자동 시작 설정 실패: {e}", "error")
        else:
            try:
                disable_autostart()
                self._log("로그인 시 자동 시작 비활성화", "info")
            except Exception as e:
                self.launch_on_login_var.set(True)
                self._log(f"로그인 시 자동 시작 해제 실패: {e}", "error")

    def _on_notify_toggle(self):
        if self.remember_var.get() and self._get_folders():
            self._save_config()

    @staticmethod
    def _osascript_escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    def _send_notification(self, title: str, body: str):
        if sys.platform != "darwin":
            return
        try:
            t = self._osascript_escape(title)
            b = self._osascript_escape(body)
            subprocess.run(
                ["osascript", "-e", f'display notification "{b}" with title "{t}"'],
                timeout=3,
                capture_output=True,
            )
        except Exception:
            pass

    def _flush_watcher_notification(self):
        self._watcher_notify_after_id = None
        n = self._watcher_notify_count
        self._watcher_notify_count = 0
        if n > 0:
            self._send_notification("Korean Filename Fixer3", f"{n}개 파일 변환 완료")

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

        if self.remember_var.get() and self._get_folders():
            self._save_config()

        if self.watcher.is_running:
            folders = self._get_folders()
            try:
                self.watcher.start_many(folders, self._get_exclude_patterns())
                self.status_var.set(self._watch_status_text())
                self._log(f"제외 패턴 적용: {self._exclude_patterns_text()}", "info")
            except Exception as e:
                self.status_var.set(f"제외 패턴 적용 실패: {e}")
                self._log(f"제외 패턴 적용 실패: {e}", "error")

    def _set_startup_scan_running(self, running: bool):
        """시작 자동 스캔 중에는 수동 작업을 잠그고 건너뛰기만 허용한다."""
        self._startup_scan_in_progress = running
        self.btn_start.config(
            state="disabled" if running or self.watcher.is_running else "normal"
        )
        if running:
            self.btn_stop.config(state="normal", text="스캔 건너뛰기")
        else:
            self._startup_scan_cancel_event = None
            self.btn_stop.config(
                state="normal" if self.watcher.is_running else "disabled",
                text="■ 중지",
            )
        self.btn_preview.config(state="disabled" if running else "normal")
        self.btn_once.config(state="disabled" if running else "normal")

    def _watch_status_text(self) -> str:
        folders = self._get_folders()
        if not folders:
            return "폴더를 선택하세요."
        if len(folders) == 1:
            return f"감시 중: {folders[0]}"
        return f"감시 중: {len(folders)}개 폴더"

    # ─── 폴더 선택 및 감시 제어 ──────────────────────────────

    def _setup_drop(self):
        if not _TKDND_AVAILABLE:
            return
        try:
            target = getattr(self, "folder_listbox", self)
            target.drop_target_register(_tkdnd.DND_FILES)
            target.dnd_bind("<<Drop>>", self._on_dnd_drop)
        except Exception as e:
            logging.warning(f"DnD 등록 실패: {e}")

    def _on_dnd_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.isdir(path):
            self._add_folder_path(path, source="드래그앤드롭")

    def _add_folder_path(self, folder: str, source: str = "선택") -> bool:
        normalized = self._normalize_folder(folder)
        if normalized in self._get_folders():
            self.status_var.set("이미 등록된 폴더입니다.")
            return False
        self._set_folders([*self._get_folders(), normalized])
        self._log(f"폴더 추가 ({source}): {normalized}", "info")
        self.status_var.set("폴더가 추가되었습니다.")
        if self.remember_var.get():
            self._save_config()
        return True

    def _add_folder(self):
        folder = filedialog.askdirectory(title="감시할 폴더를 선택하세요")
        if folder:
            self._add_folder_path(folder)

    def _choose_folder(self):
        """기존 단일 선택 API 호환용 별칭."""
        self._add_folder()

    def _remove_selected_folders(self):
        try:
            selected = list(self.folder_listbox.curselection())
        except Exception:
            selected = []
        if not selected:
            self.status_var.set("삭제할 폴더를 목록에서 선택하세요.")
            return
        remaining = [f for i, f in enumerate(self._get_folders()) if i not in selected]
        self._set_folders(remaining)
        self._log(f"폴더 삭제: {len(selected)}개", "info")
        if self.remember_var.get():
            self._save_config()
        if self.watcher.is_running:
            self._start_watch()

    def _clear_folders(self):
        if not self._get_folders():
            return
        self._set_folders([])
        self._log("감시 폴더 목록을 비웠습니다.", "info")
        if self.remember_var.get():
            self._save_config()
        if self.watcher.is_running:
            self._stop_watch()

    def _start_watch(self):
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중입니다.")
            return
        folders = self._get_folders()
        if not folders:
            self.status_var.set("먼저 폴더를 추가하세요.")
            return
        exclude_patterns = self._get_exclude_patterns()
        try:
            self.watcher.start_many(folders, exclude_patterns)
        except Exception as e:
            self.status_var.set(f"감시 시작 실패: {e}")
            self._log(f"오류: {e}", "error")
            return
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set(self._watch_status_text())
        self._log(f"감시를 시작했습니다: {len(folders)}개 폴더 (제외: {self._exclude_patterns_text()})", "info")
        self._update_tray_title(watching=True)
        self._update_tray_menu_state(watching=True)

    def _stop_current_activity(self):
        if self._startup_scan_in_progress:
            self._skip_startup_scan()
        else:
            self._stop_watch()

    def _stop_watch(self):
        self.watcher.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("감시가 중지되었습니다.")
        self._log("감시를 중지했습니다.", "info")
        self._update_tray_title(watching=False)
        self._update_tray_menu_state(watching=False)

    # ─── 일괄 변환 ────────────────────────────────────────────

    @staticmethod
    def _as_folder_list(folders) -> list[str]:
        if folders is None:
            return []
        if isinstance(folders, str):
            return [folders] if folders else []
        return list(folders)

    def _start_startup_scan(self, folders):
        """저장된 폴더들이 있으면 앱 시작 직후 누락분을 한 번 정리한다."""
        folder_list = self._as_folder_list(folders)
        if not folder_list:
            self._start_watch()
            return
        self._startup_scan_cancel_event = threading.Event()
        self._set_startup_scan_running(True)
        self.status_var.set(f"시작 시 누락분 확인 중... ({len(folder_list)}개 폴더)")
        self._log(f"시작 시 누락분 스캔 시작... {len(folder_list)}개 폴더 (제외: {self._exclude_patterns_text()})", "info")

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_startup_scan,
            args=(folder_list, exclude_patterns, self._startup_scan_cancel_event),
            daemon=True,
        ).start()

    def _run_startup_scan(
        self,
        folders,
        exclude_patterns: list[str],
        cancel_event: threading.Event,
    ):
        """백그라운드 스레드에서 시작 시 자동 스캔을 실행한다."""
        folder_list = self._as_folder_list(folders)
        try:
            results: list = []
            for index, folder in enumerate(folder_list, start=1):
                if cancel_event.is_set():
                    break
                partial = convert_folder(
                    folder,
                    exclude_patterns=exclude_patterns,
                    include_root=True,
                    cancel_event=cancel_event,
                    progress_callback=self._progress_callback(f"시작 스캔({index}/{len(folder_list)})"),
                )
                results.extend(partial)
            if cancel_event.is_set():
                self._cmd_queue.put(("startup_scan_cancelled", results, folder_list))
            else:
                self._cmd_queue.put(("startup_scan_done", results, folder_list))
        except Exception as e:
            self._cmd_queue.put(("startup_scan_failed", folder_list, str(e)))

    def _skip_startup_scan(self):
        """진행 중인 시작 자동 스캔에 안전한 중단 신호를 보낸다."""
        if not self._startup_scan_in_progress:
            return
        if self._startup_scan_cancel_event is not None:
            self._startup_scan_cancel_event.set()
        self.status_var.set("시작 스캔을 건너뛰는 중...")
        self.btn_stop.config(state="disabled", text="건너뛰는 중...")
        self._log("시작 시 누락분 스캔 건너뛰기 요청", "info")

    def _preview_once(self):
        """모든 감시 폴더를 스캔해 변환 예정 결과만 표시한다."""
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중에는 실행할 수 없습니다.")
            return
        folders = self._get_folders()
        if not folders:
            self.status_var.set("먼저 폴더를 추가하세요.")
            return

        self.status_var.set(f"미리보기 중... ({len(folders)}개 폴더)")
        self.btn_preview.config(state="disabled")

        was_watching = self.watcher.is_running
        self._watch_paused_for_operation = was_watching
        if was_watching:
            self.watcher.stop()

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_preview,
            args=(folders, was_watching, exclude_patterns),
            daemon=True,
        ).start()

    def _run_preview(self, folders, resume_watch: bool, exclude_patterns: list[str]):
        """백그라운드 스레드에서 미리보기를 계산하고 결과를 메인 스레드에 전달한다."""
        folder_list = self._as_folder_list(folders)
        try:
            results: list = []
            for folder in folder_list:
                results.extend(preview_folder(
                    folder,
                    exclude_patterns=exclude_patterns,
                    include_root=True,
                ))
            self._cmd_queue.put(("preview_done", results, folder_list, resume_watch))
        except Exception as e:
            self._cmd_queue.put(("preview_failed", folder_list, resume_watch, str(e)))

    def _convert_once(self):
        """모든 감시 폴더를 한 번 스캔해서 변환한다.

        감시 중이면 레이스 컨디션 방지를 위해 변환 동안 감시를 일시 중단한다.
        """
        if self._startup_scan_in_progress:
            self.status_var.set("시작 시 누락분 스캔 중에는 실행할 수 없습니다.")
            return
        folders = self._get_folders()
        if not folders:
            self.status_var.set("먼저 폴더를 추가하세요.")
            return

        self.status_var.set(f"변환 중... ({len(folders)}개 폴더)")
        self.btn_once.config(state="disabled")

        was_watching = self.watcher.is_running
        self._watch_paused_for_operation = was_watching
        if was_watching:
            self.watcher.stop()

        exclude_patterns = self._get_exclude_patterns()
        threading.Thread(
            target=self._run_batch_convert,
            args=(folders, was_watching, exclude_patterns),
            daemon=True,
        ).start()

    def _run_batch_convert(self, folders, resume_watch: bool, exclude_patterns: list[str]):
        """백그라운드 스레드에서 일괄 변환을 실행하고 결과를 메인 스레드에 전달한다."""
        folder_list = self._as_folder_list(folders)
        try:
            results: list = []
            for index, folder in enumerate(folder_list, start=1):
                partial = convert_folder(
                    folder,
                    exclude_patterns=exclude_patterns,
                    include_root=True,
                    progress_callback=self._progress_callback(f"일괄 변환({index}/{len(folder_list)})"),
                )
                results.extend(partial)
            self._cmd_queue.put(("batch_done", results, folder_list, resume_watch))
        except Exception as e:
            self._cmd_queue.put(("batch_failed", folder_list, resume_watch, str(e)))

    def _sync_folder_after_conversion(self, folder: str, results: list) -> str:
        new_folder = folder_after_results(folder, results)
        if new_folder != folder:
            folders = self._get_folders()
            if folder in folders:
                updated = [new_folder if f == folder else f for f in folders]
                self._set_folders(updated)
            remember = self.__dict__.get("remember_var")
            if remember is not None and remember.get():
                try:
                    self._save_config()
                except Exception:
                    pass
            try:
                self._log(f"감시 폴더 경로 갱신: {new_folder}", "info")
            except Exception:
                pass
        return new_folder

    def _sync_folders_after_conversion(self, folders, results: list) -> list[str]:
        """루트 폴더 자체가 변환됐으면 새 경로로 목록을 갱신한다."""
        folder_list = self._as_folder_list(folders)
        current = self._get_folders() or folder_list
        updated = list(current)
        changed = False
        for folder in folder_list:
            new_folder = folder_after_results(folder, results)
            if new_folder != folder and folder in updated:
                updated = [new_folder if f == folder else f for f in updated]
                try:
                    self._log(f"감시 폴더 경로 갱신: {new_folder}", "info")
                except Exception:
                    pass
                changed = True
        if changed:
            self._set_folders(updated)
            remember = self.__dict__.get("remember_var")
            if remember is not None and remember.get():
                try:
                    self._save_config()
                except Exception:
                    pass
        return self._get_folders() or updated

    def _progress_callback(self, operation: str):
        def callback(progress):
            phase, current, total = progress
            self._cmd_queue.put(("operation_progress", operation, phase, current, total))
        return callback

    def _on_batch_done(self, results: list, folders, resume_watch: bool):
        """일괄 변환 완료 후 결과를 표시하고 필요하면 감시를 재개한다."""
        converted = [r for r in results if r.status == "converted"]
        conflicts = [r for r in results if r.status == "conflict"]
        errors    = [r for r in results if r.status == "error"]
        skipped   = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r, notify=False)

        summary = (f"완료 — 변환: {len(converted)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        if converted and self.notify_on_convert_var.get():
            self._send_notification("Korean Filename Fixer3", summary)
        self.btn_once.config(state="normal")

        updated = self._sync_folders_after_conversion(folders, results)
        if resume_watch:
            self._resume_watch(updated)
        else:
            self._watch_paused_for_operation = False

    def _on_batch_failed(self, folders, resume_watch: bool, error: str):
        """일괄 변환 실패 후 버튼 상태를 복구하고 필요하면 감시를 재개한다."""
        self.status_var.set(f"변환 실패: {error}")
        self._log(f"변환 실패: {error}", "error")
        self.btn_once.config(state="normal")

        if resume_watch:
            self._resume_watch(folders)
        else:
            self._watch_paused_for_operation = False

    def _on_preview_done(self, results: list, folders, resume_watch: bool):
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
            self._resume_watch(folders)
        else:
            self._watch_paused_for_operation = False

    def _on_preview_failed(self, folders, resume_watch: bool, error: str):
        """미리보기 실패 후 버튼 상태를 복구하고 필요하면 감시를 재개한다."""
        self.status_var.set(f"미리보기 실패: {error}")
        self._log(f"미리보기 실패: {error}", "error")
        self.btn_preview.config(state="normal")

        if resume_watch:
            self._resume_watch(folders)
        else:
            self._watch_paused_for_operation = False

    def _on_startup_scan_done(self, results: list, folders):
        """시작 시 자동 스캔 완료 후 결과를 기록하고 감시를 시작한다."""
        converted = [r for r in results if r.status == "converted"]
        conflicts = [r for r in results if r.status == "conflict"]
        errors    = [r for r in results if r.status == "error"]
        skipped   = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r, notify=False)

        summary = (f"시작 시 누락분 처리 완료 — 변환: {len(converted)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        if converted and self.notify_on_convert_var.get():
            self._send_notification("Korean Filename Fixer3", summary)
        self._set_startup_scan_running(False)
        self._sync_folders_after_conversion(folders, results)
        self._start_watch()

    def _on_startup_scan_cancelled(self, results: list, folders):
        """시작 자동 스캔을 건너뛴 뒤 처리된 결과만 기록하고 감시를 시작한다."""
        converted = [r for r in results if r.status == "converted"]
        conflicts = [r for r in results if r.status == "conflict"]
        errors    = [r for r in results if r.status == "error"]
        skipped   = [r for r in results if r.status == "skipped"]

        for r in results:
            self._log_result(r, notify=False)

        summary = (f"시작 스캔 건너뜀 — 변환: {len(converted)}개 / "
                   f"충돌: {len(conflicts)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(skipped)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self._set_startup_scan_running(False)
        self._sync_folders_after_conversion(folders, results)
        self._start_watch()

    def _on_startup_scan_failed(self, folders, error: str):
        """시작 시 자동 스캔 실패 시에도 앱은 계속 실행하고 감시는 시작한다."""
        self.status_var.set(f"시작 시 누락분 스캔 실패: {error}")
        self._log(f"시작 시 누락분 스캔 실패: {error}", "error")
        self._set_startup_scan_running(False)
        self._start_watch()

    def _resume_watch(self, folders=None):
        try:
            targets = self._as_folder_list(folders) or self._get_folders()
            if not targets:
                self._watch_paused_for_operation = False
                return
            self.watcher.start_many(targets, self._get_exclude_patterns())
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.status_var.set(self._watch_status_text())
            self._log(f"감시 재개: {len(targets)}개 폴더 (제외: {self._exclude_patterns_text()})", "info")
            self._update_tray_title(watching=True)
            self._update_tray_menu_state(watching=True)
        except Exception as e:
            self._log(f"감시 재개 실패: {e}", "error")
        finally:
            self._watch_paused_for_operation = False

    # ─── 업데이트 확인 ────────────────────────────────────────

    def _maybe_auto_update_check(self):
        """시작 시 마지막 확인 후 7일이 지났으면 새 버전을 확인한다."""
        if self.__dict__.get("_update_check_in_progress"):
            return
        if not should_check(self.__dict__.get("_last_update_check", 0.0)):
            return
        self._start_update_check(manual=False)

    def _check_update_manual(self):
        """버튼으로 직접 새 버전을 확인한다."""
        if self._update_check_in_progress:
            self.status_var.set("업데이트 확인 중입니다.")
            return
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool):
        self._update_check_in_progress = True
        self.btn_update.config(state="disabled")
        if manual:
            self.status_var.set("업데이트 확인 중...")
        threading.Thread(
            target=self._run_update_check,
            args=(manual,),
            daemon=True,
        ).start()

    def _run_update_check(self, manual: bool):
        """백그라운드 스레드에서 최신 릴리스를 조회하고 결과를 큐에 넣는다."""
        try:
            found = fetch_latest_release(GITHUB_REPO)
        except Exception:
            found = None
        if found is None:
            self._cmd_queue.put(("update_check_done", False, "", "", manual))
        else:
            tag, url = found
            self._cmd_queue.put(
                ("update_check_done", is_newer(tag, APP_VERSION), tag, url, manual))

    def _on_update_check_done(self, has_update: bool, tag: str, url: str, manual: bool):
        """업데이트 확인 결과를 표시한다. 다운로드는 사용자 승인 후에만 연다."""
        self._update_check_in_progress = False
        self.btn_update.config(state="normal")
        if tag:
            self._last_update_check = time.time()
            try:
                if self.remember_var.get():
                    self._save_config()
            except Exception:
                pass
        if has_update and tag:
            msg = f"새 버전 {tag} 사용 가능 (현재 {APP_VERSION})"
            self.status_var.set(msg)
            self._log(msg, "info")
            if self._can_self_update():
                choice = messagebox.askyesnocancel(
                    "업데이트",
                    f"{msg}\n예: 다운로드 후 자동 설치\n아니오: 다운로드 페이지 열기")
                if choice is True:
                    self._start_update_download(tag)
                elif choice is False:
                    webbrowser.open(url)
            elif messagebox.askyesno("업데이트", f"{msg}\n다운로드 페이지를 여시겠습니까?"):
                webbrowser.open(url)
        elif manual:
            if tag:
                messagebox.showinfo("업데이트", f"최신 버전입니다. (현재 {APP_VERSION})")
                self.status_var.set(f"최신 버전입니다. ({APP_VERSION})")
            else:
                messagebox.showwarning(
                    "업데이트", "버전 확인에 실패했습니다. 네트워크 연결 후 다시 시도하세요.")
                self.status_var.set("업데이트 확인 실패")
        elif not tag:
            logging.warning("자동 업데이트 확인 실패")

    @staticmethod
    def _self_update_target():
        """자동 교체 가능하면 (kind, 경로, 기준경로), 아니면 None.

        kind "dir": Windows onedir (설치폴더, exe경로).
        kind "app": macOS .app 번들 (번들경로, 부모폴더).
        """
        if sys.platform == "win32":
            install_dir = current_install_dir()
            if not install_dir:
                return None
            exe_path = current_exe_path(install_dir)
            if not os.path.isfile(exe_path) or not is_writable_dir(install_dir):
                return None
            return ("dir", install_dir, exe_path)
        if sys.platform == "darwin":
            app_path = current_macos_app()
            if not app_path:
                return None
            parent = os.path.dirname(app_path)
            if not is_writable_dir(parent):
                return None
            return ("app", app_path, parent)
        return None

    def _can_self_update(self) -> bool:
        return self._self_update_target() is not None

    def _start_update_download(self, tag: str):
        self._update_check_in_progress = True
        self.btn_update.config(state="disabled")
        self.status_var.set(f"업데이트 다운로드 중... ({tag})")
        threading.Thread(
            target=self._run_update_download,
            args=(tag,),
            daemon=True,
        ).start()

    def _download_progress_callback(self):
        def callback(progress):
            downloaded, total = progress
            self._cmd_queue.put(("update_download_progress", downloaded, total))
        return callback

    def _run_update_download(self, tag: str):
        """백그라운드에서 zip+sha를 내려받아 검증·압축해제한다."""
        target = self._self_update_target()
        if target is None:
            self._cmd_queue.put(
                ("update_download_failed", "교체할 설치 위치를 찾을 수 없습니다."))
            return
        kind = target[0]
        asset_name = WINDOWS_ZIP_NAME if kind == "dir" else MACOS_ZIP_NAME
        asset_url = release_asset_url(GITHUB_REPO, tag, asset_name)
        staging = staging_dir()
        try:
            zip_path = os.path.join(staging, asset_name)
            download_update(asset_url, zip_path,
                            progress_callback=self._download_progress_callback())
            checksum_text = fetch_text(checksum_url(asset_url))
            if not verify_sha256(zip_path, parse_checksum(checksum_text or "")):
                self._cmd_queue.put(
                    ("update_download_failed",
                     "다운로드 검증 실패(체크섬 불일치). 다시 시도하세요."))
                return
            if kind == "dir":
                new_path = extract_update(zip_path, staging)
            else:
                new_path = extract_mac_app(zip_path, staging)
            self._cmd_queue.put(
                ("update_download_done", tag, kind, new_path, target[1], staging))
        except Exception as e:
            cleanup_staging(staging)
            self._cmd_queue.put(("update_download_failed", f"다운로드 실패: {e}"))

    def _on_update_download_progress(self, downloaded: int, total):
        if total:
            self.status_var.set(
                "업데이트 다운로드 중... "
                f"{downloaded / 1048576:.1f}/{total / 1048576:.1f} MB")
        else:
            self.status_var.set(
                f"업데이트 다운로드 중... {downloaded / 1048576:.1f} MB")

    def _on_update_download_done(self, tag: str, kind: str, new_path: str,
                                 place: str, staging: str):
        self._update_check_in_progress = False
        self.btn_update.config(state="normal")
        target = self._self_update_target()
        if target is None or target[0] != kind:
            cleanup_staging(staging)
            self.status_var.set("업데이트 설치 실패: 설치 위치 확인 불가")
            return
        if not messagebox.askyesno(
                "업데이트", f"{tag} 다운로드 완료. 지금 종료하고 설치하시겠습니까?"):
            cleanup_staging(staging)
            self.status_var.set("업데이트 설치 취소됨")
            return
        if kind == "dir":
            if not self._prepare_windows_swap(tag, new_path, target, staging):
                return
        else:
            if not self._prepare_mac_swap(tag, new_path, target, staging):
                return
        self._quit_app()

    def _prepare_windows_swap(self, tag: str, new_dir: str, target,
                              staging: str) -> bool:
        _kind, install_dir, exe_path = target
        staged_exe = os.path.join(new_dir, os.path.basename(exe_path))
        if not os.path.isfile(staged_exe):
            cleanup_staging(staging)
            messagebox.showwarning(
                "업데이트",
                "다운로드한 파일이 사라졌습니다(백신 격리 가능). "
                "백신 예외 등록 후 다시 시도하세요.")
            return False
        if not same_drive(install_dir, new_dir):
            # 디렉터리 move는 볼륨을 넘을 수 없어 실패가 확정적이다.
            # 구 배치처럼 진행했다간 설치 폴더를 잃을 수 있어 사전에 중단한다.
            cleanup_staging(staging)
            messagebox.showwarning(
                "업데이트",
                "임시 폴더와 설치 드라이브가 달라 자동 설치할 수 없습니다. "
                "다운로드 페이지에서 수동으로 설치하세요.")
            return False
        if sibling_instances(os.path.basename(exe_path)):
            # 형제 인스턴스가 폴더를 잠그고 있어 move가 실패한다. 안전 중단한다.
            cleanup_staging(staging)
            messagebox.showwarning(
                "업데이트",
                "다른 KoreanFilenameFixer 창이 실행 중입니다. "
                "모든 창을 종료한 뒤 다시 시도하세요.")
            return False
        zip_path = os.path.join(staging, WINDOWS_ZIP_NAME)
        batch_path = os.path.join(tempfile.gettempdir(), UPDATE_BATCH_NAME)
        try:
            write_update_batch(batch_path, os.getpid(), install_dir,
                               new_dir, exe_path,
                               cleanup_paths=[zip_path, staging])
        except Exception as e:
            cleanup_staging(staging)
            messagebox.showwarning("업데이트", f"설치 준비 실패: {e}")
            return False
        self._log(f"업데이트 설치 시작: {tag} — 앱을 종료하고 교체합니다.", "info")
        try:
            subprocess.Popen(
                ["cmd", "/c", batch_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except Exception as e:
            cleanup_staging(staging)
            self._log(f"업데이트 설치 실행 실패: {e}", "error")
            return False
        return True

    def _prepare_mac_swap(self, tag: str, new_app: str, target,
                          staging: str) -> bool:
        _kind, app_path, _parent = target
        if not verify_bundle(new_app):
            cleanup_staging(staging)
            messagebox.showwarning("업데이트", "다운로드한 앱 서명 검증 실패. 설치를 중단합니다.")
            return False
        zip_path = os.path.join(staging, MACOS_ZIP_NAME)
        script_path = os.path.join(tempfile.gettempdir(), MAC_UPDATE_SCRIPT_NAME)
        try:
            write_mac_update_script(script_path, os.getpid(), app_path,
                                    new_app,
                                    cleanup_paths=[zip_path, staging])
        except Exception as e:
            cleanup_staging(staging)
            messagebox.showwarning("업데이트", f"설치 준비 실패: {e}")
            return False
        self._log(f"업데이트 설치 시작: {tag} — 앱을 종료하고 교체합니다.", "info")
        try:
            subprocess.Popen(
                ["/bin/bash", script_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            cleanup_staging(staging)
            self._log(f"업데이트 설치 실행 실패: {e}", "error")
            return False
        return True

    def _on_update_download_failed(self, reason: str):
        self._update_check_in_progress = False
        self.btn_update.config(state="normal")
        self.status_var.set(f"업데이트 실패: {reason}")
        self._log(f"업데이트 실패: {reason}", "error")

    # ─── 로그 출력 ────────────────────────────────────────────

    def _dispatch_command(self, cmd):
        if isinstance(cmd, tuple):
            action, *args = cmd
            if action == "operation_progress":
                self._on_operation_progress(*args)
            elif action == "startup_scan_done":
                self._on_startup_scan_done(*args)
            elif action == "startup_scan_cancelled":
                self._on_startup_scan_cancelled(*args)
            elif action == "startup_scan_failed":
                self._on_startup_scan_failed(*args)
            elif action == "preview_done":
                self._on_preview_done(*args)
            elif action == "preview_failed":
                self._on_preview_failed(*args)
            elif action == "batch_done":
                self._on_batch_done(*args)
            elif action == "batch_failed":
                self._on_batch_failed(*args)
            elif action == "update_check_done":
                self._on_update_check_done(*args)
            elif action == "update_download_progress":
                self._on_update_download_progress(*args)
            elif action == "update_download_done":
                self._on_update_download_done(*args)
            elif action == "update_download_failed":
                self._on_update_download_failed(*args)
            return

        if cmd == "start":
            self._start_watch()
        elif cmd == "stop":
            self._stop_current_activity()
        elif cmd == "show":
            self._show_window()
        elif cmd == "quit":
            self._quit_app()

    def _on_operation_progress(
        self,
        operation: str,
        phase: str,
        current: int,
        total: int | None,
    ):
        if phase == "collect":
            self.status_var.set(f"{operation}: 항목 수집 중... {current:,}개 발견")
        elif phase == "convert" and total is not None:
            self.status_var.set(f"{operation}: {current:,}/{total:,}개 처리 중...")

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
                self._dispatch_command(self._cmd_queue.get_nowait())
        except queue.Empty:
            pass
        self._poll_after_id = self.after(100, self._poll_queue)

    def _health_check(self):
        """5초마다 감시 스레드가 살아있는지 확인하고, 죽었으면 자동 재시작한다."""
        if self._shutting_down:
            self._health_check_after_id = None
            return
        should_watch = str(self.btn_stop["state"]) == "normal"
        if (
            should_watch
            and not self._startup_scan_in_progress
            and not self._watch_paused_for_operation
            and not self.watcher.is_running
        ):
            folders = self._get_folders()
            self._log("감시 프로세스가 중단되어 자동으로 재시작합니다.", "error")
            if _APPKIT and hasattr(self, "_status_item"):
                self._status_item.button().setTitle_("K!")
            try:
                self.watcher.start_many(folders, self._get_exclude_patterns())
                self.status_var.set(self._watch_status_text())
                self._log("감시 재시작 완료.", "info")
                self._update_tray_title(watching=True)
                self._update_tray_menu_state(watching=True)
            except Exception as e:
                self._log(f"감시 재시작 실패: {e}", "error")
                self.btn_start.config(state="normal")
                self.btn_stop.config(state="disabled")
                self.status_var.set("감시 재시작 실패 — 수동으로 다시 시작하세요.")
                self._update_tray_title(watching=False)
                self._update_tray_menu_state(watching=False)
        self._health_check_after_id = self.after(5000, self._health_check)

    def _log_result(self, result: ConvertResult, notify: bool = True):
        if result.status == "converted":
            self._stats["converted"] += 1
            visual = nfd_to_visual(result.original)
            self._log(f"✓ {visual}  →  {result.converted}", "converted")
            if notify and self.notify_on_convert_var.get():
                self._watcher_notify_count += 1
                if self._watcher_notify_after_id is not None:
                    self.after_cancel(self._watcher_notify_after_id)
                self._watcher_notify_after_id = self.after(3000, self._flush_watcher_notification)
        elif result.status == "preview":
            visual = nfd_to_visual(result.original)
            self._log(f"→ {visual}  →  {result.converted}", "preview")
        elif result.status == "conflict":
            self._stats["conflict"] += 1
            visual = nfd_to_visual(result.original)
            self._log(f"! {visual}  충돌: {result.error}", "conflict")
        elif result.status == "error":
            self._stats["error"] += 1
            visual = nfd_to_visual(result.original)
            self._log(f"✗ {visual}  오류: {result.error}", "error")
        self._refresh_stats()

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _copy_log_selection(self, _event=None):
        try:
            selected = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        if selected:
            self.clipboard_clear()
            self.clipboard_append(selected)
        return "break"

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
        if self._startup_scan_cancel_event is not None:
            self._startup_scan_cancel_event.set()
        self.watcher.stop()
        for attr in ("_poll_after_id", "_health_check_after_id", "_watcher_notify_after_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            if hasattr(self, "_status_item"):
                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
        except Exception:
            pass
        self.destroy()

    def on_close(self):
        """창 닫기(X) 시 메뉴바로 숨긴다. Windows 등 트레이가 없으면 바로 종료한다."""
        if _APPKIT:
            self.withdraw()
        else:
            self._quit_app()
