# MAABA 개발 및 기여 가이드

## 목차

- [개발 TODO](#개발-todo)
  - [Runtime](#runtime)
  - [Pipeline](#pipeline)
- [`interface.json` 옵션 활용](#interfacejson-옵션-활용)
  - [Task와 Option 연결](#task와-option-연결)
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

## `interface.json` 옵션 활용

Task의 `option` 배열에는 최상위 `option` 객체의 키를 작성합니다. 배열에 작성된
순서대로 옵션이 표시되고, 선택된 case의 `pipeline_override`가 실행할 Task에
병합됩니다.

### Task와 Option 연결

```json
{
    "task": [
        {
            "name": "ExampleTask",
            "label": "예제 작업",
            "entry": "Example_Main",
            "option": [
                "Example_Mode",
                "Example_Count"
            ]
        }
    ],
    "option": {
        "Example_Mode": {},
        "Example_Count": {}
    }
}
```

### `select`: 드롭다운 단일 선택

공식 Project Interface v2의 `select`입니다. `cases`가 많아도 하나의 드롭다운만
차지하며, `default_case`가 없거나 유효하지 않으면 첫 번째 case를 선택합니다.

```json
{
    "Example_Mode": {
        "label": "실행 모드",
        "type": "select",
        "cases": [
            {
                "name": "Normal",
                "label": "일반",
                "pipeline_override": {
                    "Example_Main": {
                        "next": "Example_Normal"
                    }
                }
            },
            {
                "name": "Advanced",
                "label": "고급",
                "pipeline_override": {
                    "Example_Main": {
                        "next": "Example_Advanced"
                    }
                }
            }
        ],
        "default_case": "Normal"
    }
}
```

### `radio`: 라디오 단일 선택

`radio`는 기존 라디오 목록 UI를 유지하기 위한 MAABA 전용 확장 타입입니다.
데이터 구조와 override 동작은 `select`와 같지만 공식 Project Interface v2 타입은
아닙니다. 다른 범용 UI와 호환해야 하는 설정에는 `select`를 사용하세요.

```json
{
    "Example_Location": {
        "label": "지역 선택",
        "type": "radio",
        "cases": [
            {
                "name": "First",
                "label": "첫 번째 지역"
            },
            {
                "name": "Second",
                "label": "두 번째 지역",
                "pipeline_override": {
                    "Example_Select_Location": {
                        "action": {
                            "param": {
                                "target": [1000, 300, 1, 1]
                            }
                        }
                    }
                }
            }
        ],
        "default_case": "First"
    }
}
```

### `checkbox`: 다중 선택

여러 case를 동시에 선택합니다. 선택 순서와 관계없이 `cases`에 선언된 순서대로
각 `pipeline_override`가 병합됩니다.

```json
{
    "Example_Rewards": {
        "label": "수령할 보상",
        "type": "checkbox",
        "cases": [
            {
                "name": "Daily",
                "label": "일일 보상",
                "pipeline_override": {
                    "Example_Main": {
                        "next": "Example_Daily"
                    }
                }
            },
            {
                "name": "Weekly",
                "label": "주간 보상",
                "pipeline_override": {
                    "Example_Daily": {
                        "next": "Example_Weekly"
                    }
                }
            }
        ],
        "default_case": [
            "Daily",
            "Weekly"
        ]
    }
}
```

### `switch`: 활성화 전환

두 개의 case를 사용하는 토글 옵션입니다. case의 `name`은 공식 규칙에 따라
`Yes`와 `No`를 사용하는 것을 권장합니다.

```json
{
    "Example_Optional_Task": {
        "label": "추가 작업 실행",
        "type": "switch",
        "cases": [
            {
                "name": "Yes",
                "label": "활성화",
                "pipeline_override": {
                    "Example_Main": {
                        "next": "Example_Optional"
                    }
                }
            },
            {
                "name": "No",
                "label": "비활성화"
            }
        ],
        "default_case": "Yes"
    }
}
```

### `input`: 사용자 입력

`inputs`에 입력 필드를 선언하고 `pipeline_override` 문자열에서 `{필드명}`으로
값을 참조합니다. 값 전체가 플레이스홀더이면 `pipeline_type`에 따라 실제
`string`, `int`, `bool` 타입으로 변환됩니다. 다른 문자열 안에 포함된
플레이스홀더는 문자열로 치환됩니다.

지원 필드는 다음과 같습니다.

| 필드 | 설명 |
|---|---|
| `name` | 입력 필드 ID |
| `label` | UI에 표시할 이름 |
| `description` | 입력란 툴팁 |
| `default` | 최초 입력 문자열 |
| `pipeline_type` | `string`, `int`, `bool` 중 치환할 타입 |
| `verify` | 전체 입력값을 검사할 정규식 |
| `pattern_msg` | 정규식 검증 실패 시 표시할 메시지 |
| `password` | 입력값 마스킹 여부. MAABA에서는 평문을 사용자 설정에 저장하지 않음 |

```json
{
    "Example_Count": {
        "label": "반복 설정",
        "type": "input",
        "inputs": [
            {
                "name": "Chapter",
                "label": "챕터",
                "default": "4",
                "pipeline_type": "string",
                "verify": "^\\d+$",
                "pattern_msg": "숫자만 입력해 주세요."
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
            "Example_Main": {
                "next": "Chapter_{Chapter}",
                "timeout": "{Timeout}",
                "enabled": "{Enabled}"
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
