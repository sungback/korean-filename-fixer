"""
selfupdate.py
Windows 배치 도우미 기반 자동 교체 모듈 (C-1안).

실행 중인 exe는 Windows 파일 잠금 때문에 자신을 덮어쓸 수 없으므로,
배치 스크립트를 %TEMP%에 생성해 앱 종료 후 폴더 단위(onedir 전체) 교체를 수행한다.
macOS는 대상이 아니다 (브라우저 다운로드 유지).
"""

import hashlib
import logging
import ntpath
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

from updater import ssl_context

WINDOWS_ZIP_NAME = "KoreanFilenameFixer-Windows.zip"
WINDOWS_EXE_NAME = "KoreanFilenameFixer.exe"
UPDATE_BATCH_NAME = "kff_self_update.bat"

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
    content = (
        "@echo off\n"
        "setlocal\n"
        f'set "KFF_PID={pid}"\n'
        f'set "KFF_IMAGE={ntpath.basename(exe_path)}"\n'
        f'set "KFF_CURRENT={current_dir}"\n'
        f'set "KFF_NEW={new_dir}"\n'
        f'set "KFF_EXE={exe_path}"\n'
        'set "KFF_BAK=%KFF_CURRENT%.bak"\n'
        'set "KFF_TRIES=0"\n'
        "\n"
        ":waitloop\n"
        "rem PID는 재사용될 수 있어 이미지명까지 함께 확인한다.\n"
        'tasklist /FI "PID eq %KFF_PID%" /FI "IMAGENAME eq %KFF_IMAGE%" 2>nul | find "%KFF_PID%" >nul\n'
        "if errorlevel 1 goto swap\n"
        "set /a KFF_TRIES+=1\n"
        "rem 최대 3분 대기 후에는 진행한다. 잠겨 있으면 move 실패→롤백된다.\n"
        "if %KFF_TRIES% GEQ 180 goto swap\n"
        "timeout /t 1 /nobreak >nul\n"
        "goto waitloop\n"
        "\n"
        ":swap\n"
        'if exist "%KFF_BAK%" rmdir /s /q "%KFF_BAK%"\n'
        'move "%KFF_CURRENT%" "%KFF_BAK%" >nul\n'
        "if errorlevel 1 goto rollback\n"
        'move "%KFF_NEW%" "%KFF_CURRENT%" >nul\n'
        "if errorlevel 1 goto rollback\n"
        'start "" "%KFF_EXE%"\n'
        'rmdir /s /q "%KFF_NEW%" 2>nul\n'
        f"{cleanup_lines}"
        'del "%~f0"\n'
        "exit /b 0\n"
        "\n"
        ":rollback\n"
        'if exist "%KFF_CURRENT%" rmdir /s /q "%KFF_CURRENT%" 2>nul\n'
        'if exist "%KFF_BAK%" move "%KFF_BAK%" "%KFF_CURRENT%" >nul\n'
        'del "%~f0"\n'
        "exit /b 1\n"
    )
    parent = os.path.dirname(batch_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(batch_path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(content)
    return batch_path
