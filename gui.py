"""
gui.py
tkinter 기반 GUI 모듈

[tkinter 기본 개념]
- tkinter는 Python 내장 GUI 라이브러리다.
- 모든 UI 수정(버튼 텍스트 변경, 로그 추가 등)은 반드시 메인 스레드에서만 해야 한다.
  다른 스레드에서 UI를 건드리면 크래시가 날 수 있다.
- 그래서 백그라운드 작업(파일 변환 등)은 별도 스레드로 돌리고,
  결과는 Queue에 넣어서 메인 스레드가 꺼내 표시하는 패턴을 사용한다.
"""

import logging
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk


def setup_logging():
    """
    로그를 파일과 콘솔 두 곳에 동시에 출력하도록 설정한다.
    로그 파일은 홈 디렉토리(~/)에 KoreanFilenameFixer.log로 저장된다.
    """
    import os
    log_path = os.path.join(os.path.expanduser("~"), "KoreanFilenameFixer.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),  # 파일에 저장
            logging.StreamHandler(),                           # 터미널에 출력
        ]
    )

# 임포트 전에 로깅을 먼저 세팅해야 converter/watcher의 로그도 파일에 남는다
setup_logging()

from converter import convert_folder, ConvertResult
from watcher import FolderWatcher


# ── 메인 앱 클래스 ───────────────────────────────────────────────────────────

class App(tk.Tk):
    """
    메인 창 클래스. tk.Tk를 상속받아 tkinter 창 자체가 된다.
    """

    def __init__(self):
        super().__init__()
        self.title("Korean Filename Fixer")
        self.resizable(True, True)
        self.minsize(500, 400)
        self.configure(padx=16, pady=16)

        # 백그라운드 스레드 → 메인 스레드로 변환 결과를 전달하는 큐
        # Queue는 스레드 안전(thread-safe)하게 설계되어 있어서 여러 스레드가 동시에 써도 안전하다
        self._queue: queue.Queue = queue.Queue()

        # 파일 감시자: 이벤트 발생 시 결과를 _queue에 넣도록 연결
        self.watcher = FolderWatcher(callback=self._queue.put)

        self._build_ui()    # UI 위젯 배치
        self._poll_queue()  # 100ms마다 큐 확인 시작

    # ── UI 구성 ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        """창에 들어갈 위젯들을 만들고 배치한다."""

        # 폴더 선택 행
        folder_frame = tk.Frame(self)
        folder_frame.pack(fill="x", pady=(0, 10))

        tk.Label(folder_frame, text="감시 폴더:").pack(side="left")
        self.folder_var = tk.StringVar()  # 선택된 폴더 경로를 저장하는 변수
        tk.Entry(folder_frame, textvariable=self.folder_var, width=42,
                 state="readonly").pack(side="left", padx=(6, 4))  # 직접 입력 불가(readonly)
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

        # 로그 영역 (스크롤 가능한 텍스트 박스)
        self.log = scrolledtext.ScrolledText(self, width=60, height=18,
                                             state="disabled", wrap="none",
                                             font=("Menlo", 11))
        self.log.pack(fill="both", expand=True)

        # 로그 줄마다 색상을 다르게 표시하기 위한 태그 설정
        self.log.tag_config("converted", foreground="#66ff66")  # 초록: 변환 성공
        self.log.tag_config("skipped",   foreground="#aaaaaa")  # 회색: 건너뜀
        self.log.tag_config("error",     foreground="#ff6666")  # 빨강: 오류
        self.log.tag_config("info",      foreground="#ffffff")  # 흰색: 일반 정보

    # ── 이벤트 핸들러 ────────────────────────────────────────────────────────

    def _choose_folder(self):
        """'선택' 버튼 클릭 시 폴더 선택 다이얼로그를 열고 경로를 저장한다."""
        folder = filedialog.askdirectory(title="감시할 폴더를 선택하세요")
        if folder:
            self.folder_var.set(folder)
            self._log(f"폴더 선택: {folder}", "info")
            self.status_var.set("폴더가 선택되었습니다.")

    def _start_watch(self):
        """'▶ 감시 시작' 버튼 클릭 시 실시간 감시를 시작한다."""
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        try:
            self.watcher.start(folder)
        except Exception as e:
            # 감시 시작에 실패하면 에러를 화면에 표시하고 중단
            self.status_var.set(f"감시 시작 실패: {e}")
            self._log(f"오류: {e}", "error")
            return
        self.btn_start.config(state="disabled")  # 시작 버튼 비활성화
        self.btn_stop.config(state="normal")      # 중지 버튼 활성화
        self.status_var.set(f"감시 중: {folder}")
        self._log("감시를 시작했습니다.", "info")

    def _stop_watch(self):
        """'■ 중지' 버튼 클릭 시 감시를 중단한다."""
        self.watcher.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("감시가 중지되었습니다.")
        self._log("감시를 중지했습니다.", "info")

    def _convert_once(self):
        """
        '한 번 변환' 버튼 클릭 시 선택된 폴더 전체를 한 번 스캔해서 변환한다.

        [중요] 변환 작업은 시간이 걸릴 수 있으므로 백그라운드 스레드에서 실행한다.
        메인 스레드에서 실행하면 변환이 끝날 때까지 창이 완전히 멈춘다.

        [레이스 컨디션 방지]
        감시 중에 '한 번 변환'을 같이 실행하면 같은 파일을 두 스레드가
        동시에 변환하려다 충돌할 수 있다. 그래서 변환 전에 감시를 잠깐 멈추고,
        변환이 끝나면 자동으로 재개한다.
        """
        folder = self.folder_var.get()
        if not folder:
            self.status_var.set("먼저 폴더를 선택하세요.")
            return
        self.status_var.set("변환 중...")
        self.btn_once.config(state="disabled")

        # 현재 감시 중인지 기억해두고, 감시 중이면 일시 중단
        was_watching = self.watcher.is_running
        if was_watching:
            self.watcher.stop()

        def run():
            """백그라운드 스레드에서 실행되는 실제 변환 작업."""
            results = convert_folder(folder)
            converted = [r for r in results if r.status == "converted"]
            errors    = [r for r in results if r.status == "error"]

            def on_done():
                """변환 완료 후 메인 스레드에서 UI를 업데이트하는 함수."""
                self._show_batch_results(results, converted, errors)

                # 원래 감시 중이었으면 변환 완료 후 감시를 다시 시작
                if was_watching:
                    try:
                        self.watcher.start(folder)
                        self.btn_start.config(state="disabled")
                        self.btn_stop.config(state="normal")
                        self.status_var.set(f"감시 중: {folder}")
                        self._log("감시 재개", "info")
                    except Exception as e:
                        self._log(f"감시 재개 실패: {e}", "error")

            # tkinter UI는 메인 스레드에서만 수정 가능하므로
            # self.after(0, ...)를 통해 메인 스레드에 실행을 예약한다
            self.after(0, on_done)

        threading.Thread(target=run, daemon=True).start()
        # daemon=True: 메인 창이 닫히면 이 스레드도 자동으로 종료된다

    def _show_batch_results(self, results, converted, errors):
        """'한 번 변환' 완료 후 결과를 로그에 출력하고 요약 상태를 표시한다."""
        for r in results:
            self._log_result(r)
        summary = (f"완료 — 변환: {len(converted)}개 / "
                   f"오류: {len(errors)}개 / "
                   f"건너뜀: {len(results)-len(converted)-len(errors)}개")
        self.status_var.set(summary)
        self._log(summary, "info")
        self.btn_once.config(state="normal")  # 버튼 다시 활성화

    def _poll_queue(self):
        """
        100ms마다 반복 호출되어 큐에 쌓인 변환 결과를 로그에 표시한다.

        [왜 이렇게 하나?]
        감시 스레드는 변환 결과를 Queue에 넣는다.
        UI 업데이트는 메인 스레드에서만 해야 하므로,
        메인 스레드가 주기적으로 큐를 확인해서 결과를 꺼내 표시한다.
        """
        try:
            while True:
                result = self._queue.get_nowait()  # 큐에서 항목을 꺼냄 (없으면 즉시 예외)
                self._log_result(result)
        except queue.Empty:
            pass  # 큐가 비어있으면 아무것도 하지 않음

        # 100ms 후에 이 함수를 다시 호출하도록 예약 (무한 반복)
        self.after(100, self._poll_queue)

    # ── 로그 유틸 ────────────────────────────────────────────────────────────

    def _log_result(self, result: ConvertResult):
        """변환 결과 1개를 로그에 출력한다. skipped는 노이즈가 많아서 생략한다."""
        icons = {"converted": "✓", "skipped": "–", "error": "✗"}
        icon = icons.get(result.status, "?")
        if result.status == "converted":
            msg = f"{icon} {result.original}  →  {result.converted}"
        elif result.status == "error":
            msg = f"{icon} {result.original}  오류: {result.error}"
        else:
            return  # skipped는 출력 생략
        self._log(msg, result.status)

    def _log(self, msg: str, tag: str = "info"):
        """
        로그 텍스트박스에 메시지를 추가한다.
        텍스트박스는 평소에 state="disabled"(읽기 전용)로 두고,
        쓸 때만 잠깐 "normal"로 바꿨다가 다시 잠근다.
        """
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)  # tag에 따라 색상이 달라진다
        self.log.see("end")                       # 자동으로 맨 아래로 스크롤
        self.log.config(state="disabled")

    # ── 종료 처리 ────────────────────────────────────────────────────────────

    def on_close(self):
        """창을 닫을 때 감시 스레드를 깔끔하게 정리하고 종료한다."""
        self.watcher.stop()
        self.destroy()
