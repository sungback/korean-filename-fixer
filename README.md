# Korean Filename Fixer

macOS에서 생성된 한글 파일명의 유니코드 정규화 형식을 **NFD → NFC**로 자동 변환하는 GUI 앱입니다.

## 배경

macOS는 한글 파일명을 **NFD(Normalization Form D)** 방식으로 저장합니다.
Windows나 Linux는 **NFC(Normalization Form C)** 를 사용하기 때문에, macOS에서 만든 파일을 다른 OS로 옮기면 파일명이 깨지거나 인식되지 않는 문제가 발생합니다.
이 앱은 해당 문제를 자동으로 해결합니다.

## 기능

| 기능 | 설명 |
|------|------|
| **한 번 변환** | 선택한 폴더 전체를 즉시 일괄 변환 |
| **실시간 감시** | 폴더를 감시하다가 NFD 파일이 생성/이동되면 자동 변환 |
| **재귀 처리** | 하위 폴더까지 모두 탐색, 깊은 경로부터 처리해 rename 충돌 방지 |
| **변환 로그** | 변환 성공·오류·건너뜀 결과를 컬러 로그로 표시 |

## 프로젝트 구조

```
korean-filename-fixer/
├── main.py          # 진입점 — GUI 실행
├── gui.py           # tkinter 기반 GUI (App 클래스)
├── converter.py     # NFD→NFC 변환 로직 (ConvertResult, convert_folder 등)
├── watcher.py       # watchdog 기반 실시간 폴더 감시 (FolderWatcher)
├── build.sh         # PyInstaller macOS .app 빌드 스크립트
├── requirements.txt # 의존성 목록
└── KoreanFilenameFixer.spec  # PyInstaller 스펙 파일
```

## 설치 및 실행

### 요구사항

- Python 3.10+
- macOS (NFD 파일명 문제가 발생하는 환경)

### 의존성 설치

```bash
pip install -r requirements.txt
```

### 실행

```bash
python main.py
```

## macOS 앱(.app)으로 빌드

```bash
bash build.sh
```

빌드가 완료되면 `dist/KoreanFilenameFixer` 실행 파일이 생성됩니다.

> **첫 실행 시 Gatekeeper 경고가 뜨는 경우:**
> 시스템 설정 → 개인 정보 보호 및 보안 → '확인 없이 열기' 클릭
> 또는 터미널에서: `xattr -cr dist/KoreanFilenameFixer.app`

## 사용 방법

1. 앱 실행 후 **[선택]** 버튼으로 대상 폴더를 지정합니다.
2. **[한 번 변환]** — 폴더 전체를 즉시 스캔하여 NFD 파일명을 NFC로 변환합니다.
3. **[▶ 감시 시작]** — 백그라운드에서 폴더를 감시하며, 새 파일이 추가될 때마다 자동 변환합니다.
4. **[■ 중지]** — 실시간 감시를 종료합니다.

## 의존성

| 패키지 | 용도 |
|--------|------|
| `watchdog >= 4.0.0` | 실시간 파일 시스템 이벤트 감시 |
| `pyinstaller >= 6.0.0` | macOS 독립 실행 파일 패키징 |
