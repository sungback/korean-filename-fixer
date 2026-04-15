"""
watcher.py
watchdog 기반 폴더 실시간 감시 모듈

watchdog의 기본 Observer를 사용하고,
가져오지 못하면 PollingObserver(주기적 스캔)로 폴백한다.
macOS에서는 기본 Observer가 FSEvents 기반으로 동작한다.
"""

import logging
import os
import time
import threading
import unicodedata
from typing import Callable

from watchdog.events import FileSystemEventHandler
from converter import clean_exclude_patterns, convert_file, is_nfd, should_exclude_path, should_ignore_name


class NFDHandler(FileSystemEventHandler):
    """파일 이벤트를 받아 NFD 파일명이면 변환을 실행하는 핸들러."""

    # FSEvents는 같은 파일에 이벤트를 연속으로 여러 번 발생시킬 수 있어 중복 방지 윈도우를 둔다
    _DEDUP_WINDOW = 0.2

    def __init__(self, callback: Callable, exclude_patterns=None):
        super().__init__()
        self.callback = callback
        self.exclude_patterns = clean_exclude_patterns(exclude_patterns)
        self._recent: dict[str, float] = {}
        self._dedup_lock = threading.Lock()

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path, is_directory=False)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path, is_directory=False)

    def on_moved(self, event):
        target = event.dest_path if hasattr(event, 'dest_path') else event.src_path
        self._handle(target, is_directory=event.is_directory)

    def _resolve_actual_path(self, path: str) -> str:
        """FSEventsObserver가 경로를 NFC로 정규화해 반환할 수 있으므로
        부모 디렉토리를 직접 스캔해 실제 파일명(NFD)을 가져온다."""
        dirpath = os.path.dirname(path)
        name = os.path.basename(path)
        if not os.path.isdir(dirpath):
            return path
        nfc_name = unicodedata.normalize('NFC', name)
        try:
            with os.scandir(dirpath) as entries:
                for entry in entries:
                    if unicodedata.normalize('NFC', entry.name) == nfc_name:
                        return os.path.join(dirpath, entry.name)
        except OSError:
            pass
        return path

    def _is_duplicate(self, path: str) -> bool:
        """짧은 시간 내 동일 경로 이벤트면 True를 반환한다."""
        now = time.monotonic()
        with self._dedup_lock:
            if now - self._recent.get(path, 0) < self._DEDUP_WINDOW:
                return True
            self._recent[path] = now
            # 만료된 항목을 정리해 장시간 실행 시 메모리 누수를 방지한다
            stale = [p for p, t in self._recent.items() if now - t >= self._DEDUP_WINDOW]
            for p in stale:
                del self._recent[p]
        return False

    def _handle(self, path: str, is_directory: bool):
        if should_exclude_path(path, self.exclude_patterns, is_directory=is_directory):
            return

        actual_path = self._resolve_actual_path(path)
        actual_name = os.path.basename(actual_path)

        if should_exclude_path(actual_path, self.exclude_patterns, is_directory=is_directory):
            return
        if should_ignore_name(actual_name) or not is_nfd(actual_name):
            return
        if self._is_duplicate(actual_path):
            return
        if os.path.exists(actual_path):
            result = convert_file(actual_path)
            self.callback(result)


class FolderWatcher:
    """폴더 감시의 시작/중지를 관리한다. GUI에서 이 클래스만 사용하면 된다."""

    def __init__(self, callback: Callable):
        self.callback = callback
        self.exclude_patterns: list[str] = []
        self._observer = None
        self._lock = threading.RLock()

    def start(self, folder: str, exclude_patterns=None):
        """감시 시작. 이미 실행 중이면 중지 후 재시작한다."""
        with self._lock:
            self.stop()
            self.exclude_patterns = clean_exclude_patterns(exclude_patterns)
            handler = NFDHandler(self.callback, self.exclude_patterns)
            self._observer = self._make_observer()
            self._observer.schedule(handler, folder, recursive=True)
            self._observer.start()
            logging.info(f"Watching: {folder} (exclude={self.exclude_patterns})")

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

    @staticmethod
    def _make_observer():
        """기본 Observer를 생성하고, 실패 시 PollingObserver로 대체한다."""
        try:
            from watchdog.observers import Observer
            obs = Observer()
            logging.info("Using watchdog Observer")
            return obs
        except Exception as e:
            logging.warning(f"watchdog Observer unavailable ({e}), falling back to PollingObserver")
            from watchdog.observers.polling import PollingObserver
            return PollingObserver()
