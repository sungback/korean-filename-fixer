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

        def showWindow_(self, sender):
            if self._show_cb:
                self._show_cb()

        def quitApp_(self, sender):
            if self._quit_cb:
                self._quit_cb()


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".korean_filename_fixer.json")


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


from converter import convert_folder, ConvertResult, nfd_to_visual
from watcher import FolderWatcher


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Korean Filename Fixer")
        self.resizable(True, True)
        self.configure(padx=16, pady=16)

        self._queue: queue.Queue = queue.Queue()
        self.watcher = FolderWatcher(callback=self._queue.put)
        self._dark = self._is_dark_mode()

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
                    "converted": "#66ff66", "error": "#ff6666"}
        return {"bg": "#ffffff", "fg": "#333333",
                "converted": "#007700", "error": "#cc0000"}

    # ─── UI 구성 ──────────────────────────────────────────────

    def _build_ui(self):
        self._build_folder_row()
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

        self.btn_once = tk.Button(frame, text="기존 파일들 한 번에 변환",
                                  command=self._convert_once)
        self.btn_once.pack(side="left", padx=(0, 6))

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
            folder = data.get("folder", "")
            if folder and os.path.isdir(folder):
                self.folder_var.set(folder)
                self.remember_var.set(True)
                self.status_var.set("저장된 폴더를 불러왔습니다.")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_config(self):
        """체크박스 ON이면 폴더 경로를 저장하고, OFF이면 config 파일을 삭제한다."""
        if self.remember_var.get() and self.folder_var.get():
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"folder": self.folder_var.get()}, f)
        else:
            try:
                os.remove(CONFIG_PATH)
            except FileNotFoundError:
                pass

    def _on_remember_toggle(self):
        self._save_config()

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
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        try:
            self.watcher.start(folder)
        except Exception as e:
            self.status_var.set(f"감시 시작 실패: {e}")
            self._log(f"오류: {e}", "error")
            return
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set(f"감시 중: {folder}")
        self._log("감시를 시작했습니다.", "info")

    def _stop_watch(self):
        self.watcher.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("감시가 중지되었습니다.")
        self._log("감시를 중지했습니다.", "info")

    # ─── 일괄 변환 ────────────────────────────────────────────

    def _convert_once(self):
        """폴더 전체를 한 번 스캔해서 변환한다.

        감시 중이면 레이스 컨디션 방지를 위해 변환 동안 감시를 일시 중단한다.
        """
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return

        self.status_var.set("변환 중...")
        self.btn_once.config(state="disabled")

        was_watching = self.watcher.is_running
        if was_watching:
            self.watcher.stop()

        threading.Thread(
            target=self._run_batch_convert,
            args=(folder, was_watching),
            daemon=True,
        ).start()

    def _run_batch_convert(self, folder: str, resume_watch: bool):
        """백그라운드 스레드에서 일괄 변환을 실행하고 결과를 메인 스레드에 전달한다."""
        results = convert_folder(folder)
        self.after(0, self._on_batch_done, results, folder, resume_watch)

    def _on_batch_done(self, results: list, folder: str, resume_watch: bool):
        """일괄 변환 완료 후 결과를 표시하고 필요하면 감시를 재개한다."""
        converted = [r for r in results if r.status == "converted"]
        errors    = [r for r in results if r.status == "error"]

        for r in results:
            self._log_result(r)

        summary = (f"완료 — 변환: {len(converted)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(results)-len(converted)-len(errors)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self.btn_once.config(state="normal")

        if resume_watch:
            self._resume_watch(folder)

    def _resume_watch(self, folder: str):
        try:
            self.watcher.start(folder)
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.status_var.set(f"감시 중: {folder}")
            self._log("감시 재개", "info")
        except Exception as e:
            self._log(f"감시 재개 실패: {e}", "error")

    # ─── 로그 출력 ────────────────────────────────────────────

    def _poll_queue(self):
        """100ms마다 큐를 비워 감시 스레드의 변환 결과를 로그에 표시한다."""
        try:
            while True:
                self._log_result(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _log_result(self, result: ConvertResult):
        if result.status == "converted":
            visual = nfd_to_visual(result.original)
            self._log(f"✓ {visual}  →  {result.converted}", "converted")
        elif result.status == "error":
            visual = nfd_to_visual(result.original)
            self._log(f"✗ {visual}  오류: {result.error}", "error")

    def _log(self, msg: str, tag: str = "info"):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
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
            self._tray_delegate._show_cb = self._show_window
            self._tray_delegate._quit_cb = self._quit_app

            show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "창 열기", "showWindow:", "")
            show_item.setTarget_(self._tray_delegate)

            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "종료", "quitApp:", "")
            quit_item.setTarget_(self._tray_delegate)

            menu = NSMenu.alloc().init()
            menu.addItem_(show_item)
            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(quit_item)

            status_bar = NSStatusBar.systemStatusBar()
            self._status_item = status_bar.statusItemWithLength_(
                NSVariableStatusItemLength)
            self._status_item.button().setTitle_("K")
            self._status_item.setMenu_(menu)
        except Exception:
            logging.exception("트레이 아이콘 설정 실패")

    def _show_window(self):
        """메뉴바 또는 독 아이콘 클릭 시 창을 복원한다."""
        self.after(0, self.deiconify)
        self.after(0, self.lift)
        self.after(0, self.focus_force)

    def _quit_app(self):
        """감시 중지 → 메뉴바 아이콘 제거 → 창 종료."""
        self.watcher.stop()
        try:
            if hasattr(self, "_status_item"):
                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
        except Exception:
            pass
        self.after(0, self.destroy)

    def on_close(self):
        """창 닫기(X) 시 메뉴바로 숨긴다. 종료는 메뉴바 '종료' 사용."""
        self.withdraw()
