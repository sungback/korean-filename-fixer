"""
selfupdate.py
분리 도우미 기반 자동 교체 모듈 (Windows 배치 + macOS bash).

실행 중인 앱은 자신을 덮어쓸 수 없으므로, 도우미 스크립트를 만들어
앱 종료 후 교체한다. Windows는 폴더 단위(onedir), macOS는 .app 번들 단위다.
"""

import hashlib
import logging
import ntpath
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from updater import ssl_context

WINDOWS_ZIP_NAME = "KoreanFilenameFixer-Windows.zip"
WINDOWS_EXE_NAME = "KoreanFilenameFixer.exe"
UPDATE_BATCH_NAME = "kff_self_update.bat"

MACOS_ZIP_NAME = "KoreanFilenameFixer-macOS.zip"
MACOS_APP_SUFFIX = ".app"
MAC_UPDATE_SCRIPT_NAME = "kff_self_update.sh"

DOWNLOAD_TIMEOUT = 120.0
DOWNLOAD_CHUNK_SIZE = 65536
TEXT_TIMEOUT = 15.0


def release_asset_url(repo: str, tag: str, asset: str = WINDOWS_ZIP_NAME) -> str:
    """특정 태그의 에셋 다운로드 URL을 반환한다."""
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def latest_asset_url(repo: str, asset: str = WINDOWS_ZIP_NAME) -> str:
    """latest 태그의 에셋 다운로드 URL을 반환한다."""
    return f"https://github.com/{repo}/releases/latest/download/{asset}"


def checksum_url(asset_url: str) -> str:
    """에셋 URL에 대응하는 .sha256 URL을 반환한다."""
    return asset_url + ".sha256"


def download_update(url: str, dest_path: str, progress_callback=None,
                    timeout: float = DOWNLOAD_TIMEOUT) -> str:
    """URL에서 파일을 내려받아 저장한다. 진행률은 (받은바이트, 전체바이트|None)."""
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    downloaded = 0
    total = None
    with urllib.request.urlopen(request, timeout=timeout,
                                context=ssl_context()) as response:
        try:
            total = int(response.info().get("Content-Length") or 0) or None
        except (TypeError, ValueError):
            total = None
        with open(dest_path, "wb") as f:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback((downloaded, total))
    return dest_path


def fetch_text(url: str, timeout: float = TEXT_TIMEOUT) -> str | None:
    """짧은 텍스트(체크섬 파일 등)를 내려받는다. 실패 시 None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout,
                                    context=ssl_context()) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning(f"텍스트 다운로드 실패: {e}")
        return None


def parse_checksum(text: str) -> str:
    """`해시  파일명` 형식 첫 토큰을 반환한다. 빈 문자열이면 실패扱い."""
    if not text:
        return ""
    return text.strip().split()[0] if text.strip().split() else ""


def verify_sha256(file_path: str, expected_hex: str) -> bool:
    """파일의 SHA256이 기대값과 일치하면 True."""
    if not expected_hex:
        return False
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected_hex.strip().lower()


def current_install_dir() -> str | None:
    """PyInstaller 실행 파일이 있는 폴더를 반환한다. 개발 실행이면 None."""
    if not getattr(sys, "frozen", False):
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


def current_exe_path(install_dir: str) -> str:
    return os.path.join(install_dir, WINDOWS_EXE_NAME)


def is_writable_dir(path: str) -> bool:
    return bool(path and os.path.isdir(path) and os.access(path, os.W_OK))


def extract_update(zip_path: str, staging_dir: str) -> str:
    """zip을 풀고 exe가 들어있는 최상위 폴더를 반환한다."""
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(staging_dir)
    for root, _dirs, files in os.walk(staging_dir):
        if WINDOWS_EXE_NAME in files:
            return root
    raise FileNotFoundError(f"업데이트 압축본에 {WINDOWS_EXE_NAME} 없음: {zip_path}")


def staging_dir() -> str:
    return tempfile.mkdtemp(prefix="KFF_update_")


def cleanup_staging(path: str):
    shutil.rmtree(path, ignore_errors=True)


def write_update_batch(batch_path: str, pid: int, current_dir: str,
                       new_dir: str, exe_path: str,
                       cleanup_paths: list[str] | None = None) -> str:
    """종료 대기→.bak 회전→스왑→재실행→자기 삭제 배치를 생성한다.

    cleanup_paths는 교체 성공 후 함께 지울 임시 파일/폴더(zip, 스테이징 등)다.
    """
    cleanup_lines = ""
    for path in cleanup_paths or []:
        if os.path.isdir(path):
            cleanup_lines += f'rmdir /s /q "{path}" 2>nul\n'
        else:
            cleanup_lines += f'del /f /q "{path}" 2>nul\n'
    log_path = batch_path + ".log"
    wait_path = batch_path + ".tasks"
    lines = [
        "@echo off",
        "setlocal",
        f'set "KFF_PID={pid}"',
        f'set "KFF_IMAGE={ntpath.basename(exe_path)}"',
        f'set "KFF_CURRENT={current_dir}"',
        f'set "KFF_NEW={new_dir}"',
        f'set "KFF_EXE={exe_path}"',
        'set "KFF_BAK=%KFF_CURRENT%.bak"',
        'set "KFF_TRIES=0"',
        f'set "KFF_LOG={log_path}"',
        f'set "KFF_WAIT={wait_path}"',
        "",
        'echo [%DATE% %TIME%] self-update start pid=%KFF_PID% image=%KFF_IMAGE% > "%KFF_LOG%"',
        ":waitloop",
        "rem PID는 재사용될 수 있어 이미지명까지 함께 확인한다.",
        "rem find가 파일을 읽게 해 파이프 끊김時の 키보드 대기를 구조적으로 방지한다.",
        'tasklist /FI "PID eq %KFF_PID%" /FI "IMAGENAME eq %KFF_IMAGE%" > "%KFF_WAIT%" 2>nul',
        'find "%KFF_PID%" "%KFF_WAIT%" >nul 2>&1',
        "if errorlevel 1 goto swapwait",
        "set /a KFF_TRIES+=1",
        "rem 최대 3분 대기 후에는 진행한다. 잠겨 있으면 move 실패→롤백된다.",
        "if %KFF_TRIES% GEQ 180 goto waitcap",
        "timeout /t 1 /nobreak >nul",
        "goto waitloop",
        "",
        ":swapwait",
        'del /f /q "%KFF_WAIT%" 2>nul',
        "goto swap",
        "",
        ":waitcap",
        'echo [%DATE% %TIME%] wait cap reached, tasklist follows >> "%KFF_LOG%"',
        'type "%KFF_WAIT%" >> "%KFF_LOG%" 2>&1',
        'del /f /q "%KFF_WAIT%" 2>nul',
        "goto swap",
        "",
        ":swap",
        'echo [%DATE% %TIME%] swap start >> "%KFF_LOG%"',
        'if exist "%KFF_BAK%" rmdir /s /q "%KFF_BAK%"',
        'move "%KFF_CURRENT%" "%KFF_BAK%" >nul',
        "if errorlevel 1 goto rollback",
        'move "%KFF_NEW%" "%KFF_CURRENT%" >nul',
        "if errorlevel 1 goto rollback",
        'start "" "%KFF_EXE%"',
        'rmdir /s /q "%KFF_NEW%" 2>nul',
        cleanup_lines.rstrip("\n"),
        'echo [%DATE% %TIME%] done >> "%KFF_LOG%"',
        'del "%~f0"',
        "exit /b 0",
        "",
        ":rollback",
        'echo [%DATE% %TIME%] rollback >> "%KFF_LOG%"',
        'if exist "%KFF_CURRENT%" rmdir /s /q "%KFF_CURRENT%" 2>nul',
        'if exist "%KFF_BAK%" move "%KFF_BAK%" "%KFF_CURRENT%" >nul',
        'del "%~f0"',
        "exit /b 1",
    ]
    content = "\r\n".join(lines) + "\r\n"
    parent = os.path.dirname(batch_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(batch_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(content)
    return batch_path


# ─── macOS (.app 번들) ────────────────────────────────────────

def current_macos_app() -> str | None:
    """실행 중인 .app 번들 경로를 반환한다. 개발 실행이면 None."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    dirpath = os.path.dirname(os.path.abspath(sys.executable))
    while dirpath and dirpath != os.path.dirname(dirpath):
        if dirpath.endswith(MACOS_APP_SUFFIX) and os.path.isdir(dirpath):
            return dirpath
        dirpath = os.path.dirname(dirpath)
    return None


def extract_mac_app(zip_path: str, staging_dir: str) -> str:
    """zip을 풀고 .app 번들 경로를 반환한다 (가장 얕은 것 우선)."""
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(staging_dir)
    candidates = []
    for root, dirs, _files in os.walk(staging_dir):
        for dirname in dirs:
            if dirname.endswith(MACOS_APP_SUFFIX):
                candidates.append(os.path.join(root, dirname))
    if not candidates:
        raise FileNotFoundError(f"업데이트 압축본에 .app 없음: {zip_path}")
    candidates.sort(key=lambda p: p.count(os.sep))
    return candidates[0]


def verify_bundle(app_path: str) -> bool:
    """codesign strict 검증을 통과하면 True. 실패해도 호출側이 판단한다."""
    try:
        result = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", app_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        logging.warning(f"번들 검증 실패: {e}")
        return False


def write_mac_update_script(script_path: str, pid: int, current_app: str,
                            new_app: str,
                            cleanup_paths: list[str] | None = None) -> str:
    """종료 대기→.bak 회전→스왑→quarantine 제거→reopen bash 스크립트를 생성한다."""
    q = shlex.quote
    cleanup_lines = "\n".join(
        f'rm -rf {q(p)} 2>/dev/null' for p in cleanup_paths or []
    )
    log_path = script_path + ".log"
    lines = [
        "#!/bin/bash",
        f'KFF_PID={pid}',
        f'KFF_CURRENT={q(current_app)}',
        f'KFF_NEW={q(new_app)}',
        f'KFF_LOG={q(log_path)}',
        "",
        'echo "$(date) self-update start pid=$KFF_PID" > "$KFF_LOG"',
        'while kill -0 "$KFF_PID" 2>/dev/null; do sleep 0.2; done',
        "sleep 0.5",
        'echo "$(date) swap start" >> "$KFF_LOG"',
        'KFF_BAK="${KFF_CURRENT}.bak"',
        'rm -rf "$KFF_BAK"',
        'mv "$KFF_CURRENT" "$KFF_BAK" || { echo "move-aside failed" >> "$KFF_LOG"; exit 1; }',
        'mv "$KFF_NEW" "$KFF_CURRENT" || { echo "move-in failed, restoring" >> "$KFF_LOG"; mv "$KFF_BAK" "$KFF_CURRENT"; exit 1; }',
        "xattr -dr com.apple.quarantine \"$KFF_CURRENT\" 2>/dev/null || true",
        'open "$KFF_CURRENT"',
        cleanup_lines,
        'echo "$(date) done" >> "$KFF_LOG"',
        'rm -f "$0"',
    ]
    content = "\n".join(line for line in lines if line != "") + "\n"
    parent = os.path.dirname(script_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(script_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path
