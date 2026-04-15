"""
autostart.py
macOS LaunchAgent 기반 로그인 시 자동 시작 관리 모듈
"""

import os
import plistlib
import sys


LAUNCH_AGENT_LABEL = "com.sungback.koreanfilenamefixer"


def get_launch_agent_path(home_dir: str | None = None, label: str = LAUNCH_AGENT_LABEL) -> str:
    """현재 사용자 LaunchAgents 경로의 plist 파일 위치를 반환한다."""
    base_dir = home_dir or os.path.expanduser("~")
    return os.path.join(base_dir, "Library", "LaunchAgents", f"{label}.plist")


def get_bundle_executable_path(executable_path: str | None = None) -> str | None:
    """PyInstaller .app 내부 실행 파일 경로면 절대 경로를, 아니면 None을 반환한다."""
    candidate = os.path.abspath(executable_path or sys.executable)
    macos_dir = os.path.dirname(candidate)
    contents_dir = os.path.dirname(macos_dir)
    app_dir = os.path.dirname(contents_dir)

    if (
        os.path.basename(macos_dir) == "MacOS"
        and os.path.basename(contents_dir) == "Contents"
        and os.path.basename(app_dir).endswith(".app")
        and os.path.exists(candidate)
    ):
        return candidate
    return None


def build_launch_agent_plist(
    executable_path: str,
    label: str = LAUNCH_AGENT_LABEL,
) -> dict:
    """LaunchAgent plist 내용을 생성한다."""
    executable = os.path.abspath(executable_path)
    return {
        "Label": label,
        "ProgramArguments": [executable],
        "RunAtLoad": True,
        "KeepAlive": False,
        "WorkingDirectory": os.path.dirname(executable),
    }


def get_registered_executable(plist_path: str | None = None) -> str | None:
    """기존 LaunchAgent가 등록한 실행 파일 경로를 반환한다."""
    path = plist_path or get_launch_agent_path()
    if not os.path.exists(path):
        return None

    with open(path, "rb") as f:
        data = plistlib.load(f)

    args = data.get("ProgramArguments") or []
    if not args:
        return None
    return os.path.abspath(args[0])


def is_autostart_enabled(
    executable_path: str | None = None,
    plist_path: str | None = None,
) -> bool:
    """현재 LaunchAgent가 등록되어 있으면 True를 반환한다."""
    registered = get_registered_executable(plist_path)
    if not registered:
        return False
    if executable_path is None:
        return True
    return registered == os.path.abspath(executable_path)


def needs_autostart_refresh(executable_path: str, plist_path: str | None = None) -> bool:
    """현재 앱 경로와 등록된 LaunchAgent 경로가 다르면 True를 반환한다."""
    registered = get_registered_executable(plist_path)
    if not registered:
        return False
    return registered != os.path.abspath(executable_path)


def enable_autostart(
    executable_path: str | None = None,
    plist_path: str | None = None,
) -> str:
    """현재 앱 실행 파일 경로로 LaunchAgent plist를 생성한다."""
    bundle_executable = get_bundle_executable_path(executable_path)
    if not bundle_executable:
        raise ValueError("로그인 시 자동 시작은 .app 실행에서만 지원됩니다.")

    path = plist_path or get_launch_agent_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_launch_agent_plist(bundle_executable), f, sort_keys=False)
    return path


def disable_autostart(plist_path: str | None = None):
    """LaunchAgent plist를 제거한다."""
    path = plist_path or get_launch_agent_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
