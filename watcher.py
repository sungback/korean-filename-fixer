"""
watcher.py
watchdog 기반 폴더 실시간 감시 모듈
"""

import os
import threading
from typing import Callable
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from converter import convert_file, is_nfd


class NFDHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable):
        """
        callback: ConvertResult를 받아 UI에 로그를 전달하는 함수
        """
        super().__init__()
        self.callback = callback

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
        name = os.path.basename(path)
        # is_nfd()가 False면 이미 NFC이므로 자연스럽게 중복 처리 방지됨
        if is_nfd(name) and os.path.exists(path):
            result = convert_file(path)
            self.callback(result)


class FolderWatcher:
    def __init__(self, callback: Callable):
        self.callback = callback
        self._observer: PollingObserver | None = None
        self._lock = threading.RLock()  # 재진입 가능 락 (start→stop 중첩 호출 허용)

    def start(self, folder: str):
        """폴더 감시 시작. 이미 실행 중이면 중지 후 재시작."""
        with self._lock:
            self.stop()
            handler = NFDHandler(self.callback)
            self._observer = PollingObserver()
            self._observer.schedule(handler, folder, recursive=True)
            self._observer.start()

    def stop(self):
        """폴더 감시 중지."""
        with self._lock:
            if self._observer and self._observer.is_alive():
                self._observer.stop()
                self._observer.join()
            self._observer = None

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
