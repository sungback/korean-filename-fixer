#!/usr/bin/env python3
"""Run a narrow Google Drive smoke test for NFD to NFC conversion.

The script creates one kff_smoke_* folder under Google Drive, verifies watcher
conversion and batch conversion there, then removes only that test folder.
"""

import argparse
import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path


GOOGLE_DRIVE_ROOT_NAMES = ("\ub0b4 \ub4dc\ub77c\uc774\ube0c", "My Drive")
WATCH_FILE_NFC = "\uc2e4\uc2dc\uac04\ud30c\uc77c.txt"
BATCH_DIR_NFC = "\uc77c\uad04\ud3f4\ub354"
BATCH_FILE_NFC = "\uc77c\uad04\ud30c\uc77c.txt"


def nfd(name: str) -> str:
    return unicodedata.normalize("NFD", name)


def is_nfd(name: str) -> bool:
    return len(name) != len(unicodedata.normalize("NFC", name))


def entries(path: Path) -> dict[str, str]:
    return {
        entry.name: ("NFD" if is_nfd(entry.name) else "NFC")
        for entry in os.scandir(path)
    }


def fail(message: str):
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def find_default_drive_root() -> Path | None:
    home = Path.home()
    candidates = [home / name for name in GOOGLE_DRIVE_ROOT_NAMES]

    cloud_storage = home / "Library" / "CloudStorage"
    if cloud_storage.is_dir():
        for provider in cloud_storage.iterdir():
            if not provider.name.startswith("GoogleDrive-"):
                continue
            candidates.extend(provider / name for name in GOOGLE_DRIVE_ROOT_NAMES)

    for candidate in candidates:
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate.resolve()
    return None


def wait_for_nfc_entry(path: Path, name: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = entries(path)
        if current.get(name) == "NFC":
            return True
        time.sleep(0.2)
    return False


def safe_cleanup(base: Path, drive_root: Path):
    if base.parent != drive_root or not base.name.startswith("kff_smoke_"):
        print(f"CLEANUP_SKIPPED_UNSAFE={base}")
        return
    shutil.rmtree(base, ignore_errors=True)
    print(f"CLEANUP_REMOVED={base}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Smoke-test Korean Filename Fixer in a Google Drive folder.",
    )
    parser.add_argument(
        "--drive-root",
        help="Writable Google Drive folder. Defaults to detected My Drive.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root containing converter.py and watcher.py.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for watcher conversion.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the kff_smoke_* folder for manual inspection.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    drive_root = Path(args.drive_root).expanduser().resolve() if args.drive_root else None
    if drive_root is None:
        drive_root = find_default_drive_root()
    if drive_root is None or not drive_root.is_dir():
        fail("Google Drive root not found. Pass --drive-root explicitly.")

    sys.path.insert(0, str(repo_root))
    from converter import convert_folder, preview_folder
    from watcher import FolderWatcher

    base = drive_root / f"kff_smoke_{int(time.time())}_{os.getpid()}"
    if base.exists():
        fail(f"test folder already exists: {base}")

    events = []
    watcher = None
    created = False

    try:
        base.mkdir()
        created = True
        print(f"TEST_FOLDER={base}")

        watcher = FolderWatcher(callback=events.append)
        watcher.start(str(base), [])

        (base / nfd(WATCH_FILE_NFC)).write_text("watcher smoke\n", encoding="utf-8")
        if not wait_for_nfc_entry(base, WATCH_FILE_NFC, args.timeout):
            fail(f"watcher did not create NFC file; entries={entries(base)}")

        watcher.stop()
        watcher = None

        converted_events = [result for result in events if result.status == "converted"]
        if not converted_events:
            fail(f"watcher did not report conversion; events={events}")
        print(f"WATCHER_CONVERTED={len(converted_events)}")

        batch_dir = base / nfd(BATCH_DIR_NFC)
        batch_dir.mkdir()
        (batch_dir / nfd(BATCH_FILE_NFC)).write_text("batch smoke\n", encoding="utf-8")

        previews = preview_folder(str(base))
        preview_count = sum(1 for result in previews if result.status == "preview")
        if preview_count < 2:
            fail(f"expected at least 2 preview results; results={previews}")

        results = convert_folder(str(base))
        converted_count = sum(1 for result in results if result.status == "converted")
        if converted_count < 2:
            fail(f"expected at least 2 batch conversions; results={results}")

        if not wait_for_nfc_entry(base, BATCH_DIR_NFC, 5.0):
            fail(f"batch directory is not NFC; entries={entries(base)}")
        if entries(base / BATCH_DIR_NFC).get(BATCH_FILE_NFC) != "NFC":
            fail(f"batch file is not NFC; entries={entries(base / BATCH_DIR_NFC)}")

        print(f"PREVIEW_COUNT={preview_count}")
        print(f"BATCH_CONVERTED={converted_count}")
        print("RESULT=PASS")
    finally:
        if watcher is not None:
            watcher.stop()
        if created and not args.keep:
            safe_cleanup(base, drive_root)
        elif created:
            print(f"CLEANUP_SKIPPED_KEEP={base}")


if __name__ == "__main__":
    main()
