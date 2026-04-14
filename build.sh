#!/bin/bash
# build.sh — PyInstaller로 macOS .app 패키징

set -e

APP_NAME="KoreanFilenameFixer"
DIST_DIR="dist"

echo "=== 의존성 설치 ==="
pip install -r requirements.txt

echo "=== PyInstaller 빌드 ==="
pyinstaller \
  --windowed \
  --onefile \
  --name "$APP_NAME" \
  --clean \
  main.py

echo ""
echo "=== 빌드 완료 ==="
echo "실행파일: $DIST_DIR/$APP_NAME"
echo ""
echo "※ 첫 실행 시 Gatekeeper 경고가 뜨면:"
echo "   시스템 설정 → 개인 정보 보호 및 보안 → '확인 없이 열기' 클릭"
echo "   또는 터미널에서: xattr -cr $DIST_DIR/$APP_NAME.app"
