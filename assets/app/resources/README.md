# UI 리소스

이 디렉토리는 PySide6 UI 전용 이미지와 아이콘을 보관합니다.
자동화 파이프라인과 인식용 이미지가 있는 `assets/resources`와는 별개입니다.

- `icons/controls/`: 체크박스와 라디오 버튼의 선택/미선택, 호버, 비활성 상태별 SVG
- `icons/actions/`: Task 세부 설정 버튼의 흰색 톱니바퀴 SVG

아이콘은 24×24 viewBox로 직접 작성한 벡터 이미지입니다.
주요 색은 `#00AEEF`, 호버 색은 `#009BD6`, 윤곽선은 `#CBD5E1`을 사용합니다.
QSS에서는 Qt 검색 경로 `maabaicons:`로 참조하며, MainWindow가 절대 경로를 등록합니다.

배포 시 `assets/app/pySide6`와 이 디렉토리를 함께 포함해야 합니다.
UI 파일 경로는 `winUI.py`의 위치를 기준으로 계산하므로 실행 디렉토리에 의존하지 않습니다.
