# 연결·화면 모니터 구현 기록

## 2026-10-04 — 로그 우클릭 메뉴의 앱 테마 적용

- 원인: 로그 QTextEdit의 기본 컨텍스트 메뉴는 메뉴 표면색/항목 색상을 정의하지 않아 시스템 다크 메뉴와 앱 라이트 텍스트 색상이 혼재할 수 있었다.
- 변경: Qt 표준 로그 메뉴의 Copy/Select All/단축키·선택 영역 복사 동작을 유지하고 `logContextMenu` 전용 QSS를 앱의 현재 유효 테마로 적용한다. 라이트/다크 표면·텍스트·비활성 항목·호버·구분선을 명시한다. 시스템 팔레트를 따르는 기본 아이콘은 제거해 반대 테마에서의 아이콘 대비 문제도 피한다. 메뉴는 닫힘/예외 시 deleteLater로 정리하며 다른 창/편집기의 메뉴나 OS 테마를 바꾸지 않는다.
- 검증: 시스템 다크 팔레트+강제 라이트에서 실제 메뉴 렌더 표면 및 글자 팔레트, 강제 다크·시스템 추종·다시 열 때 테마 전환, 호버 렌더, 선택 복사/전체 선택·비활성 복사, 우클릭 시 viewport 위치와 메뉴 해제를 테스트한다. [QTextEdit.createStandardContextMenu](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QTextEdit.html#PySide6.QtWidgets.QTextEdit.createStandardContextMenu), [Qt QMenu style sheet](https://doc.qt.io/qt-6/stylesheet-reference.html#qmenu-widget).
- 결과: 로그 중복 회귀 **13개**, 메뉴 테마/동작 회귀 **6개**, 전체 UnitTest **305개**, py_compile 및 diff --check 통과. 리소스 JSON SHA256은 작업 전과 동일하다. 기존 winUI.py 사용자 변경은 로그 수정 커밋에 amend했으며 메뉴 수정은 별도 커밋이다.

## 2026-10-04 — 실행 결과 오류 중복 제거

- 원인: initialize/run_task 내부 정리 실패가 반환값 또는 예외에 포함되고, RuntimeWorker의 finally 정리 재시도에서 같은 원인을 다시 추가했다. StopWorker/RuntimeWorker의 종료 콜백 순서에 따라서도 같은 중지 실패가 두 번 출력될 수 있었다.
- 변경: 결과 전용 메시지 합성에서 알려진 Runtime/정리 문맥 접두사를 제외한 정확한 원인 키를 비교한다. 서로 다른 오류·부분 문자열은 보존한다. UI 결과 중복 키는 실행별로 초기화하고 종료 콜백에만 적용하므로, 반복 파이프라인/진행 로그나 다음 실행의 동일 오류는 숨기지 않는다. finally 정리 자체와 StopWorker가 예외를 던지는 경우도 결과 오류로 전달하며 실제 실패 상태/정리 재시도 정책은 그대로 유지한다.
- 사용자 winUI.py의 성공 기호/오류 기호 표시 변경은 이 로그 수정 커밋에 통합한다. 리소스 JSON은 커밋하지 않는다. feature/Codex에서 기능별 커밋 후 feature/TaskJSON은 5f61777에 두고 Codex만 push한다.

## 2026-10-04 — 자동 복원 후 재실행의 실제 포커스 보정

- 사용자 재현: 자동 최소화 작업 종료 후 복원된 게임을 클릭하지 않고 재실행하면 최소화 직후 복구된다. 게임에 한 번 실제 포커스를 주면 발생하지 않는다. 홀짝 횟수가 아닌 복원 후 활성화 상태를 기준으로 수정한다. 기존 실제 로그에서도 이전 Tasker/Controller 파괴는 다음 연결 전에 끝났으므로 종료 순서를 근본 원인으로 취급하지 않는다.
- 분석: 5.12.3 `PseudoMinimizeHelper`는 대상이 실제 전경 창이 되면 투명한 pseudo-minimize를 해제한다. `InputUtils.send_activate_message`는 SDK 입력 전에 `WM_ACTIVATE / WA_ACTIVE`를 보내며, 이는 실제 Windows 전경 전환과 다르다. 자동 비활성 복원 이후 게임의 활성화/포커스 처리와 이 메시지가 결합되는 경로가 사용자 재현 조건에 부합한다. 게임 내부에서 전경으로 바뀌는 정확한 함수까지 확정한 것은 아니다. [PseudoMinimizeHelper](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaWin32ControlUnit/Screencap/PseudoMinimizeHelper.cpp), [InputUtils](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaWin32ControlUnit/Input/InputUtils.h), [WM_ACTIVATE](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-activate).
- 원복: `maaLifecycle.py`와 강제 Destroy 테스트를 제거하고 일반 SDK Tasker/Win32Controller 및 SDK 자체 소유권 관리로 돌아간다. 바인딩 private handle/own을 변경하거나 살아 있는 Job이 참조하는 native 핸들을 강제로 파괴하지 않는다. 정지 완료·sink 제거·controller inactive·원본 복원 흐름을 유지하며 inactive 실패 시 재시도할 객체/원본은 보존한다.
- 구현: 자동 최소화 파이프라인 실행에만 원본 WindowPlacement 저장 → 실제 포커스 준비 → SDK 컨트롤러 생성/연결 → 첫 Action에서 한 번 시스템 최소화 요청을 적용한다. 실제 포커스는 `SetForegroundWindow` 한 번과 최대 0.9초의 `GetForegroundWindow` 확인으로 준비하며 세션 종료 시 준비 플래그를 초기화한다. 이미 최소화된 창은 원본을 보존한 채 비활성 일반 표시 후 준비한다. 새 프로그램 실행은 입력 방지 보호막 안에서 준비한다. 일반 실행·프로그램 실행만 있는 큐·연결 진단·작업 중 노드/캡처에는 포커스를 가져오지 않는다.
- 제한: Windows가 전경 전환을 거부하거나 실제 전경 상태가 확인되지 않으면 SDK 연결/작업 제출 전 실패로 안내한다. `AttachThreadInput`, 가짜 클릭, ALT 주입, 작업 중 반복 포커스 강제, 강제 재최소화는 사용하지 않는다. 시작 시 대상이 잠시 전경으로 전환될 수 있다. 작업 중 사용자가 직접 복구하는 동작은 계속 허용한다. [SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow).
- 유지: 종료 후 일반 창은 `SW_SHOWNOACTIVATE`, 최소화 원본은 `SW_SHOWMINNOACTIVE`로 위치/크기/상태를 복원한다. 최대화 원본도 보존한다. 이는 다음 실행의 실제 포커스 준비를 대체하지 않는다.
- 검증: 전체 UnitTest **286개** 통과. 새 포커스 회귀 10개는 6회 자동 복원/무클릭 재실행, 생성 전 준비, 세션 플래그 초기화, 포인터 HWND, 지연·실패·취소, 일반 실행/실행 전용 큐 제외를 검사한다. 별도 실제 Windows/SDK 창에서 정상 완료 **6회**·수동 중지 **6회**, `PostMessageWithWindowPos`의 실제 SDK 클릭 전후 최소화 및 종료 후 원본 위치/크기/상태 복원을 확인했다. 시작 창만 활성화하여 실제 MAA 시작 버튼의 전경 권한을 모델링하며 대상 창에 수동 클릭/포커스를 주지 않는다. 기존 검증과 달리 SDK 입력 활성화 메시지를 실제로 거친다. 이는 실게임 내부 포커스 처리의 완전한 재현을 의미하지 않는다.
- Git: 사용자의 winUI.py 로그 변경 및 리소스 JSON은 보존하고 직전 Fix에 amend한다. 두 브랜치의 공통 HEAD를 유지하며 원격 push는 하지 않는다.

## 2026-10-04 — 최소화 재분석 및 모니터링 문구 설계

- 재확인한 5.12.3 SDK는 PrintWindow/FramePool 연결 중 테스트 캡처로 실제 최소화를 투명한 일반 창으로 바꾼다. `inactive`는 스타일만 되돌리며 실제 최소화 복원과는 다르다. 기존 실행 경로는 연결 이후에 WindowPlacement를 저장·크기 조절하여 SDK가 바꾼 상태를 원본으로 저장하거나 `SW_RESTORE`로 전경 활성화했다. 로그에서도 클릭 전 지연 중 pseudo-minimize가 해제되는 사례가 확인되어 입력 방식만 원인으로 단정하지 않는다.
- 원본은 SDK 생성/연결 및 시작 보호막 **이전**에 저장한다. 실행용 크기 조절은 SDK 연결 전에 비활성 일반 창 표시로 수행하며 시작 보호막 안에서도 먼저 크기를 조절한 뒤 최소화한다. 종료/중지/초기화 실패 시 SDK 정리 후 원본을 복원한다. 사전 확인도 SDK 연결 전 원본을 저장하고 해제 후 복원하여 다음 실행에 변형된 상태를 전달하지 않는다.
- 사전 확인 해제는 SDK의 투명 pseudo-minimize가 남아 있을 때만 원본 위치·상태를 복원한다. SDK 변형이 없으면 사용자가 그동안 변경한 위치·크기·최소화/복구를 덮어쓰거나 일반 창을 활성화하지 않는다. 복원 실패 상태는 컨트롤러 참조가 없어도 다음 작업 시작을 막고 해제를 재시도한다.
- 실제 테스트 창/SDK로 추가 재현: 정상 최소화(`IsIconic=1`)에도 전경 창 HWND가 `None`이면 기존 검사가 실패했다. 전경 창 없음은 정상적인 비활성 상태로 인정하며 HWND 포인터/정수 표현도 통일한다. 전송 성공만으로 성공 처리하지 않고 실제 iconic 상태 및 대상 창이 전경이 아닌 조건은 유지한다.
- 유휴 연속 모니터링은 최소화 창에 자동 연결/추가 캡처하지 않는다. 마지막 이미지를 유지하고 복구를 기다리며, 사용자가 창을 복구하면 자동 재개한다. 대상 HWND/PID 소실은 종료 조건이다. 작업 중에는 기존처럼 캐시만 읽는다.
- MaaEnd의 현재 프런트엔드는 MXU다. [MXU screenshot_service.rs](https://github.com/MistEO/MXU/blob/9fa8cc51e8ff8cd89d99f3ea55fe3a7a82e6ede3/src-tauri/src/screenshot_service.rs)는 인스턴스당 하나의 캡처 루프를 유지하고 작업 실행 중 `post_screencap`을 건너뛴다. 이 설계를 재확인했으며 MaaEnd 자체의 최소화 지원까지 확인한 것으로 해석하지 않는다.
- 연속 모니터링의 일시적 오류/준비 재시도/연결 종료는 실행 로그에 기록하지 않는다. 단일 테스트 및 명시적인 연결 진단 오류는 유지한다. 화면 하단은 출처(테스트 캡처/실행 캐시)를 먼저 표시하고 이미지 가로·세로 수치를 제거한다.
- 표시 FPS는 1초마다 실제 경과 시간으로 측정한다. 목표 1~15 FPS는 경고 제외. 기존 절댓값 미달 기준 `ceil(목표/4)`을 유지하며 **누적 10회** 미달 관측 후 `출력 프레임이 낮습니다. (N FPS) 목표 프레임을 낮추세요.`를 별도 줄에 표시한다. 회복/작업 전환/숨김/중지·재개는 경고를 지우지 않으며 목표 FPS 변경만 누적·경고를 초기화한다. 단순 문구 갱신은 관측 횟수를 늘리지 않는다.
- 검증 계획: SDK 연결 전 원본 저장/크기 조절 순서, 시작 보호막과 실패 복원, 사전 확인 정리, 최소화 유휴 재연결 대기·복구, 로그 무출력, 9/10회 경계·회복 후 유지·목표 변경 초기화·저 FPS 제외, 긴 줄/경고 줄의 실제 가로 스크롤 회귀. 실제 게임 재검증은 사용자 환경에서 필요하다. 사용자 JSON은 수정·커밋하지 않고 기존 Fix를 amend한다.
- 구현 검증: 전체 UnitTest **276개** 통과. 별도 `native_monitor_probe.py`에서 실제 Windows 창/설치된 MaaFramework 5.12.3 PrintWindow 컨트롤러를 사용하여 일반/이미 최소화된 원본의 크기 조절·연결·최소화·캡처·종료 후 WindowPlacement 일치 및 유휴 최소화 자동 연결 대기를 확인했다. 테스트가 만든 창만 사용했으며 실제 게임 탐색·입력은 없다. 수동 주소 ADB 캡처에는 Win32 최소화 검사를 적용하지 않는다. `py_compile`, `git diff --check` 및 사용자 JSON SHA256 보존을 확인하고 직전 `Fix: 연속 모니터링 상태 유지`에 amend한다.

## 2026-10-04 — 캡처 컨트롤러 충돌 및 경고·가로 스크롤 수정

아래는 이전 구현·검증 기록이며, 현재 상태 저장 시점·사전 연결 복원·경고 정책은 위 설계로 보완되었다.

- 원인 확인: MaaFramework **5.12.3**의 PrintWindow/FramePool은 `PseudoMinimizeHelper`를 사용한다. 입력 비활성화와 무관하게 원래 창 확장 스타일/투명도를 컨트롤러별로 보관하고 최소화 시 투명화·비활성 복원, inactive/파괴 시 스타일 복구·재최소화를 수행한다. 같은 HWND에 별도 컨트롤러를 붙이면 서로 다른 원본을 보관하여 작업 캡처·입력·최소화/복원이 충돌할 수 있다. [Manager](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaWin32ControlUnit/Manager/Win32ControlUnitMgr.cpp), [PseudoMinimizeHelper](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaWin32ControlUnit/Screencap/PseudoMinimizeHelper.cpp).
- 실행 중 미리보기는 **작업 컨트롤러의 캐시 읽기만** 수행한다. 별도 컨트롤러 생성, 추가 screencap, 입력, inactive, 창 스타일/투명도 변경을 하지 않는다. 사전 컨트롤러는 작업 시작 전에 안전하게 정리하고, 작업·중지 콜백과 SDK 정리가 모두 끝난 뒤에만 재연결한다. 실행 중 모니터링을 시작해도 실제 HWND/PID/프리셋 식별 정보만 보관하여 종료 후 같은 대상을 재확인한다. 높은 출력 FPS 설정이 새 캡처 FPS를 보장하지 않으며 실행 캐시의 새 화면 속도는 작업 주기에 종속된다.
- `Unknown input method: 0`: Win32 `make_input`은 Null(0)을 지원 분기로 처리하지 않고 오류를 기록한다. 실행 중 별도 생성 경로와 Null 값을 제거했다. 유휴 사전 확인은 지원되는 PostMessage로 생성하되 실제 입력 요청은 하지 않는다. 작업의 interface 프리셋 입력/캡처 설정은 유지한다.
- 기존 `_preserve_minimized_window` 분기는 작업 종료 시 원래 위치·크기·상태를 버리므로 제거했다. 작업/중지 완료 → SDK inactive 및 컨트롤러 파괴 → 원래 WindowPlacement 복원 순서. 시작 전 이미 최소화되어 있었다면 그 상태로 돌아가며 강제 일반 창 복원은 하지 않는다. 복원 실패 시 원본을 보존하고 정리를 재시도할 수 있다.
- FPS 문구의 아이콘/기호/퍼센트 제거. **목표 1~15 FPS는 출력값만 표시하고 경고하지 않는다(출력 0이나 목표 초과도 포함).** 목표 15 FPS 초과에만 절댓값 차이를 max(1, ceil(목표 FPS / 4))와 비교한다. 기준: 30은 8, 45는 12, 60은 15 FPS. 초기 측정·FPS 전환은 경고하지 않는다.
- 화면 문구는 가용 폭에 맞춰 줄바꿈하고 최소 가로 폭을 요구하지 않는다. 모니터는 세로 스크롤만 허용하되 실제 콘텐츠도 viewport 폭 안에 맞춘다. 긴 오류/경고, 가로·세로 이미지, 최소 폭, 창 높이/카드 순서 변경, 접기·펼치기 회귀로 확인한다.
- `wait_freezes Image is empty pre_image=true cur_image=false`는 화면 안정화 비교의 첫 캡처가 비어 있음을 뜻한다. 컨트롤러 상태 충돌을 제거했으나 로그만으로 원인이 유일하다고 단정하지 않는다. [ActionHelper](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaFramework/Task/Component/ActionHelper.cpp). 실제 게임에서 계속 발생하면 해당 노드/캡처 방식의 별도 검증이 필요하다. 사용자 파이프라인 변경은 보존한다.
- 검증: 캡처·FPS 수정만 포함한 첫 Fix 트리를 별도 폴더에서 **246개** 회귀로 검증했다. 연속 유지·재연결·가로 스크롤 수정까지 포함한 전체 **260개** 회귀, `py_compile`, `git diff --check` 통과. 리소스/파이프라인 JSON **34개**의 SHA256이 작업 전과 동일하다. 실제 게임·전원·프로그램 종료 호출은 없다. 관련 직전 두 Fix에 amend하고 Codex/TaskJSON의 공통 HEAD 및 사용자 JSON 미커밋 상태를 유지한다.

## 2026-10-04 — 연속 모니터링 유지 및 실행 중 캡처 개선 설계

아래는 최초 설계·검증의 이력이다. 별도 Win32 컨트롤러/Null 입력/10% 경고 정책은 위 수정으로 폐기되었다.

- 원인: 실행 중 미리보기는 `AppRuntime.capture_cached_frame()`만 사용한다. 캐시는 작업의 인식/액션/지연 주기에서만 갱신되므로 표시 FPS를 올려도 새 화면 수가 늘지 않는다. FPS 변경, 작업 시작/완료, 접기/페이지 이동, 일시적 캡처 오류도 현재 스트림을 중지한다.
- 연속 모니터링의 사용자 실행 의도와 일시적인 작업/연결 전환 대기를 분리한다. FPS 변경은 다음 예약 간격에 반영하고, 작업 시작/완료 및 숨겨진 화면에서도 실행 의도를 유지한다. 사용자의 중지, 실제 연결 해제/대상 변경, 프로그램 종료 시만 해제한다. 일시적 오류는 마지막 이미지를 유지하고 제한된 주기로 재시도하며 같은 오류 로그를 반복하지 않는다.
- 실행 컨트롤러에는 추가 `post_screencap()` 요청을 보내지 않는다. 실제 작업의 HWND/캡처 프리셋 스냅샷으로 별도의 Win32 캡처 전용 컨트롤러를 연결한다. 입력 방법은 공식 `MaaWin32InputMethodEnum.Null`로 비활성화하고, 작업의 클릭/입력/캡처 큐와 분리한다. 초기화/정리 중에는 준비 대기, 일시적 독립 연결 실패에는 실행 캐시를 보조 경로로 사용한다. 목표 FPS는 상한이며 SDK/OS/CPU 성능에 따른 실효 FPS를 보장하지 않는다.
- 근거: [v5.12.3 Python Controller](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/binding/Python/maa/controller.py), [ControllerAgent 큐](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaFramework/Controller/ControllerAgent.cpp), [Win32 입력/캡처 정의](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/include/MaaFramework/MaaDef.h).
- 검증: 상태 변경/FPS 변경/접기/페이지 이동/작업 시작·완료/준비 지연/일시적 오류/수동 중지/연결 끊김/창 닫기, 요청 직렬화, 오래된 결과 무시, 작업 큐 무간섭, 입력 없는 별도 컨트롤러 및 이미지 소유권을 회귀 테스트한다. 실게임 FPS 측정은 별도의 사용자 검증이 필요하다.
- Git: 현재 `feature/Codex`는 `feature/TaskJSON`의 조상이다(0 ahead/4 behind). Codex를 현재 TaskJSON까지 FF → 기능별 Codex 커밋 → TaskJSON을 Codex까지 FF 후 원래 브랜치로 복귀한다. 파이프라인 미커밋 수정/미추적 파일은 해시를 비교해 보존하며 이번 코드 커밋에 포함하지 않는다. 원격은 수정하지 않는다.
- 진행 1 완료: 실제 HWND/프리셋 스냅샷과 입력 없는 독립 Win32 캡처 큐 구현. 연결은 한 세션에서 재사용하고 실패 시 1초 간격으로 재시도하며 실행 캐시로 보조한다. QImage 소유권은 유지하면서 불필요한 검증용 이미지 복사 1회를 제거했다. 신규 회귀 10개 및 전체 UnitTest 231개 통과; 연속 유지 로직은 다음 기능 단위에서 진행한다.
- 직전 Fix amend: 화면 하단에 목표 FPS와 실제 출력 FPS를 함께 표시한다. 새 이미지가 실제 `paintEvent`에서 그려진 횟수를 monotonic 경과 시간으로 나누고, 1초마다 표시를 갱신한다. 일반 재노출/크기 변경 repaint 및 그리기 전에 합쳐진 프레임은 중복 계산하지 않는다. 초기 측정 중에는 경고하지 않고, 목표 대비 절대 편차가 10% 이상이면 하락 시 `성능 미달`, 초과 시 `FPS 편차` 경고를 표시한다. 화면이 숨겨져 그리지 못하는 상태는 성능 실패로 계산하지 않으며 중지 시 측정 타이머도 종료한다. 신규 회귀 11개 통과. [Qt paintEvent](https://doc.qt.io/qt-6/qwidget.html#paintEvent), [QTimer 정확도](https://doc.qt.io/qt-6/qtimer.html#accuracy-and-timer-resolution) 기준.
- 진행 2 완료: 연속 실행 의도는 FPS 변경, 작업 시작/완료, 이전 프레임 처리 대기, 접기/페이지 이동 및 일시적 오류에서 유지한다. 재시도는 250ms 간격으로 직렬 수행하고 동일 오류 로그는 한 번만 남긴다. 작업 시작 시 사전 컨트롤러만 안전하게 정리하고, 새 작업 세션 준비가 완료되면 독립 캡처를 재개한다. 오래된 프레임은 무시하며 실제 연결 해제/사용자 중지/앱 종료 시만 중지한다. 작업 실행 중 해제 버튼도 모니터 컨트롤러만 해제하고 작업 컨트롤러는 변경하지 않는다. FPS 변경/작업 전환 시 측정을 초기화하여 이전 속도와 새 목표를 섞지 않는다. 일시 오류 중에도 목표/출력 FPS와 오류 설명을 함께 표시하며 이미지·전체 패널을 지우지 않는다. 신규 상태 전환 회귀 10개 및 기존 접기/페이지/오류 회귀 갱신 완료.
- 최종 검증: 전체 UnitTest **252개**, `py_compile`, `git diff --check` 통과. 파이프라인/리소스 JSON **34개**의 작업 전후 SHA256이 동일하다. 실제 게임의 캡처 속도·CPU 사용량은 모의 테스트로 보장하지 않으므로 목표/실효 FPS 표시로 실기 확인한다. Git 커밋은 UI/런타임/테스트/기록만 포함하며 파이프라인 수정과 `test.json`은 미커밋 상태로 보존한다.

## 작업 완료 후 동작 — 구현 완료

### 직전 커밋 추가 수정 — 광학 정렬 및 컨트롤러별 UI

- `OpticalCheckBox`는 QCheckBox의 네이티브 표시·선택·클릭·키보드 동작을 유지하되, [tightBoundingRect](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QFontMetricsF.html#PySide6.QtGui.QFontMetricsF.tightBoundingRect)로 실제 글자 영역의 중심을 표시 아이콘의 중심에 맞춘다. 숫자상 위젯 높이가 아닌 실제 그려진 텍스트 픽셀로 밝은/어두운 테마, 한글/영문 혼합, 선택/해제 상태를 검증한다.
- 공유 연결 설정에서 선택한 프리셋을 `interface.json.controller` 및 리소스 허용 목록으로 판별한다. 타입 이름/포트로 추측하지 않는다. Win32는 대상 프로그램 종료/MAA 종료 유지, Adb는 앱 종료/에뮬레이터 종료/MAA 종료 표시. 컨트롤러 선택 변경 시 열려 있는 세부 설정과 왼쪽 요약을 즉시 동기화한다. Adb만 선언된 경우에도 첫 허용 프리셋을 판별한다.
- `close_emulator=true`이면 `close_app=true`를 저장 정규화와 UI 모두에서 강제하며 앱 종료를 해제할 수 없다. 에뮬레이터 선택 해제 후에는 앱 종료도 해제 가능. 각 타입의 선택은 따로 보존하고 화면 요약/실행 스냅샷에서는 다른 타입의 종료 옵션을 제외한다. 이번에만/모두 해제/재실행 저장 동작 유지.
- **ADB 종료는 UI/선택 저장 단계이며 실제 명령은 미구현**: [공식 ADB 문서](https://developer.android.com/tools/adb#am)에서 `am force-stop`에는 패키지가 필요함을 확인했다. 현재 설정에는 종료 패키지 및 대상 에뮬레이터 PID/인스턴스 매핑이 없고 작업 런타임도 Win32 전용이다. 포트만 보고 임의 앱/에뮬레이터를 종료하지 않는다. UI에 준비 단계 안내를 표시하고 해당 요청은 로그를 남기고 생략한다. MAA 종료 및 시스템 옵션은 기존 구현을 유지한다.
- 사용자 요청에 따라 위 수정은 `83fc2a4`에 amend한다. 사용자 JSON은 변경하지 않는다. 아래 최초 구현의 테스트 수는 이전 검증 기록이다.
- 검증: 전체 UnitTest **214개**, 완료 후 동작 **42개**(기본/150% 배율), `py_compile`, `git diff --check` 통과. Win32 및 Adb 밝은/어두운 테마 렌더링 확인. 실제 ADB/전원/프로그램 종료 호출 없음.

### 최초 구현

- 참고: MAA의 [PostActionSetting](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev/src/MaaWpfGui/Models/PostActionSetting.cs), [TaskQueueView](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev/src/MaaWpfGui/Views/UI/TaskQueueView.xaml). 중앙 세부 설정에 옵션 표시, 왼쪽에는 중앙 정렬된 제목/요약과 설정 아이콘을 둔다. 코드를 복사하지 않고 현재 Qt 구조에 맞게 작성한다.
- 대상 프로그램 종료/MAA 종료는 독립 선택. 화면 잠금/절전/최대 절전/시스템 종료는 하나만 선택하며 다시 클릭하면 해제할 수 있다. 모두 해제는 모든 옵션과 이번에만을 해제한다.
- `user_config.json.after_actions`에는 영구 선택만 저장한다. 이번에만 활성화 중의 변경은 세션 전용이며 기존 영구 선택은 보존한다. 정상 완료 시 세션 선택을 소비하고 영구 선택으로 돌아온다. 재실행은 항상 영구 선택 및 이번에만 해제 상태로 시작한다.
- 실제 파이프라인 작업을 하나 이상 정상 완료한 경우에만 수행한다. 초기화/작업/정리 실패, 수동 중지, 창 닫기, 프로그램 실행만 한 경우에는 수행하지 않는다. 실행 시 옵션 스냅샷 고정, 양쪽 작업 스레드 종료와 SDK 정리 이후 정확히 한 번 처리한다.
- 프로그램 종료는 실제 실행 컨트롤러의 HWND/PID/전체 실행 경로를 연결 시 보관하고 종료 직전 재검증한 후 `WM_CLOSE`를 요청한다. 프로세스명 일괄 종료/강제 종료는 사용하지 않는다. 현재 실행 런타임은 Win32 전용이므로 ADB 앱/에뮬레이터 종료는 범위 밖이다.
- Windows: [LockWorkStation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-lockworkstation), [SetSuspendState](https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-setsuspendstate), [ExitWindowsEx](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-exitwindowsex). 전원 권한 활성화/복원, 실패 로그, 강제 종료 플래그 없음. MAA 종료는 기존 안전한 close 흐름을 사용한다.
- MAA 종료와 시스템 동작을 함께 선택하면 SDK/모니터 정리가 끝나고 close가 허용된 시점에 숨겨진 별도 Python 도우미를 시작한다. [상속 핸들](https://docs.python.org/3/library/subprocess.html#subprocess.STARTUPINFO)로 해당 MAA 프로세스의 실제 종료를 기다린 후 시스템 요청. 절전 중 MAA 종료가 지연되거나 종료 시 전원 요청이 유실되는 것을 방지한다. 결과는 `assets/user/debug/after_action.log`에 기록한다. 현재 Python 실행 환경 기준이며 frozen 배포는 도우미 패키징 확장이 필요하다.
- 검증은 모의 Windows API만 사용하며 실제 잠금/전원/게임 종료는 수행하지 않는다. UI 정렬, 선택 배타성/해제, 저장/복원/이번에만, 정상/실패/중지/닫기/콜백 순서, 대상 재검증을 회귀 테스트한다. 모두 한 커밋으로 작성한다.

구현: `afterActions.py`(선택 모델/Windows 요청/종료 후 도우미), `afterActionUI.py`(세부 설정), `winUI.py`(실행 스냅샷/종료 판정/저장/요약). 완료 후 설정은 실행 중 편집 불가이며 기존 작업 옵션 편집 허용 정책은 유지한다.
단독 시스템 동작은 요청 실패를 실행 로그에 표시한다. 프로그램 정상 종료·잠금·시스템 종료는 요청 수락과 실제 완료가 다를 수 있다. 에뮬레이터/강제 프로세스 종료, 장시간 검증 및 실제 전원 동작 검증은 포함하지 않는다.
밝은/어두운 테마에서 정렬과 옵션 화면을 확인했다. 요약 길이가 바뀌어도 왼쪽 영역의 라벨 중심과 설정 아이콘 오른쪽 여백은 유지한다. 추가 회귀 **33개**, 전체 UnitTest **205개**, `py_compile`, `git diff --check` 통과. Windows API 시그니처 로딩 확인(실제 동작 호출 없음). 커밋: `Feat: 작업 완료 후 종료 및 전원 동작 추가`. 사용자 JSON 두 파일은 변경/커밋하지 않는다.

저장 예시 (직접 수정 없이 UI에서 선택하면 자동 저장):

```json
"after_actions": {
    "close_program": false,
    "close_app": false,
    "close_emulator": false,
    "close_maa": false,
    "system_action": ""
}
```

`system_action`은 `""`, `"lock"`, `"sleep"`, `"hibernate"`, `"shutdown"` 중 하나다. `once`는 세션 전용으로 저장하지 않는다.

## 최신 추가 수정 — 정렬, FPS, UI 상태 복원

사용자가 연속 표시/높이 조절의 의도한 동작을 실기 확인했다.

지정한 기존 커밋에 변경과 독립 회귀 테스트를 반영했다. 이후 커밋도 새 부모에 재연결되므로 해시가 변경되었다.

| 지정 커밋 | amend 후 | 반영 내용 |
| --- | --- | --- |
| `3ece700` | `be6f5b7` | 설정의 연결 제목/컨트롤러/대상/상세 박스 왼쪽 기준선, 제목 중앙 정렬 및 여백 정돈 |
| `ffb0677` | `a9b9b16` | 연속 표시 15/30/45/60 FPS 선택·정규화·저장 추가 |
| `fcaea39` | `e0844f4` | Select의 focus 잔류 강조 제거, 마우스가 벗어나면 원래 색으로 복원 |

병합 부모·기존 메시지·저자 정보 유지. 원래 기록은 `backup/Codex-before-ui-amend-1aa09f8`에 보존했다.
임시 인덱스로 새 트리를 검증한 뒤 브랜치만 재연결했으며, 사용자 JSON을 체크아웃/스태시하지 않았다. 아래 이전 기록의 해시는 amend 이전 기준이다.

새 기능은 `Feat: 창 크기 및 UI 레이아웃 상태 유지`로 별도 커밋한다.
`user_config.json`의 `ui_state`에 다음 정보를 저장한다.

- 창 위치·크기·최대화: [Qt 공식 saveGeometry/restoreGeometry](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html#PySide6.QtWidgets.QWidget.restoreGeometry), Base64 인코딩.
- 중앙/모니터링 좌우 폭: `workspace_sizes`, 처음 표시할 때 실제 레이아웃 크기를 기준으로 다시 적용.
- 연결/화면/로그 펼침 상태: `monitor_expanded`. 기존 카드 순서/세로 높이 비율 저장도 유지.

이동/크기/좌우 폭/카드 펼침 변경은 300ms 지연 저장하고 종료할 때 즉시 저장한다. 초기 로딩의 설정 저장이 이전 UI 상태를 덮어쓰지 않도록 보호했다.
화면/DPI가 달라지면 Qt가 화면 안으로 위치/크기를 조정할 수 있으며, 패널 최소 크기는 유지한다. 잘못된 UI 값은 기본값으로 대체하고 작업 설정은 보존한다.
최소화 종료 시 다음 실행 창은 숨기지 않는다. UI 상태를 복원해도 작업/연결/연속 캡처는 자동 시작하지 않는다.
추가 FPS는 최대 요청 빈도이며 실효 60 FPS를 보장하지 않는다. 최종 UnitTest **172개**, `py_compile`, `git diff --check` 통과. 밝은/어두운 테마 정렬 이미지 확인 완료. 사용자 JSON 두 파일은 원래 변경 그대로 보존하며 새 기능 파일만 커밋한다.

## 추가 개선 — 연속 표시 및 세로 크기 조절

사용자 확인: 기본 exe 연결 및 캡처가 실제 환경에서 작동함. ADB/장시간 검증은 별도.

1. 연속 캡처를 하나의 UI 세션으로 취급하여 매 프레임 상태 문구/버튼 enabled 상태/전체 패널 동기화를 반복하지 않는다. 이미지 전용 불투명 캔버스만 갱신한다.
2. 연결은 내용 높이를 유지하고, 펼쳐진 화면/로그는 남은 높이를 확장해 사용한다. 세로 경계를 드래그하여 배분하며 이미지도 가용 크기에 맞춰 비율 유지 확대한다.
3. 관련 변경을 각각 `Fix`, `Design` 커밋으로 분리. 기존 JSON 수정 보존.

1단계 완료: UnitTest 157개, py_compile, diff --check 통과. 연속 프레임 중 상태/버튼/카드 크기가 고정되고 이미지 캔버스만 다시 그려짐을 Qt 이벤트로 검증했다.
커밋: `12f15f0` (`Fix: 연속 캡처 상태 전환 및 패널 깜빡임`). 연속 표시 중 연결 UI는 세션 전체에서 잠그고, 중지 후 해제한다.

2단계 구현: [Qt 공식 QSplitter API](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSplitter.html)를 확인하고 세로 splitter를 적용했다.
연결과 접힌 카드는 내용/헤더 높이로 고정하며, 펼친 화면/로그만 남은 공간을 확장해 쓴다. 둘 다 펼치면 사이의 짧은 회색 핸들을 드래그해 높이를 배분한다.
패널 순서가 바뀌거나 연결이 두 영역 사이에 있어도 높이 조절이 작동한다. 최소 높이를 유지하고 드래그로 영역을 0까지 접지 않는다.
높이 비율은 `user_config.json`의 `monitor_height_weights`에 화면/로그 키로 저장하여 접기/다시 펼치기 및 순서 변경/재실행에도 유지한다.
미리보기의 180px 고정 높이를 제거하고, 이미지 전용 캔버스를 남은 높이에 확장했다. 이미지는 너비·높이 중 제한되는 쪽에 맞춰 비율을 유지하며 확대하므로 가로로 긴 화면은 너비가 제한되면 상하 여백이 남을 수 있다.
2단계 완료: Qt 라이트/다크 렌더링 및 실제 마우스 높이 조절 확인. UnitTest **163개**, py_compile, diff --check 통과.
모든 카드 순서, 최소 높이 제한, 접기/재실행 높이 비율 복원, 이미지 비율 유지 확대를 검증했다. `Design: 화면 및 로그 세로 분할과 자동 확대` 커밋으로 정리.
기존 JSON 2개는 SHA256이 시작 시와 동일하며 커밋에서 제외했다. 최종 실기 확인은 사용자가 연속 캡처 및 경계선 드래그로 진행.

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
- [x] 실제 exe 기본 연결/캡처 동작 사용자 확인.
- [ ] ADB 및 장시간 실제 게임 환경 확인(사용자 실행 환경 필요).

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

1. 기본은 단발 `스크린샷 테스트`. 연속 모드 선택 시 FPS 1/2/5/10/15/30/45/60(기본 2) 중 선택.
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
