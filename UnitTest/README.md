# UnitTest

Windows에서 가상환경을 준비한 뒤 저장소 루트에서 실행합니다.

```powershell
.venv/Scripts/python.exe -m unittest discover -s UnitTest -v
```

게임 실행, Tasker 수명주기, 최소화, 작업 목록 및 설정 UI의 회귀를 검증합니다. 게임 실행·캡처·입력 API는 모의 객체로 대체하고 Qt는 offscreen 모드로 사용하므로 실제 게임을 실행하거나 조작하지 않습니다.

최소화 테스트는 게임 창에 한 번만 `WM_SYSCOMMAND / SC_MINIMIZE`를 전달하는지, 전송 실패/창 종료/전경 잔류 시 정상적으로 실패하는지, 이후 사용자 복구를 다시 최소화하지 않는지 확인합니다.

표준 PI v2 기본 Win32 구성도 파일로 읽어 검증합니다. `window_regex`, 상대 리소스 경로 목록, 임의의 `task.entry`로 실행하며, `PrintWindow`/`FramePool`과 최소화 설정을 조합합니다. 내장 게임 실행 작업, Agent, 별도 `program` 설정, Pipeline의 `focus`는 최소화 기능의 필수 조건이 아닙니다.

현재 런타임은 PI 전체를 구현한 범용 클라이언트는 아닙니다. Win32 `window_regex`가 필요하며 첫 번째 리소스를 사용합니다. `class_regex`만을 사용하는 검색, `import`, `attach_resource_path`, Agent/pretask 선언 등의 처리를 보장하지 않습니다. 기본 구성 테스트 통과를 PI 전체 호환 또는 다른 게임의 실제 최소화·캡처 성공으로 해석하지 않습니다.

스펙 기준: [MaaFramework 5.12.3 Project Interface V2](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/docs/en_us/3.3-ProjectInterfaceV2.md).
