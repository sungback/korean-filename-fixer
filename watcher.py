"""
watcher.py
FSEventsObserver 기반 폴더 실시간 감시 모듈

FSEventsObserver(macOS 커널 기반, 즉시 감지)를 우선 사용하고
실패 시 PollingObserver(주기적 스캔)로 폴백한다.
"""

import logging
import os
import time
import threading
import unicodedata
from typing import Callable

from watchdog.events import FileSystemEventHandler
from converter import convert_file, is_nfd, should_ignore_name


def _make_observer():
    """FSEventsObserver 생성 시도, 실패 시 PollingObserver로 대체한다."""
    try:
        from watchdog.observers import Observer
        obs = Observer()
        logging.info("Using FSEventsObserver")
        return obs
    except Exception as e:
        logging.warning(f"FSEventsObserver unavailable ({e}), falling back to PollingObserver")
        from watchdog.observers.polling import PollingObserver
        return PollingObserver()


# FSEvents는 같은 파일에 이벤트를 연속으로 여러 번 발생시킬 수 있어 중복 방지 윈도우를 둔다
DEDUP_WINDOW = 0.2


class NFDHandler(FileSystemEventHandler):
    """파일 이벤트를 받아 NFD 파일명이면 변환을 실행하는 핸들러."""

    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback
        self._recent: dict[str, float] = {}
        self._dedup_lock = threading.Lock()

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        target = event.dest_path if hasattr(event, 'dest_path') else event.src_path
        self._handle(target)

    def _handle(self, path: str):
        dirpath = os.path.dirname(path)
        name = os.path.basename(path)

        # FSEventsObserver가 경로를 NFC로 정규화해 반환할 수 있으므로
        # 부모 디렉토리를 직접 스캔해 실제 파일명(NFD)을 가져온다
        actual_path = path
        if os.path.isdir(dirpath):
            nfc_name = unicodedata.normalize('NFC', name)
            try:
                for entry in os.scandir(dirpath):
                    if unicodedata.normalize('NFC', entry.name) == nfc_name:
                        actual_path = os.path.join(dirpath, entry.name)
                        break
            except OSError:
                pass

        actual_name = os.path.basename(actual_path)
        if should_ignore_name(actual_name):
            return
        if not is_nfd(actual_name):
            return

        now = time.monotonic()
        with self._dedup_lock:
            if now - self._recent.get(actual_path, 0) < DEDUP_WINDOW:
                return
            self._recent[actual_path] = now

        if os.path.exists(actual_path):
            result = convert_file(actual_path)
            self.callback(result)


class FolderWatcher:
    """폴더 감시의 시작/중지를 관리한다. GUI에서 이 클래스만 사용하면 된다."""

    def __init__(self, callback: Callable):
        self.callback = callback
        self._observer = None
        self._lock = threading.RLock()

    def start(self, folder: str):
        """감시 시작. 이미 실행 중이면 중지 후 재시작한다."""
        with self._lock:
            self.stop()
            handler = NFDHandler(self.callback)
            self._observer = _make_observer()
            self._observer.schedule(handler, folder, recursive=True)
            self._observer.start()
            logging.info(f"Watching: {folder}")

    def stop(self):
        """감시를 중지하고 스레드를 정리한다."""
        with self._lock:
            if self._observer and self._observer.is_alive():
                self._observer.stop()
                self._observer.join()
            self._observer = None

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
