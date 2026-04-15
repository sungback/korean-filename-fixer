"""
autostart.py
macOS/Windows 로그인 시 자동 시작 관리 모듈
"""

import os
import plistlib
import sys

try:
    import winreg
except ImportError:  # pragma: no cover - Windows 외 플랫폼
    winreg = None


LAUNCH_AGENT_LABEL = "com.sungback.koreanfilenamefixer"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE_NAME = "KoreanFilenameFixer"


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


def get_windows_executable_path(executable_path: str | None = None) -> str | None:
    """PyInstaller .exe 실행 파일 경로면 절대 경로를, 아니면 None을 반환한다."""
    candidate = os.path.abspath(executable_path or sys.executable)
    if candidate.lower().endswith(".exe") and os.path.exists(candidate):
        return candidate
    return None


def get_autostart_executable_path(executable_path: str | None = None) -> str | None:
    """현재 플랫폼에서 로그인 시 자동 시작에 쓸 실행 파일 경로를 반환한다."""
    if sys.platform == "darwin":
        return get_bundle_executable_path(executable_path)
    if sys.platform == "win32":
        return get_windows_executable_path(executable_path)
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


def _build_windows_run_command(executable_path: str) -> str:
    executable = os.path.abspath(executable_path)
    return f'"{executable}"'


def _parse_windows_run_command(command: str) -> str | None:
    command = (command or "").strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        if end == -1:
            return None
        return os.path.abspath(command[1:end])
    executable, _, _rest = command.partition(" ")
    return os.path.abspath(executable) if executable else None


def _get_windows_registered_executable(
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
) -> str | None:
    """Run 레지스트리에 등록된 실행 파일 경로를 반환한다."""
    if winreg is None:
        return None

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path)
    except FileNotFoundError:
        return None

    try:
        command, _value_type = winreg.QueryValueEx(key, value_name)
        return _parse_windows_run_command(command)
    except FileNotFoundError:
        return None
    finally:
        winreg.CloseKey(key)


def get_registered_executable(
    plist_path: str | None = None,
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
) -> str | None:
    """현재 플랫폼의 자동 시작 등록 실행 파일 경로를 반환한다."""
    if sys.platform == "darwin":
        path = plist_path or get_launch_agent_path()
        if not os.path.exists(path):
            return None

        with open(path, "rb") as f:
            data = plistlib.load(f)

        args = data.get("ProgramArguments") or []
        if not args:
            return None
        return os.path.abspath(args[0])

    if sys.platform == "win32":
        return _get_windows_registered_executable(run_key_path, value_name)

    return None


def is_autostart_enabled(
    executable_path: str | None = None,
    plist_path: str | None = None,
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
) -> bool:
    """현재 플랫폼의 자동 시작이 등록되어 있으면 True를 반환한다."""
    registered = get_registered_executable(plist_path, run_key_path, value_name)
    if not registered:
        return False
    if executable_path is None:
        return True
    return registered == os.path.abspath(executable_path)


def needs_autostart_refresh(
    executable_path: str,
    plist_path: str | None = None,
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
) -> bool:
    """현재 앱 경로와 자동 시작 등록 경로가 다르면 True를 반환한다."""
    registered = get_registered_executable(plist_path, run_key_path, value_name)
    if not registered:
        return False
    return registered != os.path.abspath(executable_path)


def enable_autostart(
    executable_path: str | None = None,
    plist_path: str | None = None,
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
) -> str:
    """현재 플랫폼의 로그인 시 자동 시작 등록을 생성한다."""
    autostart_executable = get_autostart_executable_path(executable_path)
    if not autostart_executable:
        raise ValueError("로그인 시 자동 시작은 지원되는 배포 실행 파일에서만 사용할 수 있습니다.")

    if sys.platform == "darwin":
        path = plist_path or get_launch_agent_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump(build_launch_agent_plist(autostart_executable), f, sort_keys=False)
        return path

    if sys.platform == "win32":
        if winreg is None:  # pragma: no cover - Windows 외 플랫폼
            raise RuntimeError("Windows 레지스트리를 사용할 수 없습니다.")

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key_path)
        try:
            winreg.SetValueEx(
                key,
                value_name,
                0,
                winreg.REG_SZ,
                _build_windows_run_command(autostart_executable),
            )
        finally:
            winreg.CloseKey(key)
        return autostart_executable

    raise ValueError("현재 플랫폼에서는 로그인 시 자동 시작을 지원하지 않습니다.")


def disable_autostart(
    plist_path: str | None = None,
    run_key_path: str = WINDOWS_RUN_KEY,
    value_name: str = WINDOWS_RUN_VALUE_NAME,
):
    """현재 플랫폼의 로그인 시 자동 시작 등록을 제거한다."""
    if sys.platform == "darwin":
        path = plist_path or get_launch_agent_path()
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    if sys.platform == "win32":
        if winreg is None:  # pragma: no cover - Windows 외 플랫폼
            return
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                run_key_path,
                0,
                winreg.KEY_SET_VALUE,
            )
        except FileNotFoundError:
            return

        try:
            winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
