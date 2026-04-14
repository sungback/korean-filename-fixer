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

# converter/watcher의 로그도 파일에 남도록 임포트 전에 설정한다
setup_logging()

from converter import convert_folder, ConvertResult
from watcher import FolderWatcher


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Korean Filename Fixer")
        self.resizable(True, True)
        self.minsize(500, 400)
        self.configure(padx=16, pady=16)

        self._queue: queue.Queue = queue.Queue()
        self.watcher = FolderWatcher(callback=self._queue.put)
        self._dark = self._is_dark_mode()

        self._build_ui()
        self._load_config()
        self._poll_queue()

    def _is_dark_mode(self) -> bool:
        """시스템 테마가 다크 모드인지 감지한다."""
        try:
            bg = self.tk.call("ttk::style", "lookup", "TFrame", "-background")
            r, g, b = [x >> 8 for x in self.winfo_rgb(bg)]
            return (0.299 * r + 0.587 * g + 0.114 * b) < 128
        except Exception:
            return False

    def _build_ui(self):
        # 폴더 선택 행
        folder_frame = tk.Frame(self)
        folder_frame.pack(fill="x", pady=(0, 10))

        tk.Label(folder_frame, text="감시 폴더:").pack(side="left")
        self.folder_var = tk.StringVar()
        tk.Entry(folder_frame, textvariable=self.folder_var, width=42,
                 state="readonly").pack(side="left", padx=(6, 4))
        tk.Button(folder_frame, text="선택",
                  command=self._choose_folder).pack(side="left")
        self.remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(folder_frame, text="기억", variable=self.remember_var,
                       command=self._on_remember_toggle).pack(side="left", padx=(6, 0))

        # 버튼 행
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", pady=(0, 10))

        self.btn_start = tk.Button(btn_frame, text="▶ 감시 시작", width=12,
                                   command=self._start_watch)
        self.btn_start.pack(side="left", padx=(0, 6))

        self.btn_stop = tk.Button(btn_frame, text="■ 중지", width=8,
                                  state="disabled", command=self._stop_watch)
        self.btn_stop.pack(side="left", padx=(0, 6))

        self.btn_once = tk.Button(btn_frame, text="한 번 변환", width=10,
                                  command=self._convert_once)
        self.btn_once.pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.status_var = tk.StringVar(value="폴더를 선택하세요.")
        tk.Label(self, textvariable=self.status_var,
                 anchor="w", fg="gray").pack(fill="x", pady=(0, 4))

        if self._dark:
            log_bg, log_fg = "#1e1e1e", "#dddddd"
            c_converted, c_error = "#66ff66", "#ff6666"
        else:
            log_bg, log_fg = "#ffffff", "#333333"
            c_converted, c_error = "#007700", "#cc0000"

        self.log = scrolledtext.ScrolledText(self, width=60, height=18,
                                             state="disabled", wrap="none",
                                             font=("Menlo", 11),
                                             bg=log_bg, fg=log_fg,
                                             insertbackground=log_fg)
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("converted", foreground=c_converted)
        self.log.tag_config("error",     foreground=c_error)
        self.log.tag_config("info",      foreground=log_fg)

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

    def _convert_once(self):
        """
        폴더 전체를 한 번 스캔해서 변환한다.
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

        def run():
            results = convert_folder(folder)
            converted = [r for r in results if r.status == "converted"]
            errors    = [r for r in results if r.status == "error"]

            def on_done():
                self._show_batch_results(results, converted, errors)
                if was_watching:
                    try:
                        self.watcher.start(folder)
                        self.btn_start.config(state="disabled")
                        self.btn_stop.config(state="normal")
                        self.status_var.set(f"감시 중: {folder}")
                        self._log("감시 재개", "info")
                    except Exception as e:
                        self._log(f"감시 재개 실패: {e}", "error")

            self.after(0, on_done)

        threading.Thread(target=run, daemon=True).start()

    def _show_batch_results(self, results, converted, errors):
        for r in results:
            self._log_result(r)
        summary = (f"완료 — 변환: {len(converted)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(results)-len(converted)-len(errors)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self.btn_once.config(state="normal")

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
            self._log(f"✓ {result.original}  →  {result.converted}", "converted")
        elif result.status == "error":
            self._log(f"✗ {result.original}  오류: {result.error}", "error")

    def _log(self, msg: str, tag: str = "info"):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        self.watcher.stop()
        self.destroy()
