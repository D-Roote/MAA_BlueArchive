# MAABA 개발 및 기여 가이드

## 목차

- [개발 TODO](#개발-todo)
  - [Runtime](#runtime)
  - [Pipeline](#pipeline)
- [`interface.json` 옵션 활용](#interfacejson-옵션-활용)
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

## `interface.json` 옵션 활용

옵션의 기본 구조와 필드는 [공식 Project Interface V2 문서](https://maafw.com/en/docs/3.3-ProjectInterfaceV2/)를
따릅니다. `task[].option`에는 최상위 `option` 객체에 정의한 옵션 ID를 문자열
배열로 지정합니다. 옵션은 배열에 지정한 순서대로 표시되며, 선택 결과의
`pipeline_override`는 해당 작업을 시작할 때 파이프라인에 병합됩니다.

### `task`와 `option` 연결

```json
{
    "task": [
        {
            "name": "DailyRoutine",
            "label": "일일 작업",
            "entry": "DailyRoutine",
            "option": [
                "ExecutionMode",
                "TimeoutSettings"
            ]
        }
    ],
    "option": {
        "ExecutionMode": {
            "label": "실행 모드",
            "type": "select",
            "cases": [
                {
                    "name": "Normal",
                    "label": "일반"
                }
            ],
            "default_case": "Normal"
        },
        "TimeoutSettings": {
            "label": "제한 시간 설정",
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
                "DailyRoutine": {
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
        "BattleStage": {
            "label": "전투 스테이지",
            "type": "select",
            "cases": [
                {
                    "name": "Chapter3",
                    "label": "3장",
                    "pipeline_override": {
                        "EnterStage": {
                            "next": "MainChapter_3"
                        }
                    }
                },
                {
                    "name": "Chapter4",
                    "label": "4장",
                    "pipeline_override": {
                        "EnterStage": {
                            "next": "MainChapter_4"
                        }
                    }
                }
            ],
            "default_case": "Chapter4"
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
        "BountyLocation": {
            "label": "현상수배 지역",
            "type": "radio",
            "cases": [
                {
                    "name": "Highway",
                    "label": "고가도로"
                },
                {
                    "name": "DesertRailroad",
                    "label": "사막 기찻길",
                    "pipeline_override": {
                        "SelectBountyLocation": {
                            "action": {
                                "param": {
                                    "target": [1000, 300, 1, 1]
                                }
                            }
                        }
                    }
                }
            ],
            "default_case": "Highway"
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
        "RewardTypes": {
            "label": "수령할 보상",
            "type": "checkbox",
            "cases": [
                {
                    "name": "DailyReward",
                    "label": "일일 보상",
                    "pipeline_override": {
                        "CollectDailyReward": {
                            "enabled": true
                        }
                    }
                },
                {
                    "name": "WeeklyReward",
                    "label": "주간 보상",
                    "pipeline_override": {
                        "CollectWeeklyReward": {
                            "enabled": true
                        }
                    }
                }
            ],
            "default_case": [
                "DailyReward",
                "WeeklyReward"
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
        "UseCafeInvitation": {
            "label": "카페 초대 실행",
            "type": "switch",
            "cases": [
                {
                    "name": "Yes",
                    "label": "사용",
                    "pipeline_override": {
                        "InviteStudent": {
                            "enabled": true
                        }
                    }
                },
                {
                    "name": "No",
                    "label": "사용 안 함",
                    "pipeline_override": {
                        "InviteStudent": {
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
        "CustomStage": {
            "label": "스테이지 직접 입력",
            "type": "input",
            "inputs": [
                {
                    "name": "ChapterNumber",
                    "label": "챕터 번호",
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
                "EnterStage": {
                    "next": "MainChapter_{ChapterNumber}",
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
