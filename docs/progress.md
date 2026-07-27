# 구현 진행 로그

`docs/architecture.md`의 로드맵(§10) 대비 실제 구현 상태를 기록한다. 설계 자체가 바뀌면 architecture.md를, 무엇을 언제 어떻게 만들었는지는 이 문서를 갱신한다.

## 6단계 — 실물 로봇 연결 및 통합 완료 (2026-07-27)

**로봇이 처음으로 실제 연결된 세션.** §10 로드맵상 "로봇 연결 대기 중"이던 항목들(Layer 1 실물 검증, `vision/face.py` 실물 검증, Layer 2, `launcher.py` 승격, 얼굴인식↔추적 통합)을 이 한 세션에서 전부 끝냈다. 사용자가 직접 로봇으로 테스트하며 진행 — 그때그때 발견된 버그를 바로 고치는 식으로 진행됨.

### Layer 1 실물 검증 및 재보정

- `scripts/test_motions.py`로 실물 매크로 검증 → 초기 위치(HOME)가 부정확해서 사용자가 손으로 자세를 다시 잡음 → `scripts/read_positions.py`(신규, 읽기 전용) 작성해 관절별 새 영점 실측 → `hardware/init.py`의 `MOTOR_HOME_POSITIONS`와 `hardware/config.py`의 모든 관절별 절대 위치 상수(READY/ACTION/TOP/MIDDLE/DOWN 등)를 관절별 델타만큼 일괄 평행이동. 단, HEAD_NOD_ID(#1)는 첫 실측값이 `-77`(부호있는 32비트, 안전범위 밖)로 나와 통신 순간오류로 의심 → 재측정해 `4022`로 확정.
- 실물 테스트로 hug 동작 시 왼팔이 살짝 과하게 올라가는 것 발견 → `LEFT_ARM_TOP_POS`를 862→902로 미세조정.
- `docs/architecture.md` §05 안전범위 표를 실측값 기준으로 전부 갱신.

### 버그 3건 발견 및 수정 (실물 테스트 중 발견)

1. **`scripts/test_vision.py` 종료 시 레이스**: `stop_event.set()` 후 얼굴추적 스레드를 `join()` 없이 바로 `port.closePort()` 호출 → 워커 스레드가 루프를 아직 못 빠져나온 상태에서 이미 닫힌 포트에 쓰다가 `'NoneType' object has no attribute 'hEvent'` 에러. `t_face.join(timeout=5.0)` 추가로 해결(이후 `launcher.py`에도 처음부터 이 순서로 작성됨).
2. **`run_no_robot.py`(당시) 카메라 기본값 불일치**: `vision/face.py`의 `face_tracker_worker`는 이미 `camera_index=1`이 기본값인데, 이걸 부르는 스크립트 3개(`run_no_robot.py`/`test_vision.py`/`test_vision_brain.py`)가 전부 인자 없을 때 `0`으로 덮어쓰고 있었음 — 로봇 카메라(1번) 대신 PC 내장캠(0번)을 보는 버그. 세 파일 다 기본값을 `1`로 수정.
3. **`run_no_robot.py`(당시) 대화가 한 턴만 진행**: `recv_loop`가 `async for message in session.receive():`를 한 번만 돎 — Gemini Live API의 `session.receive()`는 턴 하나짜리 스트림이라 턴이 끝나면 이 for문 자체가 끝나버려 `recv_loop()`가 종료됨. 그 뒤로는 마이크 오디오는 계속 보내지는데 응답을 받을 사람이 없는 상태가 되어 "한 번 하고 안 이어짐" 증상. `while not stop_event.is_set(): async for message in session.receive(): ...`로 감싸서 턴마다 `receive()`를 다시 호출하도록 수정.

### 전체 코드 재검토(사용자 요청)에서 발견해 고친 것

- **제스처 동시실행 레이스**: `core/motion_tools.py`의 `play_gesture`가 매 호출마다 새 스레드를 띄우고 즉시 리턴하는데, `hardware/motion.py`의 Layer 1 매크로(hug/greeting/wave/shy)는 재진입 방지가 전혀 없었음(dance만 자체 `_dance_running`으로 막혀있었음). `test_motions.py`는 메뉴 입력이 사람 페이스라 문제가 안 드러났지만, 실시간 대화에서 모델이 연속 턴에 제스처를 두 번 부르면 두 매크로가 같은 팔 모터에 동시에 다른 목표 위치를 써서 예측 불가능한 움직임이 날 수 있었음. `busy` 이벤트 가드 추가(추후 Layer 2와도 공유하도록 확장).
- `_REPO_ROOT` 계산이 파일 위치(scripts/ → 루트)에 종속적이었던 것, `core/suppress.py`의 로그 폴더가 cwd 기준인 것 등 경미한 항목도 점검(후자는 `python launcher.py`를 항상 루트에서 실행하는 한 문제없어 그대로 둠).

### `run_no_robot.py` → `launcher.py` 승격

`docs/integration-points.md`에 미리 적어뒀던 계획대로: 파일을 저장소 루트로 옮기고(`git mv`), `core/motion_tools.py`의 `play_gesture`와 `vision/face.py`의 `face_tracker_worker`를 실제로 대화 파이프라인에 연결. 종료 시 `t_face.join()` → `shutdown_all_motors()` → `port.closePort()` 순서를 처음부터 지키도록 작성(위 버그 1번 교훈 반영).

### 자막 기능 — 미연결 상태로 확인, 더 이상 불필요 판정

전체 기능 재점검 중 `display/subtitle.py`(v2에서 포팅됨, tkinter 자막 창)가 어디에도 연결되어 있지 않은 걸 발견(코드 전체에서 `subtitle_window_process`를 호출하는 곳이 없었음). 사용자 확인 결과 자막 기능 자체가 더 이상 필요 없다고 판단 — 연결 작업 없이 그대로 둠(`docs/integration-points.md`에 기록).

### Layer 2 — `express_gesture` 구현

`hardware/motion.py`에 `play_express_gesture(joint, intensity, speed, repeat)` 추가. 팔/고개는 "쉬는 자세 ↔ 안전범위 끝"을 intensity로 선형보간하는 한 방향 동작, 어깨는 안전범위가 중앙 기준 좌우 대칭이라 좌우로 흔드는 wiggle 동작으로 다르게 구현. 보간 양 끝이 이미 안전범위 안쪽 값이라 intensity 0~1 전 구간에서 안전범위를 못 벗어나는 구조. `core/motion_tools.py`를 `make_motion_tools()`로 재구성해 `play_gesture`(Layer 1)와 `express_gesture`(Layer 2)가 하나의 `busy` 가드를 공유하도록 함(둘이 서로 겹쳐 실행되는 것도 막힘). `test_motions.py`에 `6) express` 메뉴 추가해 관절/intensity/speed/repeat을 직접 입력하며 독립 검증 가능하게 함.

### 얼굴인식 ↔ 팬/틸트 실시간 통합

기존에는 대화 시작 전 `identify_user_via_webcam()`이 카메라를 따로 열어 한 번만 인식하고, `face_tracker_worker`는 `brain=None`으로 추적만 담당하는 두 단계 구조였음. 이걸 하나로 합쳐 `face_tracker_worker(..., brain=brain)`이 인식과 추적을 같은 스레드/카메라 세션에서 함께 하도록 변경, `wait_for_identification()`이 `shared_state['detected_user']`를 폴링. 세션 시작 시 확정된 이름은 세션 내내 고정하고 이후 다른 사람이 감지돼도 무시(사용자 결정) — `vision/face.py`에 이미 있던 `is_initial_recognition_active`(이름이 한 번 확정되면 재인식 자체를 멈추는 로직)를 그대로 활용해서 새로 구현할 필요가 없었음.

### 로봇이 먼저 인사 + 처음 보는 사람 자동 등록

사용자 요청: "launcher 실행 시 모터 켜지고 로봇이 먼저 인사, 아는 사람이면 이름 부르며 인사, 모르는 사람이면 이름을 물어보고 학습".

- **먼저 말 걸기**: Live API는 기본적으로 사용자 입력을 기다렸다가 응답하므로, 연결 직후 `session.send_client_content(turns=..., turn_complete=True)`로 "사용자가 말하기를 기다리지 말고 먼저 인사하라"는 텍스트 턴을 보내 말문을 열게 함(공식 문서 권장 패턴 — `ai.google.dev/gemini-api/docs/live-guide` 확인). `core/utils.py`의 페르소나에도 "먼저 말을 거세요" 지시를 상시 포함하도록 추가.
- **처음 보는 사람 자동 등록**: `core/memory_tools.py`의 `make_remember_fact_tool`을 `name: str`이 아니라 `name_state: dict`(예: `{"name": None}`)를 받도록 재설계. Live API는 세션 도중 tools 목록을 바꿀 수 없어서, 이름을 몰라도 `remember_fact` 툴을 처음부터 항상 붙여두고 **모델이 처음으로 `remember_fact(field="name", value=...)`를 호출하는 순간** 그 값으로 이름을 확정(+ `shared_state['detected_user']` 갱신 + 그 시점 `shared_state['current_face_embedding']`으로 `brain.register_face()` 호출)하는 "자가부트스트랩" 방식 채택. 이름 확정 전에 다른 field를 먼저 부르면 저장하지 않고 이름부터 물어보라고 안내. 이 인터페이스 변경으로 기존 테스트 스크립트 4개(`test_live_poc/persona/live_audio/memory.py`)의 호출부도 `make_remember_fact_tool(name)` → `make_remember_fact_tool({"name": name})`로 같이 수정.
- 오프라인 유닛 테스트(가짜 brain으로 `remember_fact` 시퀀스 직접 호출)로 부트스트랩 로직 자체는 사전 검증함 — 실제 Live API 붙여서 사용자가 확인, "다 너무 잘된다"로 통과.

### 에코/AEC 조사 (구현은 보류)

barge-in은 v3를 만든 핵심 이유 중 하나라 포기 불가로 확정(사용자 결정) — "TTS 재생 중 마이크 무시" 같은 회피책은 배제.

- Windows WASAPI "통신(Communications)" 카테고리로 OS 자체 AEC를 타는 방법을 조사 후 실제로 코드로 시도 → **이 환경에서 실패 확인**: `sounddevice`가 잡는 기본 입출력 장치가 WASAPI가 아니라 MME 호스트API라, 카테고리 값과 무관하게 `WasapiSettings`를 붙이기만 해도 `Incompatible host API specific stream info` 에러가 남. 이 경로 폐기.
- ChatGPT/Gemini 등 실제 서비스가 에코 없이 잘 되는 이유를 분석 — 결국 웹은 브라우저 내장 AEC3(`getUserMedia echoCancellation:true`), 데스크톱 앱은 대부분 Electron이라 크로미움의 AEC3 그대로 사용, 모바일은 OS AEC 편차가 커서 WebRTC AEC3를 직접 쓰는 경우가 많음 — **셋 다 결국 WebRTC AEC3 하나로 수렴**.
- `pip install aec-audio-processing` — Windows(Python 3.11)에서 prebuilt wheel로 깔끔히 설치 확인(컴파일 불필요), 실제 WebRTC APM(AEC3 포함)을 SWIG로 감싼 진짜 엔진. `process_reverse_stream()`이 자기가 설정한 레이트가 아니라 근단(마이크) 프레임 크기를 그대로 요구하는 함정 발견(스피커 24kHz → 마이크 16kHz로 리샘플링해서 먹여야 함). 합성 신호(원단 사인파+에코+별도 목소리 성분)로 300프레임 시뮬레이션 → 적응 필터 수렴 후 **에코 성분 약 30~36dB 감쇠** 확인 — 실사용 가능한 수준.
- 결론: `aec-audio-processing`으로 구현 가능성은 확정됐고, 실제 `launcher.py`의 `MicStreamer`/`Speaker`에 연결하는 작업만 남음(사용자가 나중으로 보류, `docs/integration-points.md`에 상세 기록).

### AEC 실제 연결 (같은 세션에 이어서 완료)

`launcher.py`에 인라인이던 `MicStreamer`/`Speaker`를 `media/audio_manager.py`(신규)로 분리하면서 `EchoCanceller` 클래스로 AEC를 실제로 연결. 마이크 콜백은 캡처 오디오를 `process_stream`에 통과시켜 정제된 오디오만 Gemini로 보내고, 스피커 콜백은 재생 중인 오디오를 24kHz→16kHz로 리샘플링해서 `process_reverse_stream`(에코 참조)에 동시 투입. `.env`에 `ENABLE_AEC`(켜기/끄기), `AEC_STREAM_DELAY_MS`(왕복지연 추정치, 기본 100ms) 추가. `requirements.txt`에 `aec-audio-processing`·`scipy` 반영.

**검증 2단계**: ①로봇 없이 실제 오디오 장치(PC 마이크/스피커)로 마이크·스피커 콜백을 2초간 동시에 구동하는 스모크 테스트 — 크래시 없음, 예상 바이트 수(64000 = 2초×16kHz×2바이트) 정확히 일치. ②**사용자가 이어폰을 빼고 실제 로봇으로 대화 — 최종 검증 성공**("성공이야! 진짜 대박이다"). 왕복지연 추정치(100ms)를 튜닝 없이 그대로 썼는데도 잘 작동함.

### 검증 상태 / 다음 단계

Layer 1 재보정, Layer 2, 얼굴인식↔추적 통합, 먼저 인사+자동등록, AEC까지 **§10 로드맵 핵심 기능 전부 사용자가 실제 로봇으로 테스트 완료 — v3 완성**. 남은 건 낮은 우선순위 항목뿐(`docs/integration-points.md` 참고 — Layer 3 폴백, ID 10 모터 정체, facts 정리 로직).

## 전체 코드베이스 리뷰 (완료, 커밋 `f4c4545`, `13257e1`)

"로봇 없이 할 수 있는 작업들"을 다 끝낸 시점에 전체를 한 번 훑어서 버그 4건을 찾아 고쳤다.

1. **`.env` 오버라이드가 프로젝트 전역에서 조용히 무시되고 있었음** — `hardware/config.py`(`DXL_PORT`/`DXL_BAUD`/`DXL_PROTO`/`BASE_RPM`/`TURN_RPM`)와 `core/report_manager.py`(`MODEL_NAME`)는 모듈이 import되는 시점에 `os.getenv(...)`를 읽는데, 모든 스크립트의 `load_dotenv()` 호출은 그 import보다 뒤(`main()`/개별 함수 안)에 있었다. 즉 `.env`를 아무리 고쳐도 하드코딩된 기본값만 항상 읽혔다. `.env`에 `BASE_RPM=99`를 넣고 실측해서 실제로 25.0(기본값)이 읽히는 걸 확인 → `bootstrap.py`(모든 진입 스크립트가 제일 먼저 import하는 파일)에서 import 시점에 `.env`를 로드하도록 옮겨서 해결, 다시 실측해서 99.0이 읽히는 것까지 확인함. **로봇을 연결해서 `DXL_PORT`를 강제 지정하려던 순간 조용히 안 먹혀서 헤맸을 버그** — 로봇 연결 전에 미리 잡힌 게 다행.
2. **`vision/vision_brain.py`의 `art_brain.pkl` 경로가 cwd 상대경로였음** — v3의 다른 모든 파일(`profile_manager.py`, `report_manager.py`, `face.py`의 모델 경로 등)은 전부 `__file__` 기준 절대경로로 고쳐뒀는데 이것만 v2 그대로 남아있었다. 실행 위치가 바뀌면 엉뚱한 곳에 얼굴 데이터를 읽고 쓸 뻔함 — 절대경로로 통일.
3. **`scripts/run_no_robot.py`의 세션종료 리포트가 대화 시작 전 facts 스냅샷을 재사용**하고 있었음 — 대화 중 `remember_fact`로 새로 알게 된 사실이 결과지에 반영이 안 되는 상태였다. 리포트 생성 직전에 `profiles.load_profile_for_chat(name)`을 다시 호출하도록 수정.
4. `core/motion_tools.py` 독스트링이 존재하지 않는 `docs/progress.md §09`를 인용하고 있었음(실제로는 `docs/architecture.md §09`) — 인용 수정.

**의도적으로 안 고친 것**: `core/utils.py`의 `_get_env` 헬퍼가 정의만 되고 어디서도 안 쓰인다 — v2 원본에도 이미 있던 죽은 코드라 v3에서 새로 생긴 문제가 아니고, 무해해서 그대로 둠.

`docs/architecture.md` §09/§10도 이 시점에 최신 상태로 갱신함(barge-in 사람 테스트 완료 반영, 스피커→마이크 에코 오탐을 신규 오픈 이슈로 추가, §10에 "로봇 없이 가능한 나머지 조각" 5단계 추가).

## 로봇 없이 할 수 있는 작업들 (완료)

로봇이 당장 연결 안 된 상태라, 로봇/모터가 필요 없는 작업부터 먼저 진행하기로 함. 순서: display/ → vision_brain.py → report_manager.py → motion_tools.py → `[대화종료]` 처리 → 통합 데모(run_no_robot.py). §10 로드맵상 모터 불필요 작업은 이걸로 전부 소진됨 — 남은 건 전부 로봇 연결이 전제.

### display/ 포팅 (완료, 커밋 `5c17dd7`)

| 파일 | 내용 |
|---|---|
| `display/main.py` | `RobotFaceApp` — pygame 기반 얼굴 표정 렌더러. `emotion_queue`에서 명령을 꺼내 `change_emotion`으로 반영 |
| `display/common_helpers.py`, `emotions/*.py`(12종: neutral/happy/excited/tender/scared/angry/sad/surprised/listening/thinking/close/scanning), `emotions/eyebrow.py`, `emotions/cheeks.py` | v2에서 변경 없이 그대로 포팅 — 전부 `..common_helpers`만 참조하는 자기완결형 모듈이라 하드웨어 의존성 없음. v2에 있던 `sleepy.py`/`wake.py`는 v2 main.py도 안 쓰길래 v3에도 안 가져옴 |
| `display/subtitle.py` | tkinter 자막 창(별도 프로세스). 폰트는 `display/fonts/`에 파일만 번들되어 있고 런타임에 자동 등록되진 않음 — v2도 마찬가지였음(시스템에 폰트 미설치 시 tkinter가 조용히 기본 폰트로 대체, 에러 아님) |
| `scripts/test_display.py` | 콘솔에 감정 이름을 입력하면 emotion_queue로 밀어넣는 독립 테스트 도구. 로봇도 카메라도 불필요 |

**검증**: 자동 종료되는 스모크 테스트(HAPPY→SAD→SCANNING 순서로 큐에 명령 넣고 3초 후 종료)를 실제로 실행해 통과 확인함 — 실제 pygame 창이 뜨고 감정 전환이 정상 동작. `core/emotion_tools.py`는 수정 없이 그대로 연결됨(애초에 `emotion_queue` 파라미터로 설계돼 있었음).

### vision/vision_brain.py 포팅 (완료, 커밋 `7eeb201`)

| 파일 | 내용 |
|---|---|
| `vision/vision_brain.py` | `FuzzyART`(패턴 기억) + `RobotBrain`(insightface `buffalo_l` 얼굴 임베딩 추출 + 인식/등록) — v2에서 변경 없이 포팅. `recognize_face(frame)`/`register_face(embedding, name)` 인터페이스가 `vision/face.py`의 `brain=` 파라미터가 기대하는 것과 이미 정확히 일치해서 face.py 쪽 수정 불필요 |
| `scripts/test_vision_brain.py` | 웹캠으로 실시간 인식 미리보기 + `r` 키로 현재 얼굴 등록하는 독립 테스트 도구. 로봇 불필요 |

**검증**: `buffalo_l` 모델이 v2 사용 이력 덕분에 `~/.insightface/models/`에 이미 캐시되어 있어 재다운로드 없이 바로 로딩됨. `RobotBrain()` 실제 초기화 + 빈 프레임에 `recognize_face()` 호출까지 실행해 `(None, None)`이 정상 반환되는 것 확인. `art_brain.pkl`이 v3 루트에 아직 없어 "기억된 얼굴 수: 0"으로 시작 — v2의 학습된 얼굴 데이터와 분리된 깨끗한 상태(의도된 동작, v2 파일을 갖고 오지 않음).

### report_manager.py 포팅 (완료, 커밋 `ddeef9e`)

v2는 학년/전공/RC/MBTI 고정 슬롯(`user_info` dict)을 받았는데, v3는 슬롯이 없으므로(§04) `profile_manager.load_profile_for_chat(name)`이 만드는 자유형 facts_summary 문자열을 그대로 프롬프트에 넣는 것으로 교체. 그 외(마크다운 양식, batch Gemini 호출, `vitals_data`는 여전히 수동 입력값이라 launcher.py가 콘솔로 물어봐야 함) 변경 없음. `scripts/test_report.py`로 실제 Gemini 호출까지 실행해 대화록·결과지 파일 둘 다 정상 생성 확인함(내용 품질도 육안 확인 — 팀플 예시로 자연스러운 위로 편지 생성됨).

### core/motion_tools.py 추가 (완료, 커밋 `05bf957`)

`memory_tools.py`/`emotion_tools.py`와 같은 클로저 패턴으로 `make_play_motion_tool(...)` → `play_gesture(name)` 툴 작성. `play_manual_motion`이 dance 제외 나머지 매크로를 블로킹으로 실행하는데, Live 세션의 tool_call 처리가 동기적이라 그대로 두면 그동안 오디오가 멎는다 — 그래서 항상 백그라운드 스레드로 실행을 넘기고 툴 자체는 즉시 "시작했다"고만 응답하도록 설계. 로봇 없이 검증 가능한 부분(잘못된 제스처 이름이 하드웨어 접근 전에 걸러지는지, `port=None`으로도 안 죽는지)만 확인함 — 실제 모터 동작은 로봇 연결 후 검증 필요.

### extract_exit_tag() 추가 (완료, 커밋 `941704f`)

`core/utils.py`에 `EXIT_TAG = "[대화종료]"`와 `extract_exit_tag(text) -> (cleaned_text, should_end)` 추가. 페르소나 시스템 인스트럭션은 처음부터 이 태그를 내보내라고 지시해왔지만 소비하는 코드가 없었음(integration-points.md). 스트리밍 청크가 아니라 한 턴이 끝난 뒤 누적 텍스트에 대해 호출하는 계약 — 청크 경계에서 태그가 잘리는 문제는 launcher.py(Cognition 루프)가 다룰 몫으로 남겨둠. 태그 있음/없음/trailing whitespace 3가지 케이스 inline assertion으로 검증.

### scripts/run_no_robot.py — 통합 데모 (완료, 커밋 `25e8e8d`)

로봇 없이 포팅한 모든 조각을 실제로 이어붙인 하나의 실행 가능한 스크립트. `launcher.py`는 아직 없음 — 이 스크립트가 로봇 연결 후 그 뼈대가 될 예정(모터 관련해서 `vision/face.py`의 `face_tracker_worker`와 `core/motion_tools.py`만 추가하면 됨).

흐름: 웹캠으로 얼굴 인식 시도(최대 8초) → 인식되면 `profile_manager`에서 기존 facts 로드 → `build_persona_system_instruction`으로 페르소나 조립 → Live API 세션 시작(`remember_fact`는 이름을 알 때만 붙임, `set_emotion`은 항상) → `display/`로 표정 렌더링 → 매 턴마다 `extract_exit_tag`로 종료 감지 → 종료되면(정상이든 Ctrl+C든) 이름을 아는 경우 `report_manager`로 결과지 생성.

**알려진 제한(의도된 범위)**: 얼굴을 못 알아보면 이번 세션은 `remember_fact` 툴 자체를 안 붙인다 — 이름 없이는 무엇의 프로필에 저장할지 알 수 없기 때문(얼굴을 미리 등록하려면 `scripts/test_vision_brain.py`의 'r' 키 사용). vitals_data(심박수 등)는 v2도 원래 수동 콘솔 입력이었는데, 이 스크립트에선 아예 안 물어보고 `None`으로 넘김 — 로드맵 범위 밖.

**검증 상태**: 비대화 구간(startup)은 unbuffered stdout으로 실제 실행해 끝까지 확인함 — RobotBrain 로딩 → 웹캠 인식 8초 타임아웃 후 정상적으로 "이름 모른 채 진행" 분기 → remember_fact 미부착 확인 → Live API 세션 연결 성공까지 전부 정상. **실제 음성 대화 자체(마이크에 말하고 응답 듣기)는 사람이 직접 해봐야 함** — 이 세션에선 자동화된 스모크 테스트만 가능했음.

### 다음 단계

사용자가 `python scripts/run_no_robot.py`로 실제 대화(마이크+웹캠+표정 UI)를 해보고 결과 확인. 그 다음은 로봇 연결을 기다리는 항목들(Layer 2 파라미터 제스처, `vision/face.py` 실제 추적 검증, `run_no_robot.py`를 `launcher.py`로 승격) — §10 로드맵상 로봇 없이 할 수 있는 작업은 이걸로 전부 소진됨.

## 5단계 — vision/face.py 포팅 (완료, 커밋 `4bf8e46`)

### 만든 것

| 파일 | 내용 |
|---|---|
| `vision/face.py` | v2에서 포팅. `face_tracker_worker`(팬/틸트 PID 추적, brain 있으면 얼굴인식 연동) + `display_loop_main_thread`(모니터 배치). **입모양(jawOpen) 커스텀 VAD 관련 코드 전부 제거** — `mouth_event_queue` 파라미터, `MOUTH_OPEN_THRESHOLD`/`SPEAKING_TIMEOUT_SEC` 로직, `output_face_blendshapes=True` 전부 삭제(추적에 안 쓰므로 `False`로 꺼서 연산도 줄임). 모델 파일 경로도 cwd 상대경로 대신 `__file__` 기준 절대경로로 바꿔 실행 위치에 안 흔들리게 함 |
| `core/suppress.py` | v2 그대로 포팅(cv2/mediapipe 로그 억제 유틸, 변경 없음) |
| `scripts/test_vision.py` | `test_motions.py`와 같은 패턴의 독립 테스트 도구. 로봇+카메라 연결 후 실행하면 얼굴 추적만 단독 검증 가능 |
| `models/face_landmarker.task` | v2에서 복사(바이너리, `.gitignore`의 `models/*.task` 규칙으로 커밋 대상 아님 — 새 환경에서는 v2에서 다시 복사해와야 함) |

### 결정 근거

4단계에서 barge-in이 서버 VAD로 실제 검증됐으므로([[project-moti-v3-design]] 참고), v1/v2가 입모양으로 녹음 시작/종료를 트리거하던 이유 자체가 사라짐 — `vision/face.py`는 이제 팬/틸트 제어(+선택적 얼굴인식)만 담당.

### 검증 상태

`python -c "import vision.face, scripts.test_vision"`로 임포트만 확인(로봇·카메라 미연결). **실제 추적 동작(카메라+서보)은 검증되지 않음** — 사용자가 로봇 연결 후 `python scripts/test_vision.py`로 확인 예정.

### 의도적으로 범위 밖에 둔 것

- `vision/vision_brain.py`(얼굴인식, insightface+FuzzyART, `art_brain.pkl`)는 이번 단계에 포함 안 함 — `face_tracker_worker(brain=None)`으로 추적만 우선 동작. 다음 단계 후보.
- `profile_manager.load_profile_for_chat(name)`과의 연결(art_brain이 이름 확정 → 페르소나 시스템 인스트럭션에 반영)도 vision_brain 포팅 이후 과제 — `docs/integration-points.md` 참고.

### 다음 단계

vision_brain.py 포팅 여부 결정, 또는 display/ 포팅으로 먼저 전환(emotion_tools의 콘솔 로그를 실제 표정 UI로 연결).

## 4단계 — Live API PoC (완료, barge-in 실사용 검증까지 완료 — 커밋 `9561e21`, `2292338`)

### 사용한 SDK

`google-generativeai`(구, batch 전용)가 아니라 **`google-genai`(신규 통합 SDK, 1.61.0)**를 써야 Live API(`client.aio.live.connect`)에 접근할 수 있다. `client.models.list()`로 실제 `bidiGenerateContent`를 지원하는 모델을 확인: `models/gemini-3.1-flash-live-preview`, `models/gemini-3.5-live-translate-preview`.

### 만든 것과 실행 결과

| 파일 | 내용 | 검증 상태 |
|---|---|---|
| `scripts/test_live_poc.py` | 텍스트 2턴 대화를 Live 세션으로 실행, `remember_fact`+`set_emotion` 함께 부착 | **실행해서 통과함**: 연결 0.66초, 첫 응답 지연 0.49초/0.64초, 두 툴 모두 정상 호출 |
| `scripts/test_live_audio.py` | 실제 마이크 입력(sounddevice) → Live 세션 → 스피커 출력, `interrupted` 감지 시 재생 큐 비우기 | **배선만 검증(12초 무오류 스모크 테스트)** — 실제 끼어들기 체감은 사람이 직접 실행해야 함 |

### 핵심 발견

1. **모델이 TEXT 단독 출력을 거부함**: `response_modalities=[TEXT]`로 연결 시도하면 서버가 "지원하지 않는 조합"이라며 연결을 끊는다. 이 모델은 오디오 출력이 기본이라, 텍스트 확인용으로는 `output_audio_transcription=AudioTranscriptionConfig()`를 켜고 `server_content.output_transcription.text`를 읽어야 한다.
2. **function calling은 수동 처리**: batch 챗의 `enable_automatic_function_calling=True` 같은 자동 실행 기능이 Live 세션에는 없다. `message.tool_call.function_calls`를 직접 순회해 함수를 실행하고, `session.send_tool_response(function_responses=[...])`로 결과를 보내야 한다. (§09에서 "동기 방식뿐"이라 예상했던 것과 일치)
3. **barge-in은 서버가 알아서 처리**: `LiveServerContent.interrupted: bool` 필드가 존재하고, `RealtimeInputConfig.automatic_activity_detection`이 서버 측 VAD를 자동 수행한다. 즉 **v1/v2가 썼던 입모양(jawOpen blendshape) 기반 커스텀 VAD가 v3에서는 필요 없어질 가능성이 높다** — 이건 아키텍처 문서에 없던 추가 단순화 기회다.
4. `message.data`라는 편의 프로퍼티가 응답의 오디오 파트를 통째로 이어붙여 반환해줘서, 재생 코드가 파트를 직접 순회할 필요가 없었다.

### barge-in 실사용 검증 결과 (완료)

`python scripts/test_live_audio.py`를 사용자가 직접 실행해 확인함. 첫 시도(스피커+마이크, 이어폰 없음)에서 **가만히 듣고만 있었는데도 `interrupted=True`가 떴다** — 원인 규명을 위해 이어폰을 끼고 재시도하자 문제없이 정상 동작(가만히 있을 땐 안 뜨고, 실제로 말을 걸었을 때만 뜸). 즉 첫 시도의 오탐은 barge-in 로직 결함이 아니라 **스피커 소리를 마이크가 되먹임(echo)해서 서버 VAD가 "사용자가 말했다"고 오인식한 것** — 헤드리스 환경에서 헤드폰 없이 스피커+마이크를 물리적으로 가까이 두면 재현되는 전형적인 음향 피드백 문제다.

**실제 로봇에도 적용되는 시사점**: 로봇도 마이크와 스피커가 한 몸체에 붙어 있으므로 이어폰이라는 회피책을 쓸 수 없다. `launcher.py`의 오디오 루프를 만들 때 AEC(음향 에코 캔슬레이션)나 "TTS 재생 중 마이크 입력을 일시적으로 무시" 같은 처리가 필요할 가능성이 높음 — 아직 미해결, `docs/integration-points.md`에 추가 예정.

- Live API/TTS 둘 다 preview 상태라 가격·요청 한도는 여전히 미확인

### 다음 단계

barge-in 검증이 끝났으므로 §10 5단계(vision/face.py 포팅, 진행 중) 또는 Layer 2 파라미터 제스처로 진행. 스피커/마이크 에코 문제는 launcher.py 작업 시점에 별도로 다룰 것.

## 3단계 — 페르소나 시스템 인스트럭션 재작성 (완료, 커밋 `cfc05b3`)

### 만든 것

| 파일 | 내용 |
|---|---|
| `core/utils.py` | v2의 STAGES/`build_opening_prompt`/`build_extract_prompt`/`build_retry_prompt`/`build_next_prompt`/`build_hidden_first_prompt`를 전부 제거. `build_persona_system_instruction(name, facts_summary)` 하나로 첫 만남과 재회를 동일하게 처리 — 차이는 facts_summary에 뭐가 들어있는지뿐. 한동대 용어사전·학사 캘린더 인지·MBTI 톤 분기·상담 툴킷·동아리 리스트 등 도메인 지식은 v2에서 거의 그대로 이식(이건 인터뷰 구조와 무관한 내용이었음). §9 정체성 고정 규칙은 "학년을 안다면"처럼 조건부로 수정 |
| `core/emotion_tools.py` | `make_set_emotion_tool(emotion_queue=None)` — v1/v2의 `[EMOTION]태그[/EMOTION]` 정규식 파싱을 없애고 function calling으로 대체(architecture.md §06). display/가 없어서 지금은 콘솔 로그만 남김 |
| `scripts/test_persona.py` | 1부: 프롬프트 조립(이름 있음/없음) 검증, API 키 불필요 — 실행 확인 완료. 2부: 실제 Gemini에 remember_fact+set_emotion을 함께 쥐어주고 2턴 대화 — **실제로 GOOGLE_API_KEY가 설정되어 있어 실행됨** |

### 검증 상태 (실제 실행 결과)

1부, 2부 모두 이번 세션에서 실행되어 통과함. 2부 실제 대화 로그가 설계 의도를 그대로 보여줌:
- 1턴: "혹시 어떻게 불러드리면 좋을까요? 대화하면서 사용자님에 대해 조금씩 더 알아가고 싶어요!" — 강요 없이 자연스럽게 이름을 물어봄 (인터뷰 질문지 아님), `set_emotion("happy")` 호출
- 2턴: 사용자가 이름과 함께 "힘든 일이 있었다"고 하자, 남은 슬롯(학년/전공/MBTI)을 캐묻지 않고 감정에 먼저 반응, `remember_fact(field="name", value="김한동")`를 스스로 호출해 저장, `set_emotion("sad")`로 전환

즉 "능동형 호기심, 슬롯 강제 없음" 설계가 실제 모델 행동으로 확인됨.

### 의도적으로 범위 밖에 둔 것

- `play_manual_motion`(Layer 1, 이미 구현됨)을 시스템 인스트럭션/툴 목록에 아직 연결하지 않음 — 제스처 트리거링은 Live API의 인터럽션 동작 검증(§10 4단계)과 함께 다루는 게 안전하다고 판단
- display/가 없어 `set_emotion`은 로그만 남기고 실제 표정 반영은 안 됨 — display 포팅 시 emotion_queue만 연결하면 됨

### 다음 단계

§10 4단계 — Live API PoC (barge-in·지연시간·function calling 안정성 검증). 로봇 없이도 가능한 부분(연결, 툴 호출 안정성)과 실물이 필요한 부분(끼어들기 체감)을 나눠서 진행 필요.

## 2단계 — 메모리 계층 전환 (완료, 커밋 `41a4f6c`)

### 만든 것

| 파일 | 내용 |
|---|---|
| `bootstrap.py` | 계층에 속하지 않는 최상위 유틸리티. cp949 콘솔 크래시 방지(`ensure_utf8_console`)를 `hardware/config.py`에서 분리해 여기로 옮김 — hardware를 안 쓰는 스크립트(`test_memory.py`)에서도 같은 문제가 재현돼서 발견함 |
| `core/profile_manager.py` | `user_profiles.json` 저장소. `{이름: {facts: [{field, value, confidence, updated_at}], created_at, last_seen}}` 스키마. 같은 field로 재호출하면 추가가 아니라 갱신(정정) |
| `core/memory_tools.py` | `make_remember_fact_tool(name)` — Gemini function calling에 넘길 클로저를 만든다. 세션당 한 번 이름을 바인딩하므로 모델이 매번 "누구 얘기인지" 지정할 필요가 없다 |
| `scripts/test_memory.py` | 1부: 저장소 단독 검증(정정 동작, 존재 여부 조회) — API 키 불필요, 실행 확인 완료. 2부: `GOOGLE_API_KEY` 있으면 실제 Gemini에 툴을 쥐어주고 스스로 호출하는지 검증(batch function calling, Live API 아님) |
| `.env.example` | `GOOGLE_API_KEY`, `MODEL_NAME`, `DXL_PORT` 등 지금까지 코드에 등장한 환경변수 정리 |

### 검증 상태

1부(저장소 단독)는 이번 세션에서 실제로 실행해 통과 확인함 — 최초 저장, 정정(덮어쓰기, 중복 아님), 존재 여부 조회, cleanup까지 전부 정상 동작. 2부(실 Gemini 연동)는 이 환경에 `GOOGLE_API_KEY`가 없어 스킵됨 — 사용자가 `.env` 채운 뒤 실행 확인 필요.

### 다음 단계

§10 3단계 — 페르소나 시스템 인스트럭션 재작성(`core/utils.py`, STAGES 기반 프롬프트 제거하고 능동형 호기심 페르소나로). 아직 시작 안 함.

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
