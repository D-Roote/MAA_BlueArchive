# 연결·화면 모니터 구현 기록

## 작업 상태 — 2026-10-03

- [x] select 닫힌 박스의 텍스트 영역 중심/상하 여백 검증(1px 이내).
- [x] 세부 설정 좌측 6px, 우측 4px 여백 및 그룹 사이 1px 회색 구분선 적용.
- [x] 디자인 변경 amend: `fcaea39`, UnitTest 122개 통과.
- [x] 설치 버전 확인: MaaFw 5.12.3 / PySide6 6.11.1.
- [x] 기능 설계를 코드 작성 전에 기록.
- [ ] 연결 탐색/사전 확인 서비스 및 회귀 테스트.
- [ ] 모니터링/설정 창의 동일한 연결 UI.
- [ ] 단발 캡처/선택 FPS 화면 미리보기 및 수명주기 테스트.
- [ ] 전체 검증, 기능별 커밋, 실제 게임/ADB 환경 확인.

기존 사용자 수정 `interface.json`, `pipeline/Task_Schedule/schedule_main.json`은 수정하거나 커밋하지 않는다.

## 참조 및 API 검증

구현 코드를 복사하지 않고 사용자 흐름과 수명주기를 참고한다.

- MAA(요청한 실제 앱): 기본 브랜치는 `dev-v2`, 확인 SHA `70d1f06bb843ba039849d2d2d1deb7a75e6364f4`.
  [ConnectSettings](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/70d1f06bb843ba039849d2d2d1deb7a75e6364f4/src/MaaWpfGui/Configuration/Single/Settings/ConnectSettings.cs),
  [ConnectSettingsUserControlModel](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/70d1f06bb843ba039849d2d2d1deb7a75e6364f4/src/MaaWpfGui/ViewModels/UserControl/Settings/ConnectSettingsUserControlModel.cs).
  자동 탐색, ADB 실행 파일 선택, 주소 선택/직접 입력, 연결 상태 무효화 흐름 참고. ADB 교체/서버 종료 등의 파괴적 기능은 제외.
- MaaEnd: 기본 브랜치 `v2`, 확인 SHA `1737381001a1bc4c1acdc9fadd3a3724ab4d9b85`.
  [README](https://github.com/MaaEnd/MaaEnd/blob/1737381001a1bc4c1acdc9fadd3a3724ab4d9b85/README.md),
  [설치 코드](https://github.com/MaaEnd/MaaEnd/blob/1737381001a1bc4c1acdc9fadd3a3724ab4d9b85/tools/setup/build_and_install.py).
  화면 UI는 MaaEnd 내부가 아니라 README가 링크한 MistEO/MXU 클라이언트에 구현되어 있다.
  [ScreenshotPanel](https://github.com/MistEO/MXU/blob/9fa8cc51e8ff8cd89d99f3ea55fe3a7a82e6ede3/src/components/ScreenshotPanel.tsx),
  [screenshot_service](https://github.com/MistEO/MXU/blob/9fa8cc51e8ff8cd89d99f3ea55fe3a7a82e6ede3/src-tauri/src/screenshot_service.rs) 확인 후 적용.
- 실제 API는 [MaaFw v5.12.3 Python controller](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/binding/Python/maa/controller.py),
  [Toolkit](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/binding/Python/maa/toolkit.py),
  [PI v2](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/docs/en_us/3.3-ProjectInterfaceV2.md) 및 설치된 바인딩으로 교차 확인.
  `post_connection().wait().succeeded`, `connected`, `post_screencap().wait().get()` 확인.
  `Toolkit.find_desktop_windows()` 및 `find_adb_devices(specified_adb)` 사용.
  Adb 입력/캡처 방식은 기기 탐색 결과의 비트마스크를 사용하며, interface에 임의 필드를 추가하지 않는다.

## 설계

### 연결

1. `interface.controller`와 현재 리소스의 controller 허용 목록에 선언된 Win32/Adb 프리셋만 노출.
2. 작업 실행용 AppRuntime과 별개인 사전 확인 서비스. 파이프라인/리소스/Tasker를 생성하거나 프로그램을 자동 실행하지 않는다.
3. Win32는 프리셋의 window/class 정규식에 맞는 실행 중 창을 탐색하고 선택. 실행 파일/PID 정보도 표시할 수 있도록 확장하며, 대상 없는 경우 명확히 안내.
4. Adb는 자동 탐색 또는 지정 adb 실행 파일로 탐색, 기기 선택 또는 주소 입력. 재탐색/선택 변경 시 이전 연결 결과 무효화.
5. 선택한 프리셋/대상으로 SDK 연결을 실제 확인. 컨트롤러는 서비스가 소유하며 명시적으로 비활성화/해제.
6. 모니터와 설정 창은 동일한 상태/서비스를 공유한다. 설정에는 ADB 경로·주소와 화면 FPS/모드를 사용자 설정으로 저장한다.
7. QThread에서 탐색/연결/캡처. 한 번에 한 요청만 허용하여 UI 스레드 차단 및 SDK 호출 중첩 방지.
8. 작업 시작 시 사전 확인/미리보기를 안전하게 끝낸 후 기존 실행 흐름으로 넘긴다. 실행 중 사전 재연결은 차단.

### 화면

1. 기본은 단발 `스크린샷 테스트`. 연속 모드 선택 시 FPS 1/2/5/10(기본 2) 중 선택.
2. 성공적으로 연결한 대상으로만 캡처. 작업 실행 중에는 같은 실행 컨트롤러의 캐시 화면만 사용하고 추가 캡처/입력/재연결하지 않는다.
3. 단일 요청 종료 후 다음 프레임 예약(요청 큐 누적 금지), 마지막 프레임만 보관.
4. 화면 카드를 접거나 설정으로 이동/창 닫기/대상 변경/오류 시 연속 캡처를 중지한다.
5. ndarray(BGR) → 소유 데이터를 가진 QImage로 변환, UI 스레드에서 QPixmap 표시. 비율 유지, 원본 크기·응답 시간·상태 표시.
6. 실기 성능을 측정하지 않은 상태에서 FPS 성능을 보장하지 않는다. 오류 시 상태 표시 및 재시도 버튼 제공.

### 검증/커밋

- 탐색 필터, 잘못된 정규식/설정, 선언되지 않은 컨트롤러 차단, ADB 선택/오프라인, 연결 실패/정리, 빈 이미지 검증.
- 단일 요청 제한, 대상 변경의 오래된 결과 무시, 실행/정지 전환과 닫기 대기, 두 연결 UI 상태 동기화, FPS 제한·접기 중지·화면 비율 테스트.
- `.venv/Scripts/python.exe -m unittest discover -s UnitTest -q`, `py_compile`, `git diff --check`.
- 설계/기능은 별도 기능별 커밋. 디자인 커밋에 기능 구현을 섞지 않는다. 푸시 없음.

## 사용량/다음 세션 인계

이 세션의 도구에는 계정의 잔여 토큰, 5시간 사용량, 초기화 시각을 읽는 기능이 없다. 숫자를 추정하지 않는다.
[OpenAI 공식 안내](https://learn.chatgpt.com/docs/pricing)에 따르면 실제 잔여량/초기화는 사용자 usage dashboard 또는 Codex CLI `/status`에서 확인해야 한다.
한도 도달 전 중지를 보장할 수 없으므로 각 단계 완료 시 이 파일의 체크리스트, 검증 결과, 커밋 및 다음 할 일을 갱신한다.
재개 시 먼저 이 파일과 Git 상태를 읽고, 기존 JSON 수정은 계속 보존한다.

## 다음 작업

연결 서비스와 SDK 모의 테스트부터 구현. 이후 공통 연결 UI 및 화면 컨트롤러를 연결한다.
