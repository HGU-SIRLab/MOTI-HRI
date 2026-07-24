# 구현 진행 로그

`docs/architecture.md`의 로드맵(§10) 대비 실제 구현 상태를 기록한다. 설계 자체가 바뀌면 architecture.md를, 무엇을 언제 어떻게 만들었는지는 이 문서를 갱신한다.

## 1단계 — Layer 1 매크로 이식 (완료, 커밋 `d0d9bea`, `1e673c6`)

### 만든 것

| 파일 | 내용 |
|---|---|
| `hardware/config.py` | DXL 주소, 포트 자동탐색, 팬/틸트·바퀴·Layer1 관절 상수. v1의 `RPS_ARM_ID`/`DANCE_ID`/`HEAD_PAN_ID` 별칭은 전부 제거하고 `LEFT_ARM_ID`/`SHOULDER_ID`/`PAN_ID`로 통일 |
| `hardware/dxl_io.py` | v2에서 거의 그대로 재사용 (저수준 I/O 래퍼) |
| `hardware/wheel.py` | v2에서 거의 그대로 재사용 (WASD 텔레옵) |
| `hardware/init.py` | 홈 포지션 초기화(`initialize_robot`) + 신규 `shutdown_all_motors` |
| `hardware/motion.py` | v1 `dance.py`에서 이식한 `play_greeting_motion`/`play_hug_motion`/`play_shy_motion`/`play_dance`(8단계 안무) + 통합 진입점 `play_manual_motion(name, ...)` |
| `scripts/test_motions.py` | 로봇 연결 후 번호 입력으로 매크로를 개별 실행하는 독립 테스트 도구 |

### 설계 대비 구현 확인

- `play_manual_motion(name: "dance"|"hug"|"greeting"|"wave"|"shy")` — architecture.md §05가 정의한 시그니처와 정확히 일치. `wave`는 v1에 대응 함수가 없어 `greeting`의 별칭으로 처리.
- Layer 1의 "LLM은 무엇을 할지만 고르고 궤적은 코드가 고정" 원칙 준수 — `play_manual_motion`을 통해서는 `hold_seconds` 같은 내부 파라미터도 노출되지 않음(직접 함수 호출 시에만 오버라이드 가능).
- v1에서 함께 발견했던 `play_both_arms_motion`/`play_right_arm_motion`/`play_left_arm_motion`은 이번엔 **포팅하지 않음** — architecture.md §05의 Layer 1 목록에 없고, 현재는 필요하지 않다고 판단. 단축 관절 하나만 움직이는 재사용 가능한 프리미티브라 Layer 2(`express_gesture`) 구현 시 참고할 소스가 v1에 남아있음.
- 가위바위보/OX퀴즈용 `play_rps_motion`도 미포팅 — architecture.md §09 오픈 이슈로 아직 범위 미결정.

### 자체 리뷰에서 발견해 고친 버그 (커밋 `1e673c6`)

1. **`DANCE_TURN_RPM` 하드코딩 회귀** — 처음엔 `50.0` 고정값으로 넣었는데, v1 원본은 `TURN_RPM * 2`(환경변수로 `TURN_RPM`을 바꾸면 춤 회전속도도 비례해서 바뀜)였다. 그대로 두면 `.env`로 `TURN_RPM`을 조정한 사람은 춤 시퀀스만 옛날 속도로 남는 조용한 회귀였음 — `TURN_RPM * 2`로 수정.
2. **`_perform_shoulder_dance`의 인자 순서 불일치** — motion.py의 다른 모든 함수는 `(port, pkt, lock, ...)` 순서인데 이 함수만 v1 원본 그대로 `(pkt, port, lock, ...)`였다. 실제 호출부는 우연히 다 맞게 호출하고 있어 즉시 터지는 버그는 아니었지만, 같은 파일 안에서 인자 순서가 다르면 다음 수정 때 순서를 헷갈리기 딱 좋은 상태였음 — 통일.
3. **`init.shutdown_all_motors`도 같은 종류의 순서 불일치** — `initialize_robot`과 맞춤.
4. **춤 종료 `finally` 블록의 안전성 결함** — `pygame.mixer.music.stop()`이 블록 맨 앞에서 아무 보호 없이 호출되고 있었다. 믹서가 초기화되기 전에 예외가 나서 춤 루틴이 조기 종료되면, 이 줄에서 다시 예외가 터져 그 아래에 있는 `shared_state['mode'] = 'tracking'` 복구와 팔 원위치 복귀가 전부 스킵될 수 있었음 — 로봇이 `dancing` 모드에 영구히 갇히는 시나리오. `try/except`로 격리해서 음악 정지가 실패해도 안전 복구는 항상 실행되게 수정.
5. **`play_dance()` 재진입 가드 없음** — 춤이 재생 중일 때 다시 호출하면 음악·안무 스레드가 겹쳐 실행될 수 있었음. `threading.Event` 기반 가드(`is_dancing()`)를 추가해 이미 실행 중이면 새 요청을 무시하도록 수정.
6. **테스트 스크립트 종료 시 경합 조건** — 춤이 아직 재생 중인데 사용자가 종료(`q`)하면, `shutdown_all_motors`가 춤 스레드와 동시에 같은 모터에 명령을 보내고 포트를 닫아버릴 수 있었음. 종료 전 `is_dancing()`이 꺼질 때까지 대기하도록 수정.

### 부수적으로 발견한 환경 문제

- **Windows 기본 콘솔(cp949)이 이모지를 인코딩하지 못해 `print()`가 크래시함.** 로봇이 연결되지 않아 `find_dxl_port()`가 못 찾겠다는 경고(⚠️)를 출력하는 순간 바로 재현되는, import 단계에서부터 걸리는 문제였다. `hardware/config.py` 로드 시 stdout/stderr를 UTF-8로 재설정하도록 수정. v1/v2도 실제로 이 문제에 노출되어 있었을 가능성이 있음(터미널 설정에 따라 안 걸렸을 수도 있음).
- **ID 10 모터의 용도가 불명확함.** `config.py` 어디에도 이름이 없는데 v1·v2 `init.py` 모두 위치 1001로 초기화해왔다. 무엇인지 몰라 정체를 확인 못한 채 그대로 보존만 해뒀다 — 실물 로봇에서 확인 필요.

### 검증 상태

파이썬 컴파일(`py_compile`) 및 실제 import까지만 확인했다(로봇 미연결). **실제 모터 동작은 검증되지 않음** — `python scripts/test_motions.py`로 사용자가 직접 확인 예정.

### 다음 단계

로드맵(§10) 2단계 — 메모리 계층 전환(`remember_fact` function calling, `user_profiles.json` 스키마를 facts 배열로 변경). 아직 시작 안 함.
