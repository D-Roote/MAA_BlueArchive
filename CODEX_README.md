# 연결·화면 모니터 구현 기록

## 작업 상태 — 2026-10-03

- [x] select 닫힌 박스의 텍스트 영역 중심/상하 여백 검증(1px 이내).
- [x] 세부 설정 좌측 6px, 우측 4px 여백 및 그룹 사이 1px 회색 구분선 적용.
- [x] 디자인 변경 amend: `fcaea39`, UnitTest 122개 통과.
- [x] 설치 버전 확인: MaaFw 5.12.3 / PySide6 6.11.1.
- [x] 기능 설계를 코드 작성 전에 기록.
- [x] 연결 탐색/사전 확인 서비스 및 회귀 테스트.
- [x] 모니터링/설정 창의 동일한 연결 UI.
- [x] 단발 캡처/선택 FPS 화면 미리보기 및 수명주기 테스트.
- [x] 모의 전체 검증 및 연결 기능 커밋.
- [x] 화면 기능 구현/최종 검증 완료. `Feat: 단발 및 FPS 화면 모니터` 커밋으로 정리.
- [ ] 실제 게임/ADB 환경 확인(사용자 실행 환경 필요).

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
5. ndarray(BGR) → 소유 데이터를 가진 QImage로 변환, UI 스레드에서 QPainter로 표시. 비율 유지, 원본 크기·응답 시간·상태 표시.
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

연결 서비스 및 두 UI 구현 완료. 창 핸들 재사용 차단/수동 프로그램 디렉터리/타 프리셋 ADB 재사용 차단 포함 UnitTest 141개, py_compile, diff --check 통과.
작업 시작과 창 종료는 진행 중인 진단이 끝나고 사전 컨트롤러를 해제할 때까지 대기한다. SDK 네이티브 대기 자체의 강제 종료는 하지 않는다.
SDK가 응답하지 않는 경우 백그라운드 요청 및 창 닫기가 계속 대기할 수 있다. UI 스레드는 차단하지 않으며, 강제 스레드 종료로 SDK 객체를 손상시키지 않는다.

현재 interface는 Win32 2개만 선언하므로 ADB 입력은 표시하지 않는다. 향후 허용된 Adb 프리셋을 선언하면 사전 확인 UI가 표시된다.
이번 연결 기능은 **진단 전용**이다. 기존 작업 실행기의 Win32 전용 실행 경로와 대상 자동 선택은 변경하지 않았다. 진단에서 선택한 창이 작업 실행 대상으로 고정되는 것은 아니다.
실제 게임/ADB 승인을 포함한 연결 성공은 모의 테스트로 대신할 수 없어 사용자 환경 검증이 남는다.

연결 기능 커밋: `3ece700` (`Feat: 연결 사전 확인 및 공통 설정`).

화면 패널과 단일 요청 기반 FPS 타이머 구현 완료. 실행 중 캐시 읽기는 AppRuntime의 정리 락으로 보호한다.
화면 접기/설정 페이지 이동/수동 중지/대상 변경/오류/작업 시작 및 종료/창 닫기에서 반복 캡처를 중지한다.
진행 중인 네이티브 호출은 안전하게 끝날 때까지 대기하고, 오래된 프레임을 표시하지 않는다.
요청 FPS는 최대 요청 빈도이며 실효 FPS를 보장하지 않는다. 실행 캐시는 마지막 SDK 인식 화면이므로 동일한 프레임이 재표시될 수 있다.

화면 수명주기 포함 UnitTest 154개 통과. Windows offscreen의 폰트 목록이 비어 있어 테스트에 실제 맑은 고딕을 등록하고 상하 정렬/배치도 검증했다.
라이트/다크에서 1120×800 Qt 렌더링을 확인했다. 한글 표시, 두 연결 UI 배치, 비율 유지 미리보기, 카드 스크롤을 확인했고 이미지 데이터는 모의 단색 프레임을 사용했다.
캡처마다 연결 콤보를 재생성하지 않고 편집 중인 ADB 입력을 덮어쓰지 않도록 했다. 프로그램 설정 변경도 연결을 무효화한다.
ADB 실행 파일 변경 시 이전 기기의 설정을 재사용하지 않는 테스트 포함 최종 **UnitTest 155개**, py_compile, diff --check 통과.
기존 사용자 JSON 2개의 SHA256도 작업 시작 시와 동일함을 확인했다. 푸시하지 않는다.

### 실기 확인 순서

1. 게임을 직접 실행하고 모니터링 `연결`을 펼쳐 선언된 프리셋으로 `대상 탐색` → 대상 선택 → `연결 확인`.
2. 설정 창의 `연결 설정 · 사전 확인`에서도 동일한 대상/상태가 표시되는지 확인.
3. `화면`을 펼쳐 단발 테스트. 이후 연속 모드에서 1→2→5 FPS 순으로 부하와 실제 갱신 상태를 확인. 10 FPS는 환경에 따라 부담이 클 수 있다.
4. 화면 접기/설정 이동/대상 변경/해제/창 종료 시 연속 요청이 중지되는지 확인.
5. 실제 작업 시작 시 사전 연결 해제 후 기존 실행이 진행되는지 확인. 실행 중 화면 테스트는 마지막 캐시 이미지이므로 인식 간격보다 빠르게 새 장면이 나오지 않는다.
6. Adb는 현재 interface에 없으므로 동작 확인이 필요하면 **사용자가** 해당 리소스에서 허용한 Adb 프리셋을 선언하고 에뮬레이터 ADB 경로·주소/기기 승인을 준비한다. 이 작업은 interface를 임의 변경하지 않았다.

코드 작업은 완료. 다음 세션은 위 실기 체크리스트를 진행하고 실제 연결/캡처 실패가 있다면 로그와 선언된 프리셋으로 원인을 확인한다.
Git에는 디자인 amend `fcaea39`, 설계 `a7c5499`, 연결 기능 `3ece700`, 그리고 화면 기능 커밋이 순서대로 남는다. 화면 커밋 해시는 `git log -1`로 확인한다.
