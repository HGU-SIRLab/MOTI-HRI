# 구현 진행 로그

`docs/architecture.md`의 로드맵(§10) 대비 실제 구현 상태를 기록한다. 설계 자체가 바뀌면 architecture.md를, 무엇을 언제 어떻게 만들었는지는 이 문서를 갱신한다.

## 로봇 없이 할 수 있는 작업들 (진행 중)

로봇이 당장 연결 안 된 상태라, 로봇/모터가 필요 없는 작업부터 먼저 진행하기로 함. 순서: display/ → vision_brain.py → profile_manager 연결 → report_manager.py → `[대화종료]` 처리 → launcher.py 뼈대. 모터가 필요한 나머지(Layer 2 제스처, launcher.py의 모션 연결)는 로봇 연결 후로 미룸.

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

### 다음 단계

`core/profile_manager.py`와 연결 — art_brain이 이름을 확정하는 지점에서 `profiles.load_profile_for_chat(name)`을 호출해 `build_persona_system_instruction`에 넘기는 코드 작성.

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
