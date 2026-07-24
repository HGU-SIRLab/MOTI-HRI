# 통합 시 연결해야 할 지점 (Integration TODOs)

로드맵 §10 1~5단계(로봇 없이 가능한 작업 전부)까지 만든 코드는 `scripts/run_no_robot.py`가 실제로 이어붙였다. 이 문서는 **아직 진짜로 안 이어진 지점만** 남긴다 — 뭔가 새로 만들 때 여기 항목도 같이 지워나갈 것(파일 자체의 오래된 원칙).

## 로봇이 있어야 이어지는 지점

### `vision/face.py`의 `shared_state['detected_user']` ↔ `profile_manager` — 아직 안 이어짐

`face_tracker_worker(..., brain=RobotBrain())`는 얼굴을 인식하면 `shared_state['detected_user']`를 갱신하도록 되어 있다(팬/틸트 추적 스레드 안에서 지속적으로 갱신되는 방식). 하지만 **이 경로는 아직 아무 데도 연결 안 됨** — `run_no_robot.py`는 이거 대신 훨씬 단순한 `identify_user_via_webcam()`(최대 8초짜리 동기 함수, 스레드도 shared_state도 안 씀)로 이름을 알아내고 그걸로 `profile_manager`를 연결했다. `face_tracker_worker`를 실제로 스레드로 띄우게 되면(=로봇 연결 후, 팬/틸트가 필요해지는 시점) 그 스레드가 갱신하는 `shared_state['detected_user']`를 세션이 볼 수 있게 다시 연결해야 한다 — 지금의 1회성 웹캠 인식 방식과는 다른 설계가 필요할 수 있음(예: 대화 도중 다른 사람으로 바뀌는 경우를 어떻게 할지).

### `core/motion_tools.py`의 `play_gesture` 툴 — 만들어졌지만 어디에도 등록 안 됨

툴 자체(`make_play_motion_tool`)는 존재하고 잘못된 제스처 이름을 거르는 부분까지는 검증됐다. 하지만 `run_no_robot.py`의 `tools=[...]`에는 로봇이 없어서 아직 안 넣었다. **할 일**: 로봇 연결 후 `port`/`pkt`/`lock`/`shared_state`가 생기면 `make_play_motion_tool(...)`을 호출해 `remember_fact`, `set_emotion`과 함께 세션에 추가 — 이 시점에 `run_no_robot.py`를 `launcher.py`로 승격.

### Layer 2 파라미터 제스처 (`express_gesture`) — 코드 없음

안전 범위 표는 `docs/architecture.md` §05에 이미 있으므로, 구현 시 그 표를 그대로 `hardware/motion.py`에 옮기면 된다.

### ID 10 모터 — 정체 여전히 불명

`hardware/init.py`가 위치 1001로 초기화만 하고 있음. 실물 로봇으로 어떤 관절인지 확인되면 `hardware/config.py`에 정식 이름을 붙이고 "용도 불명" 주석을 지운다.

### 스피커→마이크 음향 에코로 인한 barge-in 오탐 — 코드 없음

이어폰 없이 스피커+마이크를 같이 쓰면(=로봇의 실제 구성) 스피커 소리를 마이크가 되먹여서 서버 VAD가 오탐한다(`docs/architecture.md` §09, `docs/progress.md` 4단계). AEC 또는 "TTS 재생 중 마이크 입력 무시" 중 하나를 오디오 루프(현재는 `run_no_robot.py`의 `MicStreamer`/`Speaker`)에 넣어야 함.

## 로봇과 무관하게 남은 것 (우선순위 낮음)

- **facts 정리 로직 없음**: 세션을 거듭할수록 `profile_manager`의 facts가 계속 쌓이기만 한다(v2의 `batch_update_summary`가 하던 정리 작업이 v3엔 없음). 정정은 덮어쓰기라 무한정 늘어나진 않지만, 세션이 많이 쌓이면 재검토할 것.
- **`media/` 패키지가 비어있음**: 마이크/스피커 I/O는 실제로는 존재한다 — `run_no_robot.py`의 `MicStreamer`/`Speaker` 클래스가 그 역할을 한다. 다만 v2처럼 `media/audio_manager.py`로 분리되어 있진 않고 스크립트에 inline돼 있다. `launcher.py`로 승격할 때 재사용성이 필요해지면(예: batch 폴백 경로도 같이 쓰려면) 그때 `media/`로 뽑아낼 것 — 지금 당장은 불필요한 추상화라 미룸.
- **`subtitle.py`의 번들 폰트가 런타임에 자동 등록 안 됨**: `display/fonts/`에 파일은 있지만 tkinter가 시스템에 설치된 폰트만 찾는다. 미설치 시 조용히 기본 폰트로 대체(에러 아님, v2도 동일). 필요하면 폰트 파일을 수동 설치할 것.

## 완료된 통합 (참고용 — `scripts/run_no_robot.py`, 커밋 `25e8e8d`)

로봇 없이 가능한 부분은 전부 실제로 이어져 있다:

1. `vision_brain.RobotBrain`으로 웹캠 얼굴 인식(또는 미확정) — ✅
2. `profiles.load_profile_for_chat(name)` 호출(이름 확정 시) — ✅
3. `build_persona_system_instruction(name, facts_summary)` — ✅
4. `tools=[remember_fact(이름 알 때만), set_emotion]` — ✅ (`play_gesture`는 위 "로봇이 있어야" 항목 참고)
5. 오디오 in/out (`MicStreamer`/`Speaker`) — ✅
6. `message.tool_call` 수동 처리 — ✅
7. `extract_exit_tag()`로 `[대화종료]` 감지 → `report_manager.generate_and_save_reports(name, log, 최신_facts_summary)` — ✅ (facts_summary는 리포트 생성 직전에 다시 로드함 — 대화 중 새로 안 사실 반영)

모델명: batch 폴백용 `MODEL_NAME`(기본값 `gemini-3.1-flash-lite`), Live API용 `LIVE_MODEL_NAME`(기본값 `models/gemini-3.1-flash-live-preview`), `.env.example`에 기록됨. `client.models.list()`로 실제 `bidiGenerateContent` 지원 여부 재확인 가능.
