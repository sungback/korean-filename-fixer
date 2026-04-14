"""
gui.py
tkinter 기반 GUI 모듈
"""

import queue
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

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
        self._build_ui()
        self._poll_queue()

    # ─── UI 구성 ───────────────────────────────────────────

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

        # 구분선
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(0, 8))

        # 상태 표시줄
        self.status_var = tk.StringVar(value="폴더를 선택하세요.")
        tk.Label(self, textvariable=self.status_var,
                 anchor="w", fg="gray").pack(fill="x", pady=(0, 4))

        # 로그 영역
        self.log = scrolledtext.ScrolledText(self, width=60, height=18,
                                             state="disabled", wrap="none",
                                             font=("Menlo", 11))
        self.log.pack(fill="both", expand=True)

        # 로그 색상 태그
        self.log.tag_config("converted", foreground="#66ff66")
        self.log.tag_config("skipped",   foreground="#aaaaaa")
        self.log.tag_config("error",     foreground="#ff6666")
        self.log.tag_config("info",      foreground="#ffffff")

    # ─── 이벤트 핸들러 ────────────────────────────────────

    def _choose_folder(self):
        folder = filedialog.askdirectory(title="감시할 폴더를 선택하세요")
        if folder:
            self.folder_var.set(folder)
            self._log(f"폴더 선택: {folder}", "info")
            self.status_var.set("폴더가 선택되었습니다.")

    def _start_watch(self):
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        self.watcher.start(folder)
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
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        self.status_var.set("변환 중...")
        self.btn_once.config(state="disabled")

        def run():
            results = convert_folder(folder)
            converted = [r for r in results if r.status == "converted"]
            errors    = [r for r in results if r.status == "error"]
            self.after(0, lambda: self._show_batch_results(results, converted, errors))

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
        """메인 스레드에서 100ms마다 queue를 비워 로그에 표시."""
        try:
            while True:
                result = self._queue.get_nowait()
                self._log_result(result)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ─── 로그 유틸 ────────────────────────────────────────

    def _log_result(self, result: ConvertResult):
        icons = {"converted": "✓", "skipped": "–", "error": "✗"}
        icon = icons.get(result.status, "?")
        if result.status == "converted":
            msg = f"{icon} {result.original}  →  {result.converted}"
        elif result.status == "error":
            msg = f"{icon} {result.original}  오류: {result.error}"
        else:
            return  # skipped는 로그 생략 (노이즈 방지)
        self._log(msg, result.status)

    def _log(self, msg: str, tag: str = "info"):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    # ─── 종료 처리 ────────────────────────────────────────

    def on_close(self):
        self.watcher.stop()
        self.destroy()
