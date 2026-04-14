"""
debug_watch.py
이벤트 원시 로그 출력 — 복사 시 어떤 이벤트가 오는지 확인
"""

import os
import unicodedata
import time
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

WATCH_FOLDER = "/Users/back/Downloads"

def check_name(label: str, name: str):
    nfc = unicodedata.normalize('NFC', name)
    is_nfd = (nfc != name)
    print(f"  {label}: {name!r}")
    print(f"    NFD={is_nfd} | len={len(name)} | codepoints={[hex(ord(c)) for c in name]}")

class DebugHandler(FileSystemEventHandler):
    def dispatch(self, event):
        path = getattr(event, 'dest_path', None) or event.src_path
        name = os.path.basename(path)
        print(f"[{event.event_type:10}] is_dir={event.is_directory}")
        check_name("watchdog 경로", name)

        # 실제 디렉토리를 직접 스캔해서 파일명 확인
        dirpath = os.path.dirname(path)
        if os.path.isdir(dirpath):
            for entry in os.scandir(dirpath):
                if unicodedata.normalize('NFC', entry.name) == unicodedata.normalize('NFC', name):
                    check_name("실제 파일명", entry.name)
        print()

observer = PollingObserver()
observer.schedule(DebugHandler(), WATCH_FOLDER, recursive=False)
observer.start()
print(f"감시 시작: {WATCH_FOLDER}")
print("복사 테스트 후 Ctrl+C로 종료\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()
observer.join()
