# macOS Code Signing and Notarization

이 문서는 `KoreanFilenameFixer.app`을 Developer ID로 서명하고 Apple notarization까지 진행하기 위한 준비 상태와 절차를 정리한다.

## 현재 상태

- Xcode command line tools: 사용 가능
- `xcrun notarytool`: 사용 가능
- `xcrun stapler`: 사용 가능
- 로컬 키체인 Developer ID code signing identity: 없음 (`security find-identity -v -p codesigning` 결과 0개)
- GitHub repository secrets: 없음
- 현재 공개 릴리스(`v1.11.0`): ad-hoc signed, not notarized

따라서 지금 바로 notarized release를 만들 수는 없고, Apple Developer Program의 Developer ID 인증서와 notarization 인증 정보가 필요하다.

## 필요한 준비물

- Apple Developer Program 멤버십
- `Developer ID Application` 인증서
- 로컬 빌드용 notarization credential
  - 권장: `xcrun notarytool store-credentials`로 저장한 keychain profile
  - 대안: App Store Connect API key 기반 credential
- CI 자동화를 위한 GitHub secrets
  - `MACOS_CERTIFICATE_P12`: Developer ID Application 인증서 `.p12`의 base64 값
  - `MACOS_CERTIFICATE_PASSWORD`: `.p12` 비밀번호
  - `MACOS_SIGN_IDENTITY`: 예: `Developer ID Application: Your Name (TEAMID)`
  - `APPLE_ID`: Apple ID 이메일
  - `APPLE_TEAM_ID`: Team ID
  - `APPLE_APP_SPECIFIC_PASSWORD`: notarytool에 사용할 app-specific password

## 로컬 인증서/도구 확인

```bash
xcode-select -p
xcrun --find notarytool
xcrun --find stapler
security find-identity -v -p codesigning
```

`security find-identity` 결과에 `Developer ID Application: ...` 항목이 있어야 Developer ID 서명이 가능하다.

## notarytool profile 저장

```bash
xcrun notarytool store-credentials kff-notary \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

## 로컬 서명 빌드

서명만 적용하고 notarization은 하지 않는 빌드:

```bash
MACOS_BUNDLE_ID="com.sungback.KoreanFilenameFixer" \
MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
bash build.sh
```

PyInstaller는 실제 signing identity가 지정되면 hardened runtime(`--options=runtime`)으로 서명한다.

## 로컬 서명 + notarization 빌드

```bash
MACOS_BUNDLE_ID="com.sungback.KoreanFilenameFixer" \
MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
MACOS_NOTARIZE=1 \
MACOS_NOTARY_PROFILE="kff-notary" \
bash build.sh
```

`build.sh`는 다음 순서로 처리한다.

1. PyInstaller 빌드
2. Developer ID 서명
3. strict codesign 검증
4. zip 생성
5. `xcrun notarytool submit --wait`
6. `xcrun stapler staple`
7. stapled app 기준 zip 재생성

## 배포본 검증

```bash
codesign --verify --deep --strict dist/KoreanFilenameFixer.app
codesign -dv --verbose=4 dist/KoreanFilenameFixer.app
xcrun stapler validate dist/KoreanFilenameFixer.app
spctl -a -vvv -t exec dist/KoreanFilenameFixer.app
```

서명된 notarized 배포본에서는 `codesign -dv` 출력에 Developer ID authority/team identifier가 보여야 하고, stapler 검증이 통과해야 한다.

## 참고

- Apple: Signing Mac Software with Developer ID
  https://developer.apple.com/developer-id/
- Apple: Notarizing macOS software before distribution
  https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- Apple: Customizing the notarization workflow
  https://developer.apple.com/documentation/security/customizing-the-notarization-workflow
- Apple: Hardened Runtime
  https://developer.apple.com/documentation/security/hardened-runtime
