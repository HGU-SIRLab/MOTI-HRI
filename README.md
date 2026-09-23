# 모티(Moti) — 로봇 본체

한동대학교 SIR Lab **공감서비스로봇 모티**의 몸. 마이크·스피커, 카메라·얼굴인식, 다이나믹셀
모터, 감정 표정 UI를 하나의 인지 루프(`launcher.py`)로 묶는다.

**말을 만드는 일은 이 저장소가 하지 않는다.** 대화는 같은 랜에 있는 Jetson AGX Orin —
[MOTI_BRAIN](https://github.com/HGU-SIRLab/MOTI_BRAIN) — 이 담당하고, 이쪽은 **귀·입·눈·몸**과
**모든 툴의 실제 실행**을 맡는다.

> `main` 브랜치는 Gemini Live API를 쓰던 v3이고, **이 문서는 `local-brain-integration`
> 브랜치** — 로컬 뇌에 붙은 구성을 설명한다.

- 설계 배경 전체: [`docs/architecture.md`](docs/architecture.md)
- 구현 이력: [`docs/progress.md`](docs/progress.md)
- 실험 측정 설계: [`docs/experiment_design.md`](docs/experiment_design.md)
- 젯슨 이식 절차: [`docs/jetson.md`](docs/jetson.md)

---

## 설계 철학

### 이 로봇은 유능하려고 만들어지지 않았다

모티의 목표는 문제 해결이 아니라 **공감**이다. 상담·정보검색 챗봇이 정확도로 평가받는다면,
모티는 *"이 로봇과 이야기하고 나서 마음이 좀 놓였는가"*로 평가받는다. 그래서 이 저장소의
많은 설계 결정이 성능이 아니라 **관계의 질** 쪽으로 기울어 있다.

대표적인 것들:

- **로봇이 먼저 말을 건다.** 연결 직후 히든 텍스트 턴을 보내 사용자가 입을 떼기 전에 인사한다.
  사람이 먼저 말을 걸어야 반응하는 기계는 도구지 상대가 아니다.
- **사람을 기억한다.** 얼굴로 알아보고, 대화 중 자연스럽게 알게 된 것을 쌓아두고, 다음에 만나면
  이름을 부른다. 대신 **잊어달라고 하면 프로필과 얼굴을 함께 지운다**(`forget_me`).
- **말을 끊을 수 있다.** barge-in이 되는 순간 대화는 턴제 게임이 아니라 대화가 된다.
- **몸이 있다.** 반가우면 손을 흔들고, 위로할 때는 안는 동작을 한다. 화면 속 표정만으로는
  안 되는 것이 있다.
- **모르면 모른다고 한다.** 아래 참고.

### 의도적 비완전성 (Intentional Imperfection)

**"하찮미"** — 로봇이 일부러 서툴게 구는 것이 호감을 높인다는 가설이 이 프로젝트의 연구 축이다.
코드 안에서 이 모드의 내부 이름은 그대로 **`imperfect`** 다(`core/quiz_state.py`).

퀴즈 모드는 같은 로봇을 세 가지 성격으로 돌려 이것을 비교한다:

| 모드 | 내부 이름 | 성격 |
|---|---|---|
| 1번 척척박사 | `all_knowing` | 전부 맞히고 척척 설명한다 |
| **2번 하찮미** | **`imperfect`** | **정답을 모른다. 엉뚱하게 찍고, 틀리면 귀엽게 인정한다** |
| 3번 짜증유발 | `annoying` | 알면서 안 알려주고 약 올린다 |

중요한 건 **하찮미 모드의 로봇이 "실수하는 척"을 하는 게 아니라, 애초에 정답을 받지 못한다**는
점이다(`core/quiz_state.py`가 모드별로 정답 공개 여부를 분기한다). 연기가 아니라 구조다.

그래서 이 모드에서 **정답률은 성능 지표가 아니라 조작 점검(manipulation check)** 이다. 가설
검증의 주 증거는 설문과 행동 코딩이 맡는다 — 자세한 것은
[`docs/experiment_design.md`](docs/experiment_design.md).

이 철학은 퀴즈 밖 일반 대화에도 배어 있다. 페르소나(`core/utils.py`, 약 33,500자)는 모티에게 **모르면서
아는 척하지 말고**, 억지 조언 대신 곁에 있어주고, 사용자가 말하지 않은 것을 지어내지 말라고
반복해서 지시한다.

### 그리고 — 불완전해도 정상인 기억

v2는 사용자 정보를 고정 슬롯(이름·학년·나이·MBTI·전공…)으로 두고 **전부 채워짐을 가정**했다.
v3은 자유형 `facts` 리스트로 바꿨다 — **비어 있어도, 이상한 field가 들어와도 정상 동작한다.**
사람을 설문지처럼 파악하지 않겠다는 선택이고, 동시에 "대화 중 자연스럽게 알게 된 것만 남는다"는
프라이버시 설계이기도 하다.

---

## 뇌에서 대화를 받아오는 방식

### 한 줄로 바뀌는 이유

로봇은 원래 Gemini Live API에 붙어 있었다. 로컬 뇌로 옮기면서 **`launcher.py`에서 바뀐 것은
`connect()` 호출 한 줄**이다.

```python
# 전(Gemini Live)
async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:

# 후(로컬 뇌)
async with local_live.connect(BRAIN_URI, config=config,
                              backchannel=BRAIN_BACKCHANNEL) as session:
```

`core/local_live.py`(**shim**)가 `google.genai` 라이브 세션의 겉면을 덕 타이핑하기 때문이다 —
`send_realtime_input`, `send_client_content`, `send_tool_response`, 그리고 **턴 경계에서 끝나는
`receive()` 비동기 제너레이터**까지 같은 모양이다. 그래서 수신 루프·툴 실행·재연결 로직이
**수정 없이** 돈다. Gemini로 되돌리는 것도 같은 한 줄이다.

> ⛔ **`core/local_live.py`는 뇌 저장소가 소유한다.** 이 저장소에서 편집하지 않고 `scp`로
> 복사만 한다(그래서 git에도 추적되지 않는다). 아래 [소유권 규칙](#소유권-규칙) 참고.

### 오가는 것

```
                     ws://<AGX>:8765

로봇 ──▶ 마이크 PCM16 16kHz          조건 없이 계속 보낸다. 턴 경계는 뇌가 정한다
로봇 ──▶ 툴 스키마(접속 시 1회)       함수 시그니처+docstring에서 자동 생성
로봇 ──▶ 툴 실행 결과                 뇌가 부른 것을 로봇이 실제로 수행하고 되돌려준다
로봇 ──▶ 히든 텍스트 턴               "먼저 인사해라", 프라이버시 안내, 퀴즈 진행 지시

 뇌  ──▶ 합성 음성 PCM (Piper 24kHz)  스피커로
 뇌  ──▶ 툴 호출                      set_emotion, play_gesture, remember_fact …
 뇌  ──▶ 사용자 발화 전사             대화록용 (임계 경로 밖)
 뇌  ──▶ 턴 종료 / 끼어들기 신호       barge-in이면 로봇이 재생을 즉시 끊는다
```

**로봇에는 VAD가 없다.** Gemini Live가 서버 쪽 VAD를 했기 때문에 로봇은 마이크를 조건 없이
올리기만 했고, 로컬 뇌도 그 계약을 그대로 이어받았다. 말이 끝났는지, 끼어든 것인지는 **전부
뇌가 판단한다**.

### 툴 — LLM이 결정하고, 몸이 실행한다

접속할 때 로봇이 뇌에 툴 목록을 보낸다. 스키마는 **파이썬 함수 시그니처와 docstring에서 자동
생성**된다(shim의 `tool_schemas()`) — 별도 JSON을 손으로 관리하지 않는다.

| 툴 | 하는 일 |
|---|---|
| `set_emotion(emotion)` | 표정 UI 전환 (11종). 퀴즈 모드에 따라 코드가 추가로 제한한다 |
| `play_gesture(name)` | 이름 있는 매크로 동작 — `greeting` `wave` `hug` `shy` `dance` |
| `express_gesture(joint, intensity, speed, repeat)` | 파라미터 제스처 — 관절·강도만 LLM이 정하고 좌표는 코드가 안전범위로 매핑 |
| `spin_around(turns)` | 제자리 회전 (명시적 요청 시에만) |
| `remember_fact(field, value, confidence)` | 자유형 사실 저장 + 첫 호출 시 프로필·얼굴 자가등록 |
| `forget_me()` | 프로필과 얼굴을 함께 삭제 |
| `start_quiz` `submit_guess` `request_hint` `end_quiz_early` | 퀴즈 진행 |

설계 원칙은 하나다 — **LLM은 "무엇을"만 정하고, "어떻게"는 코드가 정한다.** `express_gesture`가
좋은 예로, 모델이 관절과 강도를 말하면 실제 모터 좌표는 `hardware/motion.py`가 안전 범위 안에서
계산한다. 모델이 좌표를 직접 뱉는 구조였다면 팔이 책상을 치는 값도 그대로 실행됐을 것이다.

### 모델이 안 지킬 때를 대비한 이중화

지시만으로는 보장되지 않는 것들이 있어서, **파이썬이 확인하고 복구한다.**

- **프라이버시 고지** — 실제로 발화했는지 매 턴 확인하고, 빠지면 히든 턴으로 한 번 더 시킨다.
  전달 여부는 `session_meta.json`에 기록된다(`core/trust_notice.py`). 신뢰도 설문이 겨냥하는
  자극이라 **말했다고 가정하면 안 된다.**
- **대화 종료** — 모델이 `[대화종료]` 태그를 내면 종료하되, 안 내도 **사용자 발화에 명시적
  종료 명령이 있으면 종료한다**(`utils.is_explicit_exit_request`). 사용자의 유일한 탈출 경로를
  모델의 확률에 걸어두지 않기 위해서다.
- **인사·안내 구간 barge-in 차단** — 첫 인사와 프라이버시 고지가 나가는 동안에는 마이크를 뇌로
  보내지 않는다(`core/mic_gate.py`). 이 구간의 끼어들기는 사실상 전부 오탐이다. **어떤 신호가
  잘못돼도 30초가 지나면 마이크는 반드시 열린다** — 사용자를 막는 게이트에는 반드시 시간 상한을
  둔다.
- **이름 저장** — 확인 후에도 저장이 안 되면 세션 종료 시 콘솔에 크게 경고한다. 실험 중이면
  그 참가자 대화록이 "이름 미확인"으로 남기 때문이다.

---

## 파이프라인

```
 ┌──────────────── 로봇: Jetson Orin Nano Super 8GB ────────────────┐
 │                                                                  │
 │  마이크 ──▶ PulseAudio AEC ──▶ mic_gate ──▶ ┐                    │
 │  (C922)     (echo-cancel)      (차단 판단)   │                    │
 │                                              │                    │
 │  카메라 ──▶ InsightFace ──▶ FuzzyART ──▶ 이름 확정                │
 │  (C922)     (임베딩)        (art_brain.pkl)  │                    │
 │      └────▶ FaceLandmarker ──▶ PID ──▶ 팬/틸트 모터               │
 │                                              │                    │
 │  ┌───────────────── launcher.py ─────────────┴──────────────┐    │
 │  │  페르소나 조립(core/utils.py) · 세션 관리 · 재연결        │    │
 │  │  툴 실행 · 히든 턴 주입 · 산출물 저장                     │    │
 │  └───────────────┬──────────────────────────┬──────────────┘    │
 │                  │ core/local_live.py (shim) │                    │
 └──────────────────┼──────────────────────────┼────────────────────┘
                    │      ws://<AGX>:8765     │
 ┌──────────────────┼──────────────────────────┼────────────────────┐
 │                  ▼   뇌: Jetson AGX Orin 64GB                     │
 │   Silero VAD → smart-turn-v3 → Gemma 4 E4B → Piper TTS           │
 │   (STT 없음 — LLM이 오디오를 직접 먹는다)                          │
 └──────────────────┬──────────────────────────┬────────────────────┘
                    │ 음성 PCM                  │ 툴 호출
                    ▼                           ▼
              VoiceShifter                 모터 · 표정 UI · 기억
              (피치/포먼트)                 (hardware/ · display/ · core/)
                    ▼
                 스피커
```

### 부팅 시퀀스 (`launcher.py`)

1. 다이나믹셀 포트 열기 → 전 모터 초기 위치로 이동
2. `RobotBrain`(InsightFace + FuzzyART) 로딩
3. 얼굴추적 스레드 시작 — 인식과 팬/틸트가 **같은 카메라 세션**에서 함께 돈다
4. 최대 8초 얼굴인식 대기 → 아는 사람이면 프로필 로드, 모르면 이름 없이 진행
5. 표정 UI 스레드 + 퀴즈 사진 창 프로세스 시작
6. **뇌에 연결** → 툴 스키마 전송 → 히든 턴으로 로봇이 먼저 인사 → 대화 루프
7. 종료: 재생 드레인 → 모터 토크 OFF → facts 정리 → 대화록·퀴즈 결과 저장

### 한 턴이 도는 과정

```
사용자 발화
  └→ 마이크 → AEC → (mic_gate 통과) → 뇌로 스트리밍
       └→ 뇌가 "말이 끝났다" 판단 → Gemma 4 E4B
            ├→ 음성 청크 ──→ VoiceShifter(피치+3.5반음) ──→ 스피커
            │                   그 사이 사용자가 말하면 → barge-in → 재생 즉시 중단
            ├→ 툴 호출 ────→ launcher가 실행 → 결과를 뇌로 회신
            └→ 전사 ───────→ 대화록 누적
                 └→ turn_complete → [대화종료] 태그 검사 → 프라이버시 고지 확인
```

---

## 파일 구조

```
launcher.py              ★ 인지 루프 본체. 세션·툴 실행·히든 턴·산출물 저장 (1,059행)
run_jetson.sh            젯슨 실행 래퍼 — 디스플레이 자동탐지, PulseAudio 경유 설정
bootstrap.py             경로·환경 부트스트랩

core/
  local_live.py          ★ 뇌 연결 shim — ⛔ 뇌 저장소 소유, 여기서 편집 금지(scp로만 복사)
  utils.py               ★ 페르소나 조립(약 33,500자) · 종료 태그/백스톱 판정
  memory_tools.py        remember_fact / forget_me — 첫 호출 시 프로필·얼굴 자가등록
  profile_manager.py     user_profiles.json 입출력. 이름 정정은 '값 갱신'이 아니라 '키 이동'
  trust_notice.py        프라이버시 고지 문장(상수 고정) · 실제 발화 판정
  mic_gate.py            barge-in 차단 판단(퀴즈 중 / 인사·안내 구간). 순수 로직만
  idle_watcher.py        40초 무음 → SLEEPY 전환 판단
  emotion_tools.py       set_emotion 툴
  motion_tools.py        play_gesture / express_gesture / spin_around 툴
  quiz_state.py          ★ 퀴즈 상태 기계 — 모드별 분기(척척박사/하찮미/짜증유발)가 전부 여기
  quiz_tools.py          위 상태 기계를 툴로 감싸는 얇은 래퍼
  quiz_bank.py           문제 은행 로딩 · 라운드별 비중복 배분
  quiz_export.py         모드별 연구 데이터 JSON 저장
  report_manager.py      대화록 저장
  result_paths.py        user_result/{참가자}/{시각}/ 경로와 session_meta.json
  suppress.py            서드파티 로그 억제

vision/
  vision_brain.py        InsightFace 임베딩 + FuzzyART 온라인 학습(art_brain.pkl)
  face.py                FaceLandmarker 기반 얼굴 추적
  camera.py              카메라 백엔드 추상화(v4l2 / dshow)

hardware/
  config.py              모터 ID·홈 좌표·안전 범위 (실측값)
  init.py                포트 탐색 · 전 모터 초기화 · 토크 OFF
  motion.py              제스처 매크로와 파라미터 제스처의 실제 좌표 계산
  dxl_io.py              다이나믹셀 저수준 입출력
  wheel.py               바퀴 속도 제어(spin_around)

media/
  audio_manager.py       마이크·스피커 스트림, 플레이아웃 쿠션, 언더런 계측
  voice_shift.py         pyworld 피치/포먼트 시프트 + 청크 크로스페이드

display/
  main.py                pygame 표정 UI (풀스크린)
  emotions/              표정 렌더링 (set_emotion이 받는 11종 + sleepy/wake 등 상태 표현)
  quiz_window.py         퀴즈 사진 창 — 별도 프로세스라 버그가 표정 UI를 못 건드린다

scripts/
  jetson_doctor.py       ★ 실행 전 사전점검(카메라·시리얼·오디오·ONNX 프로바이더)
  test_*.py              하드웨어 없이 도는 단위 검사 (mic_gate, quiz_state, trust_notice …)
  read_positions.py      모터 현재 좌표 읽기(홈 자세 재보정용)
  build_quiz_bank.py     퀴즈 사진 은행 생성
  generate_snore_audio.py

docs/
  architecture.md        설계 전체
  progress.md            구현 이력
  experiment_design.md   하찮미 실험 측정 설계
  jetson.md              젯슨 이식 절차
  integration-points.md  v2→v3 이행 시 주의점

user_result/{참가자ID}/{날짜_시각}/   대화.txt · session_meta.json · 퀴즈 결과
art_brain.pkl            얼굴 기억 (FuzzyART 가중치 + 라벨)
user_profiles.json       장기 기억 (자유형 facts)
```

---

## 빠른 시작

### 1. 뇌가 먼저 떠 있어야 한다

```bash
ssh herobot@<AGX>
bash scripts/start_brain.sh      # 몇 번 돌려도 안전
```

> vLLM까지 죽어 있으면 약 29분 걸린다. **vLLM은 함부로 끄지 않는다** — 상시 가동 전제로
> 설계됐다. 자세한 것은 뇌 저장소의 `docs/brain_startup.md`.

### 2. 로봇 실행

```bash
# 의존성 (Jetson)
pip install -r requirements-jetson.txt
python scripts/jetson_doctor.py          # 사전점검

# shim 받아오기 (뇌가 갱신할 때마다)
scp herobot@<AGX>:~/moti_brain/client/local_live.py core/

# 실행
./run_jetson.sh                          # 디스플레이·오디오 자동 설정
./run_jetson.sh 1                        # 카메라 인덱스 지정
```

`run_jetson.sh`가 필요한 이유: 재부팅마다 콘솔 X 디스플레이가 `:0`/`:1`로 흔들리고, 오디오는
반드시 PulseAudio(AEC)를 경유해야 한다. 스크립트가 둘 다 자동으로 잡는다.

### 3. 대화 중 뇌 안을 들여다보기

```
http://<AGX>:8766/
```

브라우저만 있으면 된다. 지연(첫 오디오/첫 토큰), 끼어들기 카운트, VAD 확률, GPU·온도가 실시간으로
보인다. 관찰 전용이라 대화에 영향을 주지 않는다.

**모델 파일**: `models/face_landmarker.task`는 용량 때문에 git에 없다(없으면 얼굴추적 비활성).
InsightFace `buffalo_l`은 첫 실행 시 자동 다운로드. `assets/audio/snore.wav`가 없으면
`scripts/generate_snore_audio.py`로 한 번 생성한다.

**종료**: 사용자가 작별 인사를 하거나 `"대화 종료"`라고 말하면 끝난다. Ctrl+C로 끊어도 그때까지의
대화록과 퀴즈 결과는 저장된다.

---

## 주요 설정 (`.env`)

| 키 | 뜻 |
|---|---|
| `BRAIN_URI` | 뇌 주소. 기본 `ws://192.168.0.5:8765` |
| `BRAIN_BACKCHANNEL` | 대답 생성 중 "음…" 맞장구. 실물에서 타이밍이 안 맞아 기본 끔 |
| `QUIZ_EXPERIMENT_MODE` | 🚨 `true`면 페르소나가 **반토막 난다**(33,537자→17,684자, 실측). 퀴즈 실험이 아니면 `false` |
| `QUIZ_MODE_ORDER` | 참가자별 퀴즈 모드 순서(완전 카운터밸런싱) |
| `PROTECT_OPENING_BARGE_IN` | 인사·안내 구간 barge-in 차단 |
| `PLAYOUT_PRIME_MS` | 재생 쿠션. **올리기 전에 뇌 쪽 `PLAYBACK_SLACK`과 맞춰야 한다** |
| `MIC_DEVICE` / `SPEAKER_DEVICE` | 젯슨에서는 `pulse`(AEC 경유) |

전체 목록과 주의사항은 [`.env.example`](.env.example)에 주석으로 있다.

---

## 소유권 규칙

로봇과 뇌는 별도 저장소이고, **고칠 곳을 헷갈리면 양쪽이 서로의 수정을 덮어쓴다.**

| 영역 | 소유 |
|---|---|
| `launcher.py`, `core/`(단 `local_live.py` 제외), `vision/`, `hardware/`, `media/`, `display/`, `.env`, **페르소나 전문** | **로봇 (이 저장소)** |
| `core/local_live.py`, 뇌 서버·모델·TTS·VAD·턴 판정 | **뇌 ([MOTI_BRAIN](https://github.com/HGU-SIRLab/MOTI_BRAIN))** |

`core/local_live.py`가 함정이다 — **로봇에서 돌지만 뇌가 소유한다.** 여기서 편집하지 말고
`scp`로 복사만 한다(그래서 git에 추적되지 않는다).

**판정 기준**: 뇌 로그에 "보냈다"가 남았으면 로봇 문제, 안 남았으면 뇌 문제.
애매하면 양쪽 다 건드리지 말고 상의한다.

실제 운영에서 효과가 확인된 분업은 **"증상과 원인 진단까지가 로봇 몫, 어떻게 고칠지는 뇌 몫"**
이다. 로봇이 원인만 짚어 넘겼을 때 뇌 쪽이 로봇이 보지 못한 더 넓은 범위의 버그를 찾아낸 사례가
여러 번 있었다.

---

## 계보

- **v1** [hlri-iua-motirobotics](https://github.com/HandongSF/hlri-iua-motirobotics) — 모션/제스처 자산
- **v2** [Empathy-service-motirobot](https://github.com/HGU-SIRLab/Empathy-service-motirobot) — 대화 설계
- **v3** 이 저장소 — 둘을 통합하고 실시간 음성 대화를 얹음
  - `main` — Gemini Live API
  - **`local-brain-integration`** — 로컬 뇌(MOTI_BRAIN)

한동대학교 SIR Lab · 연구/논문용 · 진행 중
