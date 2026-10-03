"""
updater.py
GitHub Releases API 기반 새 버전 확인 모듈 (B안: 확인 + 다운로드 안내).

자동 다운로드·설치는 하지 않는다. 새 버전이 있으면 GUI가 팝업으로 알리고,
사용자 승인 후에만 브라우저로 릴리스 페이지를 연다.
표준라이브러리만 사용한다 (urllib).
"""

import json
import time
import urllib.request

LATEST_RELEASE_URL = "https://api.github.com/repos/{repo}/releases/latest"

# 자동 확인 최소 간격 (기본 7일). 마지막 확인 시각은 config에 저장한다.
UPDATE_CHECK_INTERVAL = 7 * 24 * 60 * 60

# API 요청 타임아웃 (초). 시작 시 블로킹을 막기 위해 짧게 둔다.
UPDATE_CHECK_TIMEOUT = 10.0


def parse_version(tag: str) -> tuple[int, ...]:
    """`v1.13.1` 같은 태그를 숫자 튜플로 바꾼다. 파싱 실패 시 빈 튜플."""
    cleaned = (tag or "").strip().lstrip("vV")
    parts: list[int] = []
    for piece in cleaned.split("."):
        if not piece.isdigit():
            return ()
        parts.append(int(piece))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """latest가 current보다 새 버전이면 True. 파싱 실패 시 False."""
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    if not latest_parts or not current_parts:
        return False
    return latest_parts > current_parts


def should_check(last_check: float, now: float | None = None,
                 interval: float = UPDATE_CHECK_INTERVAL) -> bool:
    """마지막 확인 후 interval이 지났으면 True."""
    if now is None:
        now = time.time()
    return (now - (last_check or 0)) >= interval


def fetch_latest_release(repo: str,
                         timeout: float = UPDATE_CHECK_TIMEOUT
                         ) -> tuple[str, str] | None:
    """최신 릴리스의 (tag, html_url)을 반환한다. 실패 시 None.

    오프라인·rate limit·파싱 오류 모두 None으로 흡수한다.
    호출側은 조용히 건너뛰고, 수동 확인일 때만 실패를 알린다.
    """
    url = LATEST_RELEASE_URL.format(repo=repo)
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        tag = data.get("tag_name", "")
        html_url = data.get("html_url", "")
        if not tag:
            return None
        return (tag, html_url)
    except Exception:
        return None
