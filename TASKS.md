# B안: 인앱 업데이트 확인 — 작업 목록

GitHub Releases API로 최신 버전을 확인하고, 새 버전이 있으면 다운로드 안내만 한다.
자동 다운로드·설치는 범위 밖 (C안에서 검토).

## 결정 사항 (확정됨)

- [x] 확인 주기: 시작 시 + 7일 캐시
- [x] 알림 방식: 로그 + 상태바 + messagebox 1회 (다운로드는 승인 후 브라우저)
- [x] 수동 확인 버튼 위치: 버튼 행에 "업데이트 확인" 추가

## 구현

- [x] `version.py` 신규: `APP_VERSION`, `GITHUB_REPO` 상수 (Single Source of Truth)
- [x] `updater.py` 신규 (표준라이브러리만, 의존성 추가 없음)
  - [x] `fetch_latest_release()`: `urllib`로 `releases/latest` 조회 → `(tag, html_url)` 반환
  - [x] `is_newer(latest, current)`: `v` 접두 제거 후 숫자 튜플 비교
  - [x] 실패(오프라인·rate limit·파싱 오류) 시 예외 대신 `None` 반환, 호출側 로그만
- [x] `gui.py` 연동 (기존 스레드 패턴 준수: 백그라운드 스레드 → `_cmd_queue` → `after` 폴링)
  - [x] 시작 시 백그라운드 확인 + `last_update_check` 캐시 (`~/.korean_filename_fixer.json`)
  - [x] 새 버전 시 로그·상태바 표시 + 1회 안내 (`webbrowser.open`은 사용자 승인 후)
  - [x] 버튼 행에 "업데이트 확인" 수동 버튼
- [x] `tests/test_updater.py` 신규: 버전 비교, JSON 파싱(mock), 캐시 간격, 실패 무시
- [x] 기존 gui 테스트에 큐 디스패치 케이스 추가

## 검증·릴리스

- [x] `python -m pytest tests/` + `python -m unittest discover -s tests` (macOS/Windows CI 모두 통과해야 함)
- [x] `bash build.sh` + 앱 실행 확인 (UI 변경 규칙)
- [x] 버전 상수↔태그 일치 규칙을 AGENTS.md에 1줄 추가
- [ ] 기능 추가이므로 minor 버전 업 후 태그·푸시

## 범위 밖

- 자동 다운로드·설치·재시작 (C안, 서명·공증 선행 필요)
- Homebrew Cask (별도 검토)
