## TODO

# Runtime
- [ ] runtime.py 에서 interface.json의 Controller 선택 기능 구현
- [ ] 화면 캡쳐 방식 하드 코딩 수정
- [ ] runtime.py 에서 interface.json 선택 기능 구현
- [ ] winUI.py 에서 드롭 박스 기능 구현
- [ ] 최소화 Window 복원 개선
- [ ] enum 중복 import 정리
- [ ] Controller 재생성 생명주기 정리/개선
- [ ] AppRuntime 초기화의 UI Thread Blocking 개선
- [ ] 초기화 실패 시 Runtime 정리 예외 처리
- [ ] Start, Stop 생명주기 정리/개선

# Pipeline
- [ ] 로그인 로직 개선
- [ ] 소탕 보상 수령 개선(보상 창 바로 스킵 or 대기)
- [ ] 카페 학생 목록 대기 최적화
- [ ] 카페 호감도 터치 재확인 로직 추가
- [ ] 카페 보상 수령 최적화..?
- [ ] 카페 모모톡 초대 작동 확인
- [ ] 스케쥴 지역 레벨업 예외 처리 추가
- [ ] interface.json 스케쥴 학원 선택 옵션 추가


## ScreepCature

| 항목 | 결과 |
|---|:---:|
| FramePool | O |
| FramePool(Minimize) | X |
| PrintWindow | O |
| PrintWindow(Minimize) | O |

| 항목 | 결과 | 
|---|---:| 
| 화면 캡처 방식 | **FramePool** | 
| 테스트 간격 최소 | **약 9.400 ms** | 
| 테스트 간격 최대 | **약 28.782 ms** | 
| 테스트 간격 평균 | **약 19.11 ms** | 
| 평균 테스트 주기 | **약 52.3회/초 (52.3 FPS)** | 

| 항목 | 결과 | 
|---|---:|  
| 화면 캡처 방식 | **PrintWindow** | 
| 테스트 간격 최소 | **약 17.762 ms** | 
| 테스트 간격 최대 | **약 41.586 ms** | 
| 테스트 간격 평균 | **약 26.34 ms** | 
| 평균 테스트 주기 | **약 38.0회/초 (38.0 FPS)** | 

| 항목 | 결과 | 
|---|---:| 
| 화면 캡처 방식 | **PrintWindow(Minimize)** | 
| 테스트 간격 최소 | **약 18.079 ms** | 
| 테스트 간격 최대 | **약 36.368 ms** | 
| 테스트 간격 평균 | **약 26.06 ms** | 
| 평균 테스트 주기 | **약 38.4회/초 (38.4 FPS)** | 
