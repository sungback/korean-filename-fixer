"""
converter.py
NFD(macOS) → NFC(Windows 호환) 파일명 변환 모듈

macOS는 한글 파일명을 NFD(자모 분해)로 저장하고, Windows/Linux는 NFC(완성형)를 사용한다.
이 모듈은 NFD 파일명을 NFC로 바꿔 크로스 플랫폼 호환성을 확보한다.
"""

import logging
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass
from fnmatch import fnmatchcase


@dataclass
class ConvertResult:
    path: str       # 변환 후 실제 경로 (실패 시 원본 경로)
    original: str   # 변환 전 파일명
    converted: str  # 변환 후 파일명
    status: str     # "converted" | "preview" | "conflict" | "skipped" | "error"
    error: str = ""


_IGNORED_TEMP_NAME_RE = re.compile(r"\.sb-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$")
DEFAULT_EXCLUDE_PATTERNS = (
    ".git", "node_modules", "venv", ".venv", "__pycache__", 
    "build", "dist", ".idea", ".vscode"
)


def is_nfd(name: str) -> bool:
    """NFD는 NFC보다 코드포인트가 많으므로 NFC 변환 후 길이 변화로 판단한다."""
    return len(name) != len(unicodedata.normalize('NFC', name))


def should_ignore_name(name: str) -> bool:
    """저장 중 생성되는 임시 파일명은 변환 대상에서 제외한다."""
    if name.startswith("__nfc_tmp_") and name.endswith("__"):
        return True
    return _IGNORED_TEMP_NAME_RE.search(name) is not None


def clean_exclude_patterns(patterns) -> list[str]:
    """빈 값과 중복을 제거한 제외 패턴 목록을 반환한다."""
    cleaned = []
    seen = set()
    for pattern in patterns or []:
        if pattern is None:
            continue
        value = str(pattern).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def should_exclude_path(path: str, exclude_patterns, is_directory: bool | None = None) -> bool:
    """경로 중 포함된 디렉터리명이 제외 패턴과 일치하면 True를 반환한다."""
    patterns = clean_exclude_patterns(exclude_patterns)
    if not patterns:
        return False

    normalized = os.path.normpath(path)
    parts = [part for part in normalized.split(os.sep) if part and part != os.curdir]
    if not parts:
        return False

    if is_directory is None:
        is_directory = os.path.isdir(path)
    dir_parts = parts if is_directory else parts[:-1]

    for part in dir_parts:
        if any(fnmatchcase(part, pattern) for pattern in patterns):
            return True
    return False


def _build_jamo_map() -> dict[int, int]:
    """초성·중성·종성 Hangul Jamo(U+1100~) → Compatibility Jamo(U+3131~) 매핑을 반환한다.

    macOS NFD는 Hangul Jamo 영역 코드포인트를 사용하는데, 폰트가 이를 합쳐
    렌더링해 눈에 보이지 않는다. Compatibility Jamo로 바꾸면 분리된 자모로 표시된다.
    """
    # 초성 19자 (U+1100~U+1112)
    cho = [0x3131, 0x3132, 0x3134, 0x3137, 0x3138, 0x3139, 0x3141, 0x3142, 0x3143,
           0x3145, 0x3146, 0x3147, 0x3148, 0x3149, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E]
    # 종성 27자 (U+11A8~U+11C2)
    jong = [0x3131, 0x3132, 0x3133, 0x3134, 0x3135, 0x3136, 0x3137, 0x3139, 0x313A,
            0x313B, 0x313C, 0x313D, 0x313E, 0x313F, 0x3140, 0x3141, 0x3142, 0x3144,
            0x3145, 0x3146, 0x3147, 0x3148, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E]

    mapping: dict[int, int] = {}
    for i, c in enumerate(cho):
        mapping[0x1100 + i] = c           # 초성
    for i in range(21):
        mapping[0x1161 + i] = 0x314F + i  # 중성 21자 (U+1161~U+1175)
    for i, c in enumerate(jong):
        mapping[0x11A8 + i] = c           # 종성
    return mapping


_JAMO_TO_COMPAT = _build_jamo_map()


def nfd_to_visual(name: str) -> str:
    """NFD 자모를 화면에 분리되어 보이는 호환 자모로 변환한다.

    macOS는 Hangul Jamo(U+1100~U+11FF)를 폰트 수준에서 합쳐 렌더링하므로
    NFD 파일명이 합쳐진 한글처럼 보인다. Compatibility Jamo(U+3131~U+3163)로
    바꾸면 macOS에서도 ㅎㅏㄴ처럼 분리된 형태로 표시된다.
    """
    return ''.join(chr(_JAMO_TO_COMPAT.get(ord(c), ord(c))) for c in name)


def _rename_dir(src: str, tmp: str, dst: str):
    """폴더를 NFD→NFC로 rename한다.

    macOS HFS+는 NFD↔NFC를 동일하게 취급하므로 임시 이름을 중간에 거쳐
    파일시스템이 두 이름을 별개로 인식하도록 강제한다.
    """
    os.rename(src, tmp)
    os.rename(tmp, dst)


def _rename_symlink(src: str, tmp: str, dst: str):
    """심볼릭 링크 자체를 NFD→NFC로 rename한다."""
    os.rename(src, tmp)
    os.rename(tmp, dst)


def _rename_file(src: str, tmp: str, dst: str):
    """파일을 NFD→NFC로 rename한다.

    copy2 → 원본 삭제 → rename 순서로 처리하면 Google Drive가
    삭제(NFD) + 생성(NFC) 이벤트로 인식해 서버에도 NFC 이름이 반영된다.
    """
    shutil.copy2(src, tmp)
    os.remove(src)
    os.rename(tmp, dst)


def _path_exists(path: str) -> bool:
    """깨진 symlink까지 존재하는 경로로 취급한다."""
    return os.path.exists(path) or os.path.islink(path)


def _remove_path(path: str):
    if os.path.islink(path) or not os.path.isdir(path):
        os.remove(path)
    else:
        shutil.rmtree(path)


def _rollback_tmp(src: str, tmp: str):
    """실패한 변환 시 임시 경로를 원상 복구하거나 정리한다."""
    if not _path_exists(tmp):
        return
    if _path_exists(src):
        _remove_path(tmp)
    else:
        os.rename(tmp, src)


def _find_conflicting_entry(filepath: str, converted_name: str) -> str:
    """같은 디렉터리에 목표 NFC 이름과 충돌하는 다른 엔트리가 있으면 그 이름을 반환한다."""
    dirpath = os.path.dirname(filepath)
    original_name = os.path.basename(filepath)
    try:
        with os.scandir(dirpath) as entries:
            for entry in entries:
                if entry.name == original_name:
                    continue
                if unicodedata.normalize('NFC', entry.name) == converted_name:
                    return entry.name
    except OSError:
        pass
    return ""


def plan_file(filepath: str) -> ConvertResult:
    """파일/폴더 1개의 변환 계획만 계산한다. 실제 파일 변경은 하지 않는다."""
    dirpath = os.path.dirname(filepath)
    name = os.path.basename(filepath)

    if should_ignore_name(name) or not is_nfd(name):
        return ConvertResult(filepath, name, name, "skipped")

    nfc_name = unicodedata.normalize('NFC', name)
    new_path = os.path.join(dirpath, nfc_name)
    conflict_name = _find_conflicting_entry(filepath, nfc_name)
    if conflict_name:
        return ConvertResult(
            filepath,
            name,
            nfc_name,
            "conflict",
            f"{conflict_name} 이미 존재",
        )
    return ConvertResult(new_path, name, nfc_name, "preview")


def convert_file(filepath: str, retry: int = 5, retry_interval: float = 1.0) -> ConvertResult:
    """파일/폴더 1개를 NFD → NFC로 변환한다. 이미 NFC면 'skipped'를 반환한다."""
    plan = plan_file(filepath)
    dirpath = os.path.dirname(filepath)
    name = plan.original
    nfc_name = plan.converted
    new_path = plan.path

    if plan.status == "skipped":
        return plan
    if plan.status == "conflict":
        logging.warning(f"Conflict converting {filepath}: {plan.error}")
        return plan

    # UUID로 tmp 경로를 고유하게 만들어 동시 변환 시 충돌을 방지한다
    tmp_path = os.path.join(dirpath, f"__nfc_tmp_{uuid.uuid4().hex[:8]}__")

    for attempt in range(retry):
        try:
            if os.path.islink(filepath):
                _rename_symlink(filepath, tmp_path, new_path)
            elif os.path.isdir(filepath):
                _rename_dir(filepath, tmp_path, new_path)
            else:
                _rename_file(filepath, tmp_path, new_path)

            logging.info(f"Converted: {name!r} → {nfc_name!r}")
            return ConvertResult(new_path, name, nfc_name, "converted")

        except PermissionError:
            try:
                _rollback_tmp(filepath, tmp_path)
            except Exception:
                pass
            logging.warning(f"File locked, retrying ({attempt + 1}/{retry}): {filepath}")
            time.sleep(retry_interval)

        except Exception as e:
            try:
                _rollback_tmp(filepath, tmp_path)
            except Exception:
                pass
            logging.error(f"Error converting {filepath}: {e}")
            return ConvertResult(filepath, name, nfc_name, "error", str(e))

    msg = f"{retry}회 재시도 후 실패"
    logging.error(f"Failed to convert {filepath}: {msg}")
    return ConvertResult(filepath, name, nfc_name, "error", msg)


def _collect_entries(folder: str, exclude_patterns=None, include_root: bool = False) -> list[str]:
    """제외 패턴을 적용해 변환 후보 경로를 수집한다."""
    exclude_patterns = clean_exclude_patterns(exclude_patterns)
    if should_exclude_path(folder, exclude_patterns, is_directory=True):
        logging.info(f"Skipped excluded folder: {folder}")
        return []

    all_entries = []
    for root, dirs, files in os.walk(folder):
        # 제외 디렉터리는 하위 탐색 자체를 막아 이벤트/변환 비용을 줄인다.
        dirs[:] = [
            name for name in dirs
            if not should_exclude_path(
                os.path.join(root, name), exclude_patterns, is_directory=True
            )
        ]
        for name in files:
            path = os.path.join(root, name)
            if should_exclude_path(path, exclude_patterns, is_directory=False):
                continue
            all_entries.append(path)
        for name in dirs:
            all_entries.append(os.path.join(root, name))

    if include_root:
        all_entries.append(folder)

    # 깊은 경로(구분자 수가 많은 것)를 먼저 처리
    all_entries.sort(key=lambda p: p.count(os.sep), reverse=True)
    return all_entries


def preview_folder(
    folder: str,
    exclude_patterns=None,
    include_root: bool = False,
) -> list[ConvertResult]:
    """폴더 하위의 모든 파일/폴더명을 스캔해 변환 예정 목록만 계산한다."""
    results = []
    for entry in _collect_entries(folder, exclude_patterns, include_root=include_root):
        if not _path_exists(entry):
            continue
        if should_ignore_name(os.path.basename(entry)):
            continue
        results.append(plan_file(entry))
    return results


def convert_folder(
    folder: str,
    exclude_patterns=None,
    include_root: bool = False,
) -> list[ConvertResult]:
    """폴더 하위의 모든 파일/폴더명을 NFD → NFC로 변환한다.

    깊은 경로부터 처리해 상위 폴더 rename 시 하위 경로가 무효화되는 것을 방지한다.
    """
    all_entries = _collect_entries(folder, exclude_patterns, include_root=include_root)

    results = []
    for entry in all_entries:
        if not _path_exists(entry):
            continue
        if should_ignore_name(os.path.basename(entry)):
            continue
        if should_exclude_path(entry, exclude_patterns):
            continue
        results.append(convert_file(entry))

    return results
