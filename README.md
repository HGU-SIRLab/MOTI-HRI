# 모티(Moti) v3

한동대학교 공감서비스로봇 **모티** — v1([hlri-iua-motirobotics](https://github.com/HandongSF/hlri-iua-motirobotics))의 모션/제스처 자산과 v2([Empathy-service-motirobot](https://github.com/HGU-SIRLab/Empathy-service-motirobot))의 대화 설계를 통합한 3번째 버전. Gemini Live API 기반 실시간 음성 대화, 얼굴인식/추적, 물리 제스처, 감정 표정 UI를 하나의 인지 루프(`launcher.py`)로 연결하고, 그 위에 하찮미 실험용 퀴즈 모드(N=30, 3조건 비교)를 얹었다.

설계 배경과 아키텍처 전체는 [`docs/architecture.md`](docs/architecture.md), 구현 이력은 [`docs/progress.md`](docs/progress.md), 실험 측정 설계는 [`docs/experiment_design.md`](docs/experiment_design.md) 참고.

## 주요 기능

- **실시간 음성 대화** — Gemini Live API(WebSocket)로 마이크→서버→스피커 스트리밍. 서버 측 자동 VAD가 barge-in(로봇 말 끊고 끼어들기)까지 처리한다.
- **로봇이 먼저 인사** — 연결 직후 히든 텍스트 턴으로 말문을 열어, 사용자가 입을 떼기 전에 로봇이 먼저 말을 건다.
- **얼굴인식 + 자동 등록** — InsightFace 임베딩 + FuzzyART(`art_brain.pkl`). 처음 보는 사람이면 이름을 물어보고, 대화 중 `remember_fact(field="name", ...)`가 처음 호출되는 순간 프로필 생성과 얼굴 등록을 함께 처리한다(자가부트스트랩 — 사전 등록 불필요).
- **팬/틸트 얼굴 추적** — mediapipe FaceLandmarker + PID 제어. 인식과 추적이 같은 스레드/카메라 세션에서 함께 돈다.
- **장기 기억** — `remember_fact`로 자유형 key-value 사실 누적(`user_profiles.json`), 세션 종료 시 LLM으로 유사 항목 병합/압축. `forget_me` 툴로 프로필+얼굴을 함께 삭제(GDPR식 잊혀질 권리).
- **물리 제스처 2계층** — Layer 1(이름 있는 매크로: greeting/wave/hug/shy/dance), Layer 2(파라미터 제스처: 관절·강도·속도·횟수만 LLM이 정하고 좌표는 코드가 안전범위 내로 매핑). 하나의 busy 게이트를 공유해 동시 실행 충돌을 막는다.
- **감정 표정 UI** — pygame 얼굴(`display/main.py`)을 `set_emotion` 툴로 제어(14종 표정).
- **idle-sleep** — 40초간 아무 활동이 없으면 SLEEPY 표정 + 코골이 배경음 + 추적 정지, 사용자가 말하면 즉시 깨어남.
- **목소리 후처리** — Zephyr 프리셋 + pyworld 피치/포먼트 시프트(+3.5반음/×1.12)로 더 앳된 톤. 청크 경계는 오버랩-크로스페이드로 이어붙여 끊김 없음.
- **에코캔슬레이션(AEC)** — WebRTC AEC3로 스피커→마이크 되먹임 제거. 이어폰 없이 로봇 스피커로 대화해도 barge-in 오탐이 없다.
- **세션 산출물** — 종료 시 전체 대화록 + LLM이 쓰는 "마음 처방전" 결과지 + (퀴즈를 했다면) 모드별 연구 데이터 JSON을 `user_result/`에 저장.
- **퀴즈 실험 모드** — 부분 확대 사진 퀴즈, 3가지 로봇 성격(척척박사/하찮미/짜증유발)을 한 세션 안에서 라운드별로 진행. 아래 [퀴즈 실험 모드](#퀴즈-실험-모드-하찮미-실험) 참고.

## 빠른 시작

```bash
# 1. 의존성 설치 (Python 3.10+)
pip install -r requirements.txt

# 2. 설정
#    .env.example을 .env로 복사하고 GOOGLE_API_KEY 등을 채운다
copy .env.example .env

# 3. 실행 (로봇/카메라/마이크 연결 상태에서)
python launcher.py            # 카메라 인덱스 기본 0
python launcher.py 1          # 다른 카메라를 쓸 때
```

**모델 파일**: `models/face_landmarker.task`(mediapipe 공식 배포본)는 용량 문제로 git에 없다 — 없으면 얼굴추적이 비활성화되니 직접 받아 넣을 것. InsightFace `buffalo_l`은 첫 실행 시 `~/.insightface/`에 자동 다운로드된다. SLEEPY 코골이 클립(`assets/audio/snore.wav`)이 없으면 `python scripts/generate_snore_audio.py`로 한 번 생성한다(없어도 경고만 하고 정상 동작).

**종료**: 사용자가 작별 인사를 하면 모델이 `[대화종료]` 태그를 내보내 자연 종료된다. Ctrl+C로 강제 종료해도 그때까지의 대화록/퀴즈 결과는 저장된다(31단계).

## launcher.py가 하는 일 (부팅 시퀀스)

1. Dynamixel 포트 열기 → 전 모터 초기 위치로 이동
2. RobotBrain(InsightFace+FuzzyART) 로딩
3. 얼굴추적 스레드 시작(인식+팬/틸트, 같은 카메라 세션)
4. 최대 8초간 얼굴인식 대기 → 아는 사람이면 프로필 로드, 모르면 이름 없이 진행
5. 표정 UI 스레드 + 퀴즈 사진 창 프로세스 시작
6. Gemini Live 세션 연결(툴 9종 장착) → 로봇이 먼저 인사 → 대화 루프
7. 종료 시: 모터 토크 OFF → facts 정리 → 대화록/결과지/퀴즈 결과 저장

## 하드웨어 구성

Dynamixel 모터(프로토콜 2.0, 기본 57600bps). 포트는 `.env`의 `DXL_PORT`가 비어있으면 U2D2/FTDI 계열을 자동 탐색한다.

| ID | 관절 | 용도 |
|----|------|------|
| 1  | HEAD_NOD | 고개 끄덕임 |
| 2  | PAN | 고개 좌우(얼굴 추적) |
| 3  | RIGHT(바퀴) | 우측 바퀴(속도 제어) |
| 4  | LEFT(바퀴) | 좌측 바퀴(속도 제어) |
| 5  | SHOULDER | 어깨(춤/으쓱) |
| 6  | AUX | 보조(초기화만) |
| 7  | RIGHT_ARM | 오른팔 |
| 8  | RIGHT_HAND | 오른손 |
| 9  | TILT | 고개 상하(얼굴 추적) |
| 10 | (미사용) | 정체 불명이지만 초기화/종료 대상에 포함 |
| 11 | LEFT_ARM | 왼팔 |
| 12 | LEFT_HAND | 왼손 |

관절별 안전범위/홈 위치 실측값은 `hardware/config.py`·`hardware/init.py`에 상수로 있다(2026-07-27 실물 재보정). 자세를 다시 잡아야 하면 토크 OFF 상태에서 손으로 맞춘 뒤 `python scripts/read_positions.py`로 실측해 상수를 갱신한다.

그 외: 웹캠 1대(로봇 머리), 마이크+스피커(AEC 덕에 이어폰 불필요), 표정용 모니터 + 퀴즈 사진용 모니터(멀티 모니터 배치는 env로 조정).

## 모듈 구조

```
launcher.py    진입점 — 전체 인지 루프(위 부팅 시퀀스)
bootstrap.py   계층 무관 최상위 유틸: .env 로드(임포트 시점), UTF-8 콘솔 강제

core/          대화·상태 로직 (하드웨어를 모름)
  utils.py            페르소나 시스템 인스트럭션(한동대 문화 지식 포함), [대화종료] 태그
  profile_manager.py  user_profiles.json 저장소(원자적 쓰기), facts 병합/압축
  memory_tools.py     remember_fact / forget_me 툴
  emotion_tools.py    set_emotion 툴(짜증유발 모드 중 표정 클램프 포함)
  motion_tools.py     play_gesture / express_gesture 툴(백그라운드 스레드 + busy 가드)
  report_manager.py   세션 종료 시 대화록 + "마음 처방전" 결과지 생성
  idle_watcher.py     idle-sleep 판단(순수 함수)
  quiz_bank.py        퀴즈 문제 은행 로드 + 정답 판정(퍼지 매칭, 포기 마커)
  quiz_state.py       퀴즈 상태 기계(모드별 분기 전부 여기, 순수 로직)
  quiz_tools.py       퀴즈 Gemini 툴 5종 + 지연 주입/정답 공개 타이밍(asyncio)
  quiz_export.py      퀴즈 결과를 모드별 JSON으로 저장

hardware/      모터 I/O
  config.py           포트/모터 ID/안전범위 상수 (env 오버라이드 가능)
  init.py             전 모터 초기화 / 안전 종료(토크 OFF)
  dxl_io.py           Dynamixel 읽기/쓰기 헬퍼
  motion.py           Layer 1 매크로(hug/greeting/shy/dance) + Layer 2 프리미티브 + 퀴즈 리액션 모션
  wheel.py            바퀴 속도 제어

vision/        카메라
  face.py             얼굴추적 워커(mediapipe + PID 팬/틸트, 인식 연동)
  vision_brain.py     RobotBrain — InsightFace 임베딩 + FuzzyART 인식/등록/삭제(스레드 안전)

display/       화면
  main.py             pygame 표정 UI(RobotFaceApp, 14종 감정 + 깜빡임)
  emotions/           감정별 그리기 모듈
  quiz_window.py      퀴즈 사진 전체화면 창(별도 프로세스 + Tkinter)

media/         오디오
  audio_manager.py    MicStreamer/Speaker + EchoCanceller(WebRTC AEC3), 언더런 진단
  voice_shift.py      pyworld 피치/포먼트 시프트 + 오버랩-크로스페이드 스트리밍 처리

scripts/       테스트/도구 (아래 표)
docs/          설계·이력·실험 문서
assets/        퀴즈 사진(questions.json + 크롭/원본), 코골이 클립, 춤 음악
```

### 계층 원칙

`core/`는 하드웨어를 모르고, `hardware/`는 대화를 모른다. 순수 로직(`quiz_state.py`, `quiz_bank.py`, `idle_watcher.py`)은 asyncio/모터/UI 없이 단독 테스트 가능하게 분리되어 있다 — `scripts/test_*.py`가 전부 API 키/로봇 없이 도는 이유.

### Live API 관련 핵심 제약 (다시 만질 때 주의)

- `system_instruction`/`tools`는 **연결 시점에 한 번만 고정**된다 — 세션 도중 못 바꾼다. 그래서 모드별 행동 차이는 고정된 툴의 **반환값**을 상태 기계가 런타임에 다르게 만드는 방식으로 구현되어 있다(`quiz_state.py`, `memory_tools.py`의 name_state 패턴).
- `session.receive()`는 **턴 하나짜리 스트림** — 계속 받으려면 `while` 루프로 다시 호출해야 한다.
- 툴 호출 처리는 동기적 — 블로킹 작업(모터 매크로 등)은 반드시 백그라운드 스레드로 넘긴다.
- 히든 턴 주입(`inject_turn`)은 로봇이 말하는 중이면 안 된다 — `speaking_done` 이벤트 + 버퍼 드레인 대기가 이미 구현되어 있으니 새 주입 경로를 만들면 반드시 `inject_turn()`을 거칠 것.

## 퀴즈 실험 모드 (하찮미 실험)

사물 일부를 확대한 사진을 보고 무엇인지 맞히는 게임. 사용자가 "심심해"/"퀴즈 풀자"라고 하면 시작되고, 실험자가 알려준 번호를 참가자가 말하면 모드가 정해진다:

| 모드 | 이름 | 행동 |
|------|------|------|
| 1번 `all_knowing` | 척척박사 | 정답을 처음부터 알고, 오답이면 짧고 사무적으로 즉시 공개 |
| 2번 `imperfect` | 하찮미 | 정답을 모름 — 사용자가 답하면 "저도 맞춰볼게요!"로 자기 추측을 말한 뒤 나란히 비교 공개 |
| 3번 `annoying` | 짜증유발 | 무엇을 답하든 10~12초 뜸들인 뒤 "저는 AI 로봇이라 그런 답변은 할 수 없습니다"로 매번 거절. 참가자가 명시적으로 포기("정답 알려줘"/"넘어가줘")해야만 정답 공개 |

- 한 참가자가 **로봇을 끄지 않고 한 세션 안에서** 3개 모드를 연속 진행한다. 라운드마다 겹치지 않는 5문항이 자동 배분된다(문제 은행 15문항 = 정확히 3라운드 분량).
- 이미 진행한 모드를 다시 고르면 로봇이 한 번 되물어 확인받는다(말실수/STT 오인식 방지).
- 세션 종료 시 `user_result/{날짜}_{시각}_{이름}_quiz/` 아래 모드별 JSON으로 저장된다. 문항별 필드: 정오답, 포기 여부, 힌트 요청, **소요시간(`elapsed_sec`)**, **거절 횟수(`annoying_refusals`, 짜증유발 전용)**, 타임스탬프.
- **세션이 도중에 크래시하면**: `.env`에 `QUIZ_ROUND_OFFSET=<이미 마친 라운드 수>`를 넣고 재시작하면 이미 공개된 사진을 건너뛴다. **실험 후 반드시 지울 것.**

운영 절차·주의사항 전체는 [`docs/experiment_design.md`](docs/experiment_design.md)의 "실험 운영 시 알아둘 것" 참고. 문제 추가/사진 교체는 `scripts/crop_quiz_photo.py`(크롭) → `scripts/build_quiz_bank.py`(등록) 순서로 한다.

## 환경 변수 (.env)

`.env.example`을 복사해서 채운다. `bootstrap.py`가 임포트 시점에 로드하므로 어느 진입점에서든 적용된다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `GOOGLE_API_KEY` | (필수) | Gemini API 키 |
| `LIVE_MODEL_NAME` | `models/gemini-3.1-flash-live-preview` | 실시간 대화용 Live 모델 |
| `MODEL_NAME` | `gemini-3.1-flash-lite` | 결과지/facts 정리 등 batch 호출용 모델 |
| `LIVE_VOICE_NAME` | `Zephyr` | Live API 프리셋 목소리 |
| `DXL_PORT` | (자동 탐색) | Dynamixel 시리얼 포트(예: COM3) |
| `DXL_BAUD` / `DXL_PROTO` | `57600` / `2.0` | 통신 설정 |
| `BASE_RPM` / `TURN_RPM` | `25.0` | 바퀴 속도 |
| `ENABLE_AEC` | `true` | 에코캔슬레이션 on/off |
| `AEC_STREAM_DELAY_MS` | `100` | 스피커→마이크 왕복 지연 추정치 |
| `ENABLE_VOICE_SHIFT` | `true` | 피치/포먼트 시프트 on/off |
| `VOICE_PITCH_SEMITONES` | `3.5` | 피치(+반음) |
| `VOICE_FORMANT_RATIO` | `1.12` | 포먼트 배율 |
| `VOICE_SHIFT_BUFFER_MS` | `1200` | 변조 처리 버퍼(언더런 나면 상향) |
| `VOICE_SHIFT_OVERLAP_MS` | `120` | 청크 경계 크로스페이드 길이 |
| `QUIZ_WINDOW_MONITOR_INDEX` | `0` | 퀴즈 사진 창을 띄울 모니터 |
| `QUIZ_WINDOW_TOPMOST` | `1` | 퀴즈 창 항상 위(개발 시 0으로 끄면 Esc 닫기 활성) |
| `QUIZ_ROUND_OFFSET` | `0` | 크래시 복구용 — 이미 마친 라운드 수만큼 문제 건너뜀 |

## 스크립트

전부 `python scripts/<이름>.py`로 실행. **test_*는 API 키/로봇 없이 도는 오프라인 테스트**(예외: 표시된 것).

| 스크립트 | 용도 |
|----------|------|
| `test_quiz_bank.py` | 정답 판정(퍼지 매칭, 포기 마커/오탐 방지) |
| `test_quiz_state.py` | 퀴즈 상태 기계 — 3모드 전체 흐름, 다중 라운드, 모드 재선택 가드 |
| `test_quiz_tools.py` | 퀴즈 툴 — 지연 거절, 정답 공개 타이밍, 거절 카운트 |
| `test_voice_shift.py` | 목소리 변조 — 오버랩 정렬(항등 검증), reset 세대, pyworld 경계 연속성 |
| `test_idle_watcher.py` / `test_emotion_tools.py` / `test_snore_clip.py` | idle-sleep 판단 / 표정 클램프 / 코골이 클립 로더 |
| `test_quiz_live.py` | 실제 Gemini Live API로 퀴즈 시나리오 검증 (**API 키 필요**) |
| `test_persona.py` / `test_live_poc.py` / `test_live_audio.py` | 페르소나/Live 연결/실오디오 barge-in (**API 키, 뒤 2개는 마이크·스피커 필요**) |
| `test_motions.py` | 모터 매크로 메뉴 테스트 (**로봇 필요**) |
| `test_vision.py` / `test_vision_brain.py` / `test_display.py` | 추적/인식/표정 UI 단독 확인 |
| `read_positions.py` | 모터 현재 위치 실측(재보정용, **로봇 필요**) |
| `crop_quiz_photo.py` | 퀴즈 사진 반자동 크롭(마우스 드래그) |
| `build_quiz_bank.py` | 문제 은행 등록/원본 사진 백필 |
| `generate_snore_audio.py` | 코골이 클립 1회 생성 (**API 키 필요**) |

## 데이터 파일 (git 미포함)

| 파일/폴더 | 내용 |
|-----------|------|
| `user_profiles.json` | 사용자별 facts 누적 저장소(원자적 쓰기, 손상 시 자동 백업) |
| `art_brain.pkl` | FuzzyART 얼굴 기억 — `user_profiles.json`과 짝. `forget_me`가 둘을 함께 지운다 |
| `user_result/` | 세션별 대화록·결과지·퀴즈 연구 데이터 |
| `.env` | 비밀 설정(커밋 금지 — `.env.example`만 커밋) |

⚠️ 정리(테스트 데이터 삭제 등)할 때 이 파일들을 통째로 비우지 말 것 — 알려진 테스트 키만 골라 지운다(실제 사용자 프로필을 날린 사고가 있었음).

## 트러블슈팅

| 증상 | 원인/대처 |
|------|-----------|
| 목소리가 지직거리거나 먹힘 | 종료 시 "스피커 언더런 N회" 로그 확인 → `VOICE_SHIFT_BUFFER_MS` 상향 |
| 로봇이 자기 말에 스스로 끊김(barge-in 오탐) | 에코 — `ENABLE_AEC=true` 확인, `AEC_STREAM_DELAY_MS` 조정 |
| 카메라가 안 잡힘 | `python launcher.py <인덱스>`로 카메라 번호 변경. PC 내장캠 활성화 여부에 따라 인덱스가 밀릴 수 있음 |
| 퀴즈 창이 터미널을 가림(개발 중) | `QUIZ_WINDOW_TOPMOST=0` — 이때만 Esc로 창 닫기 가능. **실험 중엔 반드시 1**(Esc 오입력으로 창이 죽으면 세션 내내 복구 불가) |
| 콘솔에서 이모지 출력 크래시(cp949) | `bootstrap.ensure_utf8_console()`이 처리함 — 새 진입점을 만들면 반드시 최상단에서 bootstrap을 임포트할 것 |
| 포트 열기 실패 | U2D2 연결/드라이버 확인, `.env`에 `DXL_PORT` 직접 지정 |
| `.env` 값이 무시됨 | `bootstrap` 임포트보다 먼저 `os.getenv`를 읽는 코드를 새로 만들지 않았는지 확인 |

## 문서

| 문서 | 내용 |
|------|------|
| [`docs/architecture.md`](docs/architecture.md) | 설계 결정 전체(왜 이렇게 만들었는가) + 로드맵 §10 |
| [`docs/progress.md`](docs/progress.md) | 단계별 구현 이력(1~33단계) — 버그의 원인/수정 근거가 전부 여기 있음 |
| [`docs/integration-points.md`](docs/integration-points.md) | 남은 통합 지점/보류 항목 |
| [`docs/experiment_design.md`](docs/experiment_design.md) | 하찮미 실험 측정 설계(가설-설문 매핑, 카운터밸런싱, 로그 지표, 운영 절차) |
| [`docs/survey_draft.md`](docs/survey_draft.md) | 설문 문항 전체 |

## 상태 (2026-08-07)

핵심 기능 전부 완성, 실물 로봇으로 검증 완료. 하찮미 실험(N=30) 준비 완료 상태 — 실험 로그 지표(문제당 소요시간, 짜증유발 거절 횟수)와 데이터 오염 방지 장치(포기 마커 오탐 방지, 모드 중복 선택 가드, 크래시 복구)까지 반영됨(33단계). 알려진 저우선순위 보류 항목은 `docs/integration-points.md` 참고.
