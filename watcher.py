"""
watcher.py
FSEventsObserver 기반 폴더 실시간 감시 모듈

[감시 방식 비교]
- FSEventsObserver: macOS 커널(FSEvents API)이 파일 변경을 알려줌 → 즉시 감지
- PollingObserver:  일정 간격으로 직접 폴더를 스캔해서 변화를 찾아냄 → 느리지만 안정적
Google Drive 같은 클라우드 폴더에서 FSEvents가 안 되면 자동으로 Polling으로 전환한다.
"""

import logging
import os
import time
import threading
import unicodedata
from typing import Callable

from watchdog.events import FileSystemEventHandler
from converter import convert_file, is_nfd


# ── Observer 생성 함수 ───────────────────────────────────────────────────────

def _make_observer():
    """
    FSEventsObserver 생성을 시도하고, 실패하면 PollingObserver로 대체한다.
    watchdog 라이브러리에서 Observer는 플랫폼에 맞는 최적 감시자를 자동 선택한다.
    macOS에서는 FSEventsObserver가 선택된다.
    """
    try:
        from watchdog.observers import Observer
        obs = Observer()
        logging.info("Using FSEventsObserver")
        return obs
    except Exception as e:
        logging.warning(f"FSEventsObserver unavailable ({e}), falling back to PollingObserver")
        from watchdog.observers.polling import PollingObserver
        return PollingObserver()


# 같은 파일에 대한 이벤트가 이 시간(초) 안에 중복으로 오면 무시한다.
# FSEvents는 파일 하나에 이벤트를 연속으로 여러 번 쏘는 경우가 있기 때문.
DEDUP_WINDOW = 0.2


# ── 파일 이벤트 핸들러 ───────────────────────────────────────────────────────

class NFDHandler(FileSystemEventHandler):
    """
    파일 생성/수정/이동 이벤트를 받아서 NFD 파일명이면 변환을 실행하는 핸들러.
    watchdog이 이벤트를 감지하면 아래 on_created / on_modified / on_moved를 호출한다.
    """

    def __init__(self, callback: Callable):
        """
        callback: 변환 결과(ConvertResult)를 받아 GUI 로그에 전달하는 함수
        """
        super().__init__()
        self.callback = callback
        self._recent: dict[str, float] = {}  # {경로: 마지막 처리 시각}
        self._dedup_lock = threading.Lock()  # _recent 딕셔너리를 여러 스레드가 동시에 수정하지 못하도록 잠금

    def on_created(self, event):
        """새 파일이 생성됐을 때 호출된다."""
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event):
        """파일 내용이 수정됐을 때 호출된다."""
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        """파일이 이동/이름변경됐을 때 호출된다. 목적지 경로를 처리한다."""
        target = event.dest_path if hasattr(event, 'dest_path') else event.src_path
        self._handle(target)

    def _handle(self, path: str):
        """
        실제 처리 로직.
        1) FSEvents는 경로를 NFC로 정규화해서 넘겨줄 수 있으므로,
           부모 폴더를 직접 스캔해서 실제 파일명(NFD일 수 있음)을 가져온다.
        2) NFD가 아니면 무시한다.
        3) 중복 이벤트를 걸러낸다.
        4) convert_file을 호출해서 변환하고 결과를 GUI로 전달한다.
        """
        dirpath = os.path.dirname(path)
        name = os.path.basename(path)

        # [핵심 포인트]
        # FSEventsObserver는 macOS 커널로부터 경로를 받는데,
        # 커널이 경로를 NFC로 정규화해서 전달하는 경우가 있다.
        # 그러면 is_nfd(name)이 False가 되어 NFD 파일을 놓치게 된다.
        # 해결: 이벤트 경로를 믿지 말고, 부모 폴더를 직접 스캔해서
        #       NFC로 비교했을 때 같은 파일을 찾아 실제 이름을 사용한다.
        actual_path = path
        if os.path.isdir(dirpath):
            nfc_name = unicodedata.normalize('NFC', name)
            try:
                for entry in os.scandir(dirpath):
                    # NFC로 정규화한 이름이 같으면 동일 파일로 판단
                    if unicodedata.normalize('NFC', entry.name) == nfc_name:
                        actual_path = os.path.join(dirpath, entry.name)
                        break
            except OSError:
                pass  # 폴더가 사라진 경우 등 예외는 조용히 무시

        actual_name = os.path.basename(actual_path)

        # 실제 파일명이 NFD가 아니면 변환 불필요
        if not is_nfd(actual_name):
            return

        # 중복 이벤트 방지: 같은 경로의 이벤트가 DEDUP_WINDOW 초 내에 또 오면 무시
        now = time.monotonic()
        with self._dedup_lock:
            if now - self._recent.get(actual_path, 0) < DEDUP_WINDOW:
                return
            self._recent[actual_path] = now

        # 파일이 실제로 존재할 때만 변환 시도 (이미 삭제됐을 수도 있음)
        if os.path.exists(actual_path):
            result = convert_file(actual_path)
            self.callback(result)  # 변환 결과를 GUI 큐에 넣는다


# ── 감시 관리자 ──────────────────────────────────────────────────────────────

class FolderWatcher:
    """
    폴더 감시의 시작/중지를 관리하는 클래스.
    GUI에서 이 클래스만 사용하면 된다.
    """

    def __init__(self, callback: Callable):
        self.callback = callback
        self._observer = None
        self._lock = threading.RLock()  # RLock: 같은 스레드에서 중첩 호출해도 데드락이 안 걸리는 잠금

    def start(self, folder: str):
        """
        폴더 감시를 시작한다.
        이미 감시 중이면 중지한 후 새로 시작한다.
        """
        with self._lock:
            self.stop()  # 기존 감시가 있으면 먼저 정리
            handler = NFDHandler(self.callback)
            self._observer = _make_observer()
            self._observer.schedule(handler, folder, recursive=True)  # 하위 폴더까지 재귀 감시
            self._observer.start()  # 감시 스레드 시작
            logging.info(f"Watching: {folder}")

    def stop(self):
        """폴더 감시를 중지하고 스레드를 정리한다."""
        with self._lock:
            if self._observer and self._observer.is_alive():
                self._observer.stop()   # 스레드에 종료 신호 전달
                self._observer.join()   # 스레드가 완전히 끝날 때까지 대기
            self._observer = None

    @property
    def is_running(self) -> bool:
        """현재 감시 중인지 여부를 반환한다."""
        return self._observer is not None and self._observer.is_alive()
