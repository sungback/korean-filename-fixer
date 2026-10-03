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

---

# C-1안: Windows 배치 도우미 자동 교체 — 작업 목록

실행 중 잠금 때문에 앱 본체가 아닌 `%TEMP%` 배치 스크립트가 종료 후 교체한다.
교체 단위는 exe 1개가 아니라 onedir 폴더(`KoreanFilenameFixer/`) 전체다. Windows 전용.

## 전제·제약

- 설치 위치가 사용자 쓰기 가능해야 함 (Program Files면 사전 체크에서 실패 안내)
- 백신 오탐 가능성 감수 (자기 교체+배치는 흔한 휴리스틱 대상)
- macOS는 기존 브라우저 다운로드 유지 (범위 밖)

## 구현

- [x] 워크플로(`build.yml`): 릴리스 에셋에 `.sha256` 추가, zip 파일명 고정 유지
- [x] `selfupdate.py` 신규 (표준라이브러리만)
  - [x] 에셋 URL 해결: `releases/latest/download/KoreanFilenameFixer-Windows.zip`
  - [x] `download_update()`: `urllib` + 진행률 콜백(상태바 표시용)
  - [x] `verify_sha256()`: `.sha256` 대조, 불일치 시 설치 중단·임시파일 삭제
  - [x] `write_update_batch()`: 종료 대기→`.bak` 회전→스왑→재실행→자기 삭제 배치 생성
  - [x] 교체 실패 시 `.bak` 복원 로직 포함
- [x] `gui.py` 연동 (워커→`_cmd_queue`→`after` 패턴 준수)
  - [x] 업데이트 팝업에 "다운로드 후 설치" 선택지 추가 (기존 "페이지 열기" 유지)
  - [x] 다운로드 진행률 상태바 표시, 완료 후 최종 확인 → 배치 실행 → 앱 종료
  - [x] 설치 위치 쓰기 가능 사전 체크, 불가 시 안내만
- [x] `tests/test_selfupdate.py` 신규: 배치 내용 단언, sha 검증, URL 규칙 (OS 독립적)
- [x] Windows CI에서 unittest 통과 확인 (배치 실행 테스트는 안 함, 내용 단언만)

## 검증·릴리스

- [x] `pytest` + `unittest discover` (양 OS)
- [ ] Windows 실머신에서 구버전→신버전 교체 1회 수동 검증 (CI 불가 영역)
- [x] 기능 추가이므로 minor 버전 업 후 태그·푸시 (v1.15.0)

## 범위 밖

- macOS 자동 교체, 델타 업데이트, `--onefile` 전환 (별도 검토)
