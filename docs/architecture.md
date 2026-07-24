# 모티 v3 시스템 아키텍처 설계안

> HGU 공감서비스로봇 MOTI · Capstone MK3
> v1(hlri-iua-motirobotics)의 모션/제스처 자산과 v2(Empathy-service-motirobot)의 대화 설계를 통합하고, 슬롯 채우기식 인터뷰를 능동형 페르소나 대화로, 하드코딩 모션 트리거를 함수 호출 기반 3계층 물리 AI로 전환한다.

## 목차

- [00. v1 → v2 → v3 요약 비교](#00-v1--v2--v3-요약-비교)
- [01. 설계 원칙 3가지](#01-설계-원칙-3가지)
- [02. 전체 아키텍처](#02-전체-아키텍처)
- [03. 대화 엔진 (Cognition)](#03-대화-엔진-cognition)
- [04. 메모리 계층](#04-메모리-계층)
- [05. 피지컬 AI (Action)](#05-피지컬-ai-action)
- [06. 표현 계층 (Display)](#06-표현-계층-display)
- [07. 인지 계층 — 얼굴 인식 (art_brain)](#07-인지-계층--얼굴-인식-art_brain)
- [08. 엔드투엔드 시나리오](#08-엔드투엔드-시나리오)
- [09. 검증 필요 / 오픈 이슈](#09-검증-필요--오픈-이슈)
- [10. 마이그레이션 로드맵 (제안)](#10-마이그레이션-로드맵-제안)

---

## 00. v1 → v2 → v3 요약 비교

| 항목 | v1 | v2 | v3 (본 설계) |
|---|---|---|---|
| 대화 진행 방식 | 이름 확인 정도, 설계 없음 | 7단계 강제 인터뷰(FSM) | 슬롯 없는 능동형 대화 + 암묵적 사실 추출 |
| 물리 동작 | 댄스·포옹·인사·부끄부끄 (하드코딩) | 팬/틸트 추적 + 고개 끄덕임뿐 | 매뉴얼 매크로(Layer1) + 파라미터 제스처(Layer2) |
| 동작 트리거 | LLM 오디오 판단 → 정규식 태그 파싱 | 없음(감정 태그만) | function calling 툴 직접 호출 |
| TTS | SAPI / Typecast | Typecast | Gemini Live API 오디오 출력(통합) |
| 얼굴 인식 | FuzzyART (art_brain) | FuzzyART (art_brain) | 변경 없음 — 그대로 재사용 |
| 코드 구조 | God Object, 전역 상태 강결합 | 계층 분리, 단방향 의존 | v2 구조 계승 + 툴 기반 계층 추가 |

---

## 01. 설계 원칙 3가지

### ① 대화는 인터뷰가 아니라 관계다

정해진 순서로 정보를 캐묻는 슬롯 채우기(STAGES) 구조를 폐기한다. 로봇은 **사람과의 소통 자체에 호기심을 갖는 페르소나**로 행동하며, 이름·전공·MBTI 같은 정보는 대화의 목적이 아니라 자연스러운 부산물이다. 첫 만남과 재회 사이에 구조적 구분을 두지 않는다 — 둘 다 "지금까지 아는 것"을 배경으로 한 연속된 대화일 뿐이다.

### ② 대화 엔진은 교체 가능해야 한다

Gemini Live API(WebSocket 기반 실시간 음성)가 목표 백엔드이지만 preview 상태이며 인터럽션 처리가 문서화되어 있지 않다. 따라서 **페르소나·메모리·모션 트리거는 전부 function calling 계약으로 추상화**하여, Live API가 검증에 실패하면 기존 batch 패턴(오디오 inline_data → 텍스트 스트리밍 → Gemini TTS)으로 후퇴해도 나머지 설계가 무너지지 않게 한다.

### ③ 물리 동작은 3계층으로 방어한다

LLM이 모터를 직접 자유 제어하게 두지 않는다. Herobotics의 3-Layer Defense 패턴을 우리 로봇의 실제 자유도(대부분 1 DOF 단축 관절)에 맞게 축소 적용: **Layer 1(매뉴얼 매크로) → Layer 2(안전범위 내 파라미터 제스처) → Layer 3(폴백)**. LLM은 항상 "무엇을 할지"만 고르고, "얼마나 움직일지"의 물리적 한계는 코드가 강제한다.

---

## 02. 전체 아키텍처

v2의 4계층 구조(Perception–Cognition–Memory–Action)를 그대로 유지하되, Cognition을 교체 가능한 백엔드로, Action을 3계층 물리 AI로 확장한다.

```
┌─────────────────────────────────────────────────────────────┐
│ PERCEPTION                                                   │
│ 얼굴추적(PID) + art_brain(FuzzyART 얼굴 인식) + 입모양 VAD    │
│ — v2 그대로                                                  │
└─────────────────────────────────────────────────────────────┘
                    ↓ 사용자 이름 / 오디오 스트림
┌─────────────────────────────────────────────────────────────┐
│ COGNITION                                                    │
│ Gemini Live API(주) 또는 batch+TTS(폴백)                     │
│ 페르소나 시스템 인스트럭션 · function calling 라우터 — 신규   │
└─────────────────────────────────────────────────────────────┘
    ↓ remember_fact / play_manual_motion / express_gesture / set_emotion
┌─────────────────────────────────────────────────────────────┐
│ MEMORY                                                        │
│ user_profiles.json (자유 key-value 누적) · report_manager     │
│ (마음처방전) — 구조 변경                                      │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ ACTION                                                         │
│ Layer1 매크로 / Layer2 파라미터 제스처 / Layer3 폴백           │
│ pygame 표정 · 자막 — 신규                                     │
└─────────────────────────────────────────────────────────────┘
```

바퀴(WASD 텔레옵)는 LLM 의사결정과 무관한 수동 조작 경로로 v1/v2와 동일하게 독립 스레드로 유지한다.

---

## 03. 대화 엔진 (Cognition)

### 주 경로 — Gemini Live API

WebSocket 세션 하나를 열어 오디오 입력/출력을 양방향 스트리밍하고, function calling으로 메모리·모션·감정을 트리거한다. 슬롯 FSM이 사라졌으므로 첫 만남과 재회를 구분할 별도 대화 모드가 필요 없다 — art_brain이 이름을 확정하는 시점에 세션의 system instruction만 아래처럼 갈아 끼운다.

| 상황 | system instruction에 주입되는 내용 |
|---|---|
| 신규 사용자 | "처음 만난 사람입니다. 스스로 궁금한 것을 자연스럽게 물어보되 질문지처럼 나열하지 마세요." |
| 재회 사용자 | [이름] + 지금까지 remember_fact로 누적된 사실 목록 → "오랜만에 만난 지인처럼 이어가세요." |

### 폴백 경로 — batch 패턴 + Gemini TTS

Live API의 인터럽션 처리가 검증에서 미흡하다고 판단되면, v2의 기존 패턴(마이크 녹음 → Gemini에 audio inline_data로 STT+대화 동시 처리 → 문장 단위 스트리밍 → TTS)을 유지하되 **Typecast를 Gemini 3.1 Flash TTS로 교체**한다. function calling 계약이 동일하므로 메모리/모션 로직은 손대지 않는다.

> **검증 상태**: barge-in 메커니즘(`interrupted` 필드, 서버 측 자동 VAD)과 function calling 배선은 `scripts/test_live_poc.py`·`test_live_audio.py`로 확인됨 — 상세는 §09, `docs/progress.md` 참고. 남은 건 사람이 직접 끼어들었을 때의 체감 자연스러움과 preview 상태의 가격·안정성.

---

## 04. 메모리 계층

슬롯을 강제로 채우는 대신, 대화 중 자연스럽게 드러나는 정보를 모델이 스스로 감지해 저장한다.

```
remember_fact(
  field: "name" | "grade" | "major" | "mbti" | "gender" | "rc" | string,
  value: string,
  confidence: "certain" | "inferred"
)
```

고정된 7단계(STAGES) 열거형 대신 `field`를 자유 문자열로 두어, 대화에서 자연스럽게 드러나는 임의의 정보(동아리 관심사, 요즘 고민 등)도 같은 경로로 누적할 수 있게 한다. 정정도 동일한 툴 재호출로 처리되어 v2의 "확인→교정" 명시적 루프가 사라진다.

### user_profiles.json 스키마 변화

| v2 (고정 슬롯) | v3 (자유 누적) |
|---|---|
| `user_info: {이름,학년,나이,MBTI,성별,전공,RC,새새인원}` — 전부 채워짐을 가정 | `facts: [{field, value, confidence, updated_at}]` — 불완전해도 정상 |

`report_manager.py`(마음처방전 생성)와 `chat_summary` 장기요약 로직은 그대로 두되, 입력을 "고정 슬롯"이 아니라 "그 시점까지 누적된 facts 목록"으로 바꾸면 된다 — 정보가 적으면 적은 대로 리포트를 생성하는 것이 오히려 실제 관계의 깊이를 반영한다.

---

## 05. 피지컬 AI (Action)

### 우리 로봇의 실제 관절 맵 (v1 `function/config.py` + v2 `hardware/config.py` 대조)

Herobotics 저장소는 3계층 방어 *패턴*만 참고했다 — 아래 표는 그쪽 스펙이 아니라 우리 로봇 자체의 실측 구성이다.

| ID | 관절 | 자유도 | 비고 |
|---|---|---|---|
| 1 | 고개 끄덕임 (HEAD_NOD_ID) | 1 DOF | v2에서만 활성 로직 존재 |
| 2 | 팬 (좌우 시선) | 1 DOF | 얼굴추적 PID 전용, 모드 상호배제 필요 |
| 3 / 4 | 좌·우 바퀴 | 속도제어 | WASD 수동 텔레옵, LLM 무관 |
| 5 | 어깨 (SHOULDER_ID) | 1 DOF | v1은 동일 관절을 `DANCE_ID`로도 중복 명명 — v2가 `SHOULDER_ID`로 정리해 유일 이름 확정 |
| 6 | 보조 관절(AUX_ID) | 1 DOF | v1 댄스 시퀀스 전용 |
| 7 / 8 | 오른팔 / 오른손 | 1 DOF씩 | |
| 9 | 틸트 (상하 시선) | 1 DOF | 얼굴추적 PID 전용 |
| 11 / 12 | 왼팔 / 왼손 (LEFT_ARM_ID) | 1 DOF씩 | v1은 동일 관절을 `RPS_ARM_ID`로도 중복 명명(가위바위보용 좁은 서브레인지) — v2가 `LEFT_ARM_ID`로 정리해 유일 이름 확정 |

> **정정 — 실제로는 충돌 아님**: 초안에서 ID 5/11을 "충돌"이라 표현했으나, 실측값 대조 결과 오류였다. v1의 `RPS_ARM_UP/DOWN_POS`(1052–1352)는 `LEFT_ARM_*_POS` 전체 범위(900–1700) 안에 정확히 포함되어 있어 **같은 물리 관절, 같은 좌표 스케일**을 두 이름(`RPS_ARM_ID`/`LEFT_ARM_ID`, `DANCE_ID`/`SHOULDER_ID`)으로 부른 것뿐이다. v2 `hardware/config.py`가 이미 임시 별칭을 걷어내고 `LEFT_ARM_ID`·`SHOULDER_ID`라는 정식 이름만 남겨 정리를 끝냈다 — 이는 v1/v2가 동일 로봇이라는 근거이기도 하다. v3에서는 v2 명명을 그대로 채택하면 되며, 별도의 "충돌 해소" 작업은 불필요하다.

### Layer 1 — 매뉴얼 매크로

v1 `dance.py`의 `play_hug_motion`, `play_greeting_motion`, `play_shy_motion`, `_new_dance_routine`을 그대로 이식해 이름 있는 함수로 노출한다. 가장 안전한 경로 — LLM은 "무엇을" 고르기만 하고 궤적은 전부 기존 검증된 하드코딩 시퀀스를 그대로 실행한다.

```
play_manual_motion(
  name: "dance" | "hug" | "greeting" | "wave" | "shy"
)
```

### Layer 2 — 파라미터화된 프리미티브

단축 관절(1 DOF)뿐이라 Herobotics식 자유 포즈 합성은 의미가 작다. 대신 관절별로 "얼마나 강하게 · 얼마나 빠르게 · 몇 번" 만 LLM이 정하고, 실제 좌표 변환은 코드가 안전 범위 안에서 처리한다.

```
express_gesture(
  joint: "right_arm" | "left_arm" | "shoulder" | "head_nod",
  intensity: 0.0–1.0,
  speed: "slow" | "normal" | "fast",
  repeat: 1–3
)
```

| 관절 | 안전 최소 | 안전 최대 | 근거 |
|---|---|---|---|
| 오른팔 (ID 7) | 3400 | 4050 | v1 `RIGHT_ARM_ACTION_POS`~`RIGHT_ARM_TOP_POS` 실측값 (준비자세 3685) |
| 왼팔 (ID 11) | 900 | 1700 | v1 `LEFT_ARM_TOP_POS`~`LEFT_ARM_ACTION_POS` 실측값 (준비자세 1402) |
| 어깨 (ID 5) | 1846 | 2200 | v1 `SHOULDER_RIGHT_POS`~`SHOULDER_LEFT_POS` 실측값 (중앙 2073) |
| 고개 끄덕 (ID 1) | 3800 | 4030 | v2 `HEAD_NOD_DOWN_POS`~`HEAD_NOD_MAX_POS` 실측값 (홈 4000) |

위 값은 v1 config.py·v2 hardware/config.py에 실제로 기록된 실측값이다. 다만 모터가 물리적으로 재조립/재캘리브레이션되었을 가능성이 있으므로, v3 구현 착수 전 `debug_motor_positions.py`로 현재 로봇 기준 재검증은 여전히 권장한다.

### Layer 3 — 폴백

툴 인자 파싱 실패, 알 수 없는 관절명, 혹은 `shared_state['mode']`가 `tracking`이라 팬/틸트가 사용 중이라 충돌하는 경우 등은 동작을 아예 실행하지 않거나 최소 동작(고개 살짝 끄덕임)으로 대체한다. 에러가 예측 불가능한 물리적 동작으로 이어지지 않도록 하는 것이 유일한 목적이다.

---

## 06. 표현 계층 (Display)

v2의 pygame 표정 UI(12종 감정, `display/emotions/*.py`)와 tkinter 자막은 구조 변경 없이 유지한다. 다만 감정 전달 방식을 정규식 태그 파싱에서 함수 호출로 통일한다.

```
set_emotion(
  emotion: "neutral" | "happy" | "excited" | "tender" | "scared" | "angry" | "sad" | "surprised" | "listening" | "thinking" | "scanning"
)
```

이렇게 하면 대화(음성/텍스트) 출력과 감정 신호가 같은 `[EMOTION]` 문자열 채널을 억지로 공유하던 v1/v2의 방식 대신, 모션 트리거(`play_manual_motion`)와 동일한 툴 프로토콜을 쓰게 되어 파싱 실패 가능성이 줄어든다. 자막은 Live API가 함께 내보내는 텍스트 출력을 그대로 사용한다.

---

## 07. 인지 계층 — 얼굴 인식 (art_brain)

`vision/vision_brain.py`의 FuzzyART 기반 얼굴 인식은 v1과 v2에서 동일하게 검증된 구성요소이며, 대화 로직과는 "인식된 이름 문자열" 하나로만 느슨하게 연결되어 있다. v3에서도 변경하지 않는다 — 감정/성격 모델이 아니라 순수 재인식 컴포넌트라는 점은 이전 분석에서 이미 확인된 사실이다.

---

## 08. 엔드투엔드 시나리오

### 시나리오 A — 처음 만난 사람

1. **얼굴 인식 실패** — `art_brain.recognize_face()` → `"Unknown"`, `shared_state['detected_user']` 갱신 없음
2. **세션 시작** — Live API 세션을 "신규 사용자" system instruction으로 오픈, 로봇이 먼저 자연스럽게 말을 건다
3. **대화 진행 중 사실 누적** — 이름이 언급되는 순간 `remember_fact("name", ...)` 호출 → 이후 등록 안내(10초 카메라 응시) → `brain.register_face()`
4. **감정/동작 반응** — 대화 흐름상 로봇이 반가움을 느끼면 `set_emotion("happy")`, 사용자가 인사하면 `play_manual_motion("greeting")`
5. **세션 종료** — 누적된 facts를 `user_profiles.json`에 저장, `report_manager`로 세션 요약 생성

### 시나리오 B — 재회

1. **얼굴 인식 성공** — `recognize_face()`가 이름 확정(5프레임 중 3표 이상) → `shared_state['detected_user']` 갱신
2. **프로필 로드** — 누적 facts를 system instruction에 주입, "오랜만에 만난 지인" 프레이밍으로 세션 오픈
3. **자연스러운 이어가기** — 이전에 몰랐던 정보(예: 전공)가 이번에 드러나면 다시 `remember_fact`로 보강 — 슬롯을 "완성"하려는 압박 없음

---

## 09. 검증 필요 / 오픈 이슈

- [x] Gemini Live API의 barge-in 메커니즘 자체는 SDK에 존재함이 확인됨(`google-genai` 1.61.0) — `LiveServerContent.interrupted: bool` 필드로 서버가 끼어들기를 알려주고, `RealtimeInputConfig.automatic_activity_detection`이 서버 측 VAD를 자동 처리해 v1/v2가 쓰던 커스텀 입모양 VAD가 필요 없어짐. **사람이 직접 이어폰을 끼고 `scripts/test_live_audio.py`로 끼어들기 체감까지 확인 완료** — 정상 동작함. (이어폰 없이 스피커+마이크를 같이 쓰면 스피커 소리를 마이크가 되먹여 `interrupted=True`가 저절로 뜨는 음향 에코 오탐이 있었는데, 이어폰 착용 시 재현이 안 돼서 원인이 에코였음이 확정됨 — barge-in 로직 자체의 결함은 아님. 아래 신규 이슈로 분리.)
- [ ] **(신규)** 스피커→마이크 음향 에코로 인한 barge-in 오탐 — 실제 로봇도 마이크/스피커가 물리적으로 붙어있어 같은 문제가 날 수 있음. `launcher.py`(현재는 `scripts/run_no_robot.py`) 오디오 루프에 AEC(에코 캔슬레이션) 또는 "TTS 재생 중 마이크 입력 무시" 중 하나를 넣어야 함 — 아직 미해결. (`docs/progress.md` 4단계 참고)
- [ ] Gemini 3.1 Flash TTS의 스트리밍 지원 여부, 음성 목록/커스터마이징 옵션, 가격 — Live API가 barge-in까지 자체 해결하므로 우선순위 낮아짐(배터리 폴백 경로에서만 필요)
- [x] Live API function calling은 예상대로 수동 처리(`tool_call` 이벤트 수신 → 직접 실행 → `send_tool_response`)가 필요했음 — `scripts/test_live_poc.py`에서 `remember_fact`/`set_emotion` 둘 다 정상 동작 확인, 첫 응답까지 지연시간 0.49~0.66초(텍스트 턴 기준). 모터처럼 수 초 걸리는 동작을 위한 "시작만 확인, 완료는 비동기 신호" 설계는 `core/motion_tools.py`로 구현됨(백그라운드 스레드 실행) — 실제 모터로는 아직 미검증.
- [ ] 가위바위보·OX퀴즈 미니게임을 v3 범위에 포함할지 결정 (포함 시 v1의 `RPS_ARM_UP/DOWN_POS` 서브레인지를 `LEFT_ARM_ID` 이름으로 재사용)
- [ ] Layer 2 안전 범위 표(§05)는 v1/v2 config.py 기록값 기준 — 실물 로봇 재캘리브레이션 여부를 `debug_motor_positions.py`로 재확인
- [ ] 두 모델 모두 preview 상태 — 캡스톤 발표 일정 내 안정성 리스크 점검

---

## 10. 마이그레이션 로드맵 (제안)

> 각 단계에서 만든 코드가 이후 단계에 어떻게 연결되어야 하는지는 [`docs/integration-points.md`](integration-points.md)에 별도로 정리한다. 새 단계를 시작하기 전에 먼저 확인할 것 — "이미 만들어둔 걸 어디에 꽂아야 하는지" 다시 찾아 헤매지 않기 위한 문서다.

1. **Layer 1 단독 이식** — v1 `dance.py` 모션 함수를 v2 `hardware/config.py`의 정식 명명(`LEFT_ARM_ID`, `SHOULDER_ID` 등)에 맞춰 `hardware/` 구조로 옮기고, 기존 키보드 트리거 등으로 독립 테스트 (대화 엔진과 무관하게 먼저 검증) — ✅ 코드 완료, 상세 내용과 발견된 버그는 [`docs/progress.md`](progress.md) 참고. 실물 로봇 검증은 아직 미완료.
2. **메모리 계층 전환** — `user_profiles.json` 스키마를 facts 배열로 바꾸고, 기존 batch Gemini 호출에서도 `remember_fact` function calling으로 먼저 시험 (Live API 없이도 검증 가능) — ✅ 완료, `docs/progress.md` 참고.
3. **페르소나 시스템 인스트럭션 재작성** — STAGES 기반 `build_*_prompt` 제거, 능동형 호기심 페르소나로 `core/utils.py` 재작성 — ✅ 완료, 실제 Gemini 대화로 검증됨. `docs/progress.md` 참고.
4. **Live API PoC** — 별도 브랜치에서 barge-in·지연시간·function calling 안정성만 집중 검증, 실패 시 batch+Gemini TTS 경로로 확정 — ✅ 완료(연결·지연시간·function calling·사람이 직접 끼어드는 barge-in 체감까지 전부 검증됨). `docs/progress.md` 참고.
5. **로봇 없이 가능한 나머지 조각 전부** — 로봇 연결이 계속 지연되어, §10에 원래 없던 단계지만 모터가 필요 없는 작업을 이 시점에 몰아서 진행함: `vision/face.py`(팬/틸트 추적, 커스텀 VAD 제거), `vision/vision_brain.py`(얼굴인식), `display/`(표정 UI), `core/report_manager.py`(세션종료 결과지), `core/motion_tools.py`(제스처 툴, 아직 로봇 미검증), `[대화종료]` 태그 처리, 그리고 이 전부를 실제로 이어붙인 `scripts/run_no_robot.py`(로봇 없는 launcher.py 격) — ✅ 완료, 상세는 `docs/progress.md`·`docs/integration-points.md` 참고.
6. **Layer 2 파라미터 제스처** — 안전 범위 실측 후 `express_gesture` 구현, 얼굴추적과의 모드 상호배제 검증 — 로봇 연결 대기 중
7. **통합 및 시나리오 리허설** — §08 시나리오 A/B를 실제 로봇으로 반복 실행, 예외 상황(사람이 여러 명, 인식 실패 등) 보강 — 로봇 연결 대기 중

---

*이 문서는 v1(`C:\cap_dev\capston_mk1\motirobotics`)과 v2(`C:\cap_dev\capston_mk2\Empathy-service-motirobot`) 코드베이스 전체 분석과 설계 논의를 정리한 초안이다. Herobotics(github: HGU-SIRLab/Herobotics)는 3-Layer Defense 패턴만 참고했으며, 하드웨어 스펙 자체는 무관하다.*
