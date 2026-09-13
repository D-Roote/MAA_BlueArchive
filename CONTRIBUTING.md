# MAABA 개발 및 기여 가이드

## 목차

- [개발 TODO](#개발-todo)
  - [Runtime](#runtime)
  - [Pipeline](#pipeline)
- [`interface.json` 구성 및 옵션 활용](#interfacejson-구성-및-옵션-활용)
  - [`controller` 선택 규칙](#controller-선택-규칙)
  - [`task`와 `option` 연결](#task와-option-연결)
  - [`select`: 드롭다운 단일 선택](#select-드롭다운-단일-선택)
  - [`radio`: 라디오 단일 선택](#radio-라디오-단일-선택)
  - [`checkbox`: 다중 선택](#checkbox-다중-선택)
  - [`switch`: 활성화 전환](#switch-활성화-전환)
  - [`input`: 사용자 입력](#input-사용자-입력)
- [화면 캡처 성능 측정](#화면-캡처-성능-측정)

## 개발 TODO

### Runtime

- [ ] `runtime.py`에서 `interface.json` 선택 기능 구현

### Pipeline

- [ ] 로그인 로직 개선
- [ ] 소탕 보상 수령 개선(보상 창 바로 스킵 또는 대기)
- [ ] 카페 보상 수령 최적화
- [ ] 카페 모모톡 초대 작동 확인
- [ ] `interface.json`에 스케줄 학원 선택 옵션 추가

## `interface.json` 구성 및 옵션 활용

기본 구조와 필드는 [공식 Project Interface V2 문서](https://maafw.com/en/docs/3.3-ProjectInterfaceV2/)를
따릅니다. 아래의 이름과 값은 사용법을 설명하기 위한 일반화된 예시이므로 실제
프로젝트 구조와 지원 방식에 맞게 변경해야 합니다.

### `controller` 선택 규칙

`controller[].name`은 컨트롤러의 고유 ID이며 캡처 방식이나 입력 방식을 이름에서
추론하지 않습니다. `resource[].controller`를 작성한 경우에는 사용할 컨트롤러의
ID를 정확히 참조해야 합니다. 이 필드를 생략하면 해당 리소스가 모든 컨트롤러를
지원하는 것으로 처리합니다.

현재 UI에는 컨트롤러 설정 화면이 구현되어 있지 않습니다. 따라서
`controller` 배열에서 현재 리소스가 지원하는 첫 번째 Win32 컨트롤러를 기본으로
사용합니다. 안정성과 범용성이 높은 구성을 먼저 선언하고, 성능 등 다른 목적의
구성을 그 뒤에 선언하세요.

향후 설정에서 원하는 컨트롤러 값이 전달되면 `win32` 내부의 `screencap`, `mouse`,
`keyboard`를 비교합니다. 선택 순서는 다음과 같습니다.

1. 설정된 값이 모두 일치하는 첫 번째 컨트롤러를 선택합니다.
2. 완전히 일치하는 항목이 없으면 `screencap` > `mouse` > `keyboard` 순으로
   일치 여부를 비교합니다.
3. 일치도가 같으면 `controller` 배열에 먼저 선언된 항목을 선택합니다.
4. 어떤 값도 일치하지 않으면 첫 번째 컨트롤러로 폴백합니다.

각 방식이 생략된 컨트롤러는 현재 런타임 기본값인 `Background`,
`PostMessageWithWindowPos`, `PostMessage`를 사용해 비교합니다. 최소화 체크박스는
선택된 컨트롤러를 변경하지 않고 대상 창의 최소화 여부만 제어합니다. 따라서
최소화 캡처를 지원하지 않는 프로그램에서는 체크박스를 사용하지 않아야 합니다.

로그는 다음과 같이 한 줄로 출력합니다.

```text
[화면 캡처: PrintWindow]
[화면 캡처: FramePool / 설정 일부 일치]
[화면 캡처: PrintWindow / 폴백]
```

다음 예제에서는 범용 컨트롤러를 먼저 선언하므로 별도 설정이 없을 때
`PrimaryController`가 선택됩니다.

```json
{
    "controller": [
        {
            "name": "PrimaryController",
            "label": "기본 컨트롤러",
            "type": "Win32",
            "win32": {
                "window_regex": "^Example App$",
                "screencap": "PrintWindow",
                "mouse": "PostMessageWithWindowPos",
                "keyboard": "PostMessage"
            }
        },
        {
            "name": "PerformanceController",
            "label": "성능 우선 컨트롤러",
            "type": "Win32",
            "win32": {
                "window_regex": "^Example App$",
                "screencap": "FramePool",
                "mouse": "PostMessageWithWindowPos",
                "keyboard": "PostMessage"
            }
        }
    ],
    "resource": [
        {
            "name": "DefaultResource",
            "path": [
                "./"
            ],
            "controller": [
                "PrimaryController",
                "PerformanceController"
            ]
        }
    ]
}
```

### `task`와 `option` 연결

`task[].option`에는 최상위 `option` 객체에 정의한 옵션 ID를 문자열 배열로
지정합니다. 옵션은 배열에 지정한 순서대로 표시되며, 선택 결과의
`pipeline_override`는 해당 작업을 시작할 때 파이프라인에 병합됩니다.

```json
{
    "task": [
        {
            "name": "SampleTask",
            "label": "샘플 작업",
            "entry": "SampleEntry",
            "option": [
                "ExecutionMode",
                "RuntimeSettings"
            ]
        }
    ],
    "option": {
        "ExecutionMode": {
            "label": "실행 모드",
            "type": "select",
            "cases": [
                {
                    "name": "Standard",
                    "label": "기본"
                }
            ],
            "default_case": "Standard"
        },
        "RuntimeSettings": {
            "label": "실행 설정",
            "type": "input",
            "inputs": [
                {
                    "name": "Timeout",
                    "label": "제한 시간(ms)",
                    "default": "20000",
                    "pipeline_type": "int"
                }
            ],
            "pipeline_override": {
                "SampleEntry": {
                    "timeout": "{Timeout}"
                }
            }
        }
    }
}
```

### `select`: 드롭다운 단일 선택

공식 Project Interface V2의 단일 선택 옵션입니다. `cases`에 정의한 항목을
`QComboBox` 형식의 단일 드롭다운으로 표시합니다. `default_case`가 없거나
유효하지 않으면 첫 번째 항목을 선택합니다.

```json
{
    "option": {
        "ExecutionMode": {
            "label": "실행 모드",
            "type": "select",
            "cases": [
                {
                    "name": "Standard",
                    "label": "기본",
                    "pipeline_override": {
                        "SelectExecutionMode": {
                            "next": "StandardFlow"
                        }
                    }
                },
                {
                    "name": "Advanced",
                    "label": "고급",
                    "pipeline_override": {
                        "SelectExecutionMode": {
                            "next": "AdvancedFlow"
                        }
                    }
                }
            ],
            "default_case": "Standard"
        }
    }
}
```

### `radio`: 라디오 단일 선택

`radio`는 기존 라디오 목록 UI를 유지하기 위한 MAABA 전용 확장 타입입니다.
데이터 구조와 `pipeline_override` 병합 방식은 `select`와 같지만 공식 Project
Interface V2 타입은 아닙니다. 다른 범용 UI와 호환해야 하는 설정에는 `select`를
사용하세요.

```json
{
    "option": {
        "TargetPosition": {
            "label": "대상 위치",
            "type": "radio",
            "cases": [
                {
                    "name": "PositionA",
                    "label": "위치 A"
                },
                {
                    "name": "PositionB",
                    "label": "위치 B",
                    "pipeline_override": {
                        "SelectTargetPosition": {
                            "action": {
                                "param": {
                                    "target": [1000, 300, 1, 1]
                                }
                            }
                        }
                    }
                }
            ],
            "default_case": "PositionA"
        }
    }
}
```

### `checkbox`: 다중 선택

여러 항목을 동시에 선택하는 공식 옵션입니다. 사용자가 항목을 선택한 순서와
관계없이 `cases`에 선언된 순서대로 각 `pipeline_override`를 병합합니다.

```json
{
    "option": {
        "FeatureSelection": {
            "label": "추가 기능",
            "type": "checkbox",
            "cases": [
                {
                    "name": "FeatureA",
                    "label": "기능 A",
                    "pipeline_override": {
                        "RunFeatureA": {
                            "enabled": true
                        }
                    }
                },
                {
                    "name": "FeatureB",
                    "label": "기능 B",
                    "pipeline_override": {
                        "RunFeatureB": {
                            "enabled": true
                        }
                    }
                }
            ],
            "default_case": [
                "FeatureA",
                "FeatureB"
            ]
        }
    }
}
```

### `switch`: 활성화 전환

두 개의 선택 항목을 사용하는 공식 토글 옵션입니다. 각 항목의 `name`에는
`Yes`와 `No`를 사용합니다.

```json
{
    "option": {
        "EnableOptionalStep": {
            "label": "선택 단계 실행",
            "type": "switch",
            "cases": [
                {
                    "name": "Yes",
                    "label": "사용",
                    "pipeline_override": {
                        "OptionalStep": {
                            "enabled": true
                        }
                    }
                },
                {
                    "name": "No",
                    "label": "사용 안 함",
                    "pipeline_override": {
                        "OptionalStep": {
                            "enabled": false
                        }
                    }
                }
            ],
            "default_case": "Yes"
        }
    }
}
```

### `input`: 사용자 입력

`inputs`에 입력 필드를 정의하고 `pipeline_override`에서 `{입력 필드 ID}` 형식으로
값을 참조합니다. 속성값 전체가 플레이스홀더이면 `pipeline_type`에 따라
`string`, `int`, `bool` 중 지정한 타입으로 변환됩니다. 플레이스홀더가 다른
문자열에 포함되어 있으면 문자열로 치환됩니다.

지원 필드는 다음과 같습니다.

| 필드 | 설명 |
|---|---|
| `name` | 입력 필드 ID |
| `label` | UI에 표시할 이름 |
| `description` | 입력 필드 설명(툴팁) |
| `default` | 초기 입력값(문자열) |
| `pipeline_type` | 파이프라인에 치환할 데이터 타입: `string`, `int`, `bool` |
| `verify` | 전체 입력값을 검사할 정규식 |
| `pattern_msg` | 정규식 검증 실패 시 표시할 메시지 |
| `password` | 입력값 마스킹 여부. MAABA에서는 평문을 사용자 설정에 저장하지 않음 |

```json
{
    "option": {
        "RuntimeParameters": {
            "label": "실행 매개변수",
            "type": "input",
            "inputs": [
                {
                    "name": "TargetName",
                    "label": "대상 이름",
                    "default": "Default",
                    "pipeline_type": "string",
                    "verify": "^[A-Za-z0-9_-]+$",
                    "pattern_msg": "영문자, 숫자, 밑줄 및 하이픈만 입력해 주세요."
                },
                {
                    "name": "Timeout",
                    "label": "제한 시간(ms)",
                    "default": "20000",
                    "pipeline_type": "int",
                    "verify": "^[1-9]\\d*$"
                },
                {
                    "name": "Enabled",
                    "label": "활성화 여부",
                    "default": "true",
                    "pipeline_type": "bool"
                }
            ],
            "pipeline_override": {
                "SampleEntry": {
                    "next": "Target_{TargetName}",
                    "timeout": "{Timeout}",
                    "enabled": "{Enabled}"
                }
            }
        }
    }
}
```

`verify` 또는 타입 변환에 실패하면 오류 메시지가 입력란 아래 표시되고 작업 시작
버튼이 비활성화됩니다. `bool` 입력에는 `true`, `false`, `1`, `0`을 사용할 수
있습니다.

## 화면 캡처 성능 측정

### 최소화 지원 여부

| 항목 | 결과 |
|---|:---:|
| FramePool | O |
| FramePool(Minimize) | X |
| PrintWindow | O |
| PrintWindow(Minimize) | O |

### FramePool

| 항목 | 결과 |
|---|---:|
| 화면 캡처 방식 | **FramePool** |
| 테스트 간격 최소 | **약 9.400 ms** |
| 테스트 간격 최대 | **약 28.782 ms** |
| 테스트 간격 평균 | **약 19.11 ms** |
| 평균 테스트 주기 | **약 52.3회/초 (52.3 FPS)** |

### PrintWindow

| 항목 | 결과 |
|---|---:|
| 화면 캡처 방식 | **PrintWindow** |
| 테스트 간격 최소 | **약 17.762 ms** |
| 테스트 간격 최대 | **약 41.586 ms** |
| 테스트 간격 평균 | **약 26.34 ms** |
| 평균 테스트 주기 | **약 38.0회/초 (38.0 FPS)** |

### PrintWindow(Minimize)

| 항목 | 결과 |
|---|---:|
| 화면 캡처 방식 | **PrintWindow(Minimize)** |
| 테스트 간격 최소 | **약 18.079 ms** |
| 테스트 간격 최대 | **약 36.368 ms** |
| 테스트 간격 평균 | **약 26.06 ms** |
| 평균 테스트 주기 | **약 38.4회/초 (38.4 FPS)** |
