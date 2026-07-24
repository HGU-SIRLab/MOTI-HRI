# 통합 시 연결해야 할 지점 (Integration TODOs)

지금까지(로드맵 §10 1~4단계) 만든 코드는 전부 **독립적으로 검증 가능하게** 일부러 서로 느슨하게 떨어뜨려 놓았다. 나중에 다음 단계(vision/, display/, launcher.py)를 만들 때 "이미 있는 것을 어디에 꽂아야 하는지" 매번 코드를 다시 뒤지지 않도록 여기 모아둔다. 무언가를 새로 만들 때 이 문서의 해당 항목도 같이 지워나가면 된다.

## vision/face.py — 포팅 완료(추적만), 얼굴인식은 아직

**결정됨(더 이상 오픈 이슈 아님)**: barge-in을 사람이 직접 이어폰 끼고 검증한 결과 서버 측 VAD(`RealtimeInputConfig.automatic_activity_detection`)가 정상 동작함을 확인했다. 그래서 `vision/face.py`(커밋 `4bf8e46`)는 입모양(jawOpen) 커스텀 VAD를 전부 제거하고 팬/틸트 추적만 담당하도록 포팅했다.

- **아직 안 된 것**: `vision/vision_brain.py`(`RobotBrain`, insightface+FuzzyART, `art_brain.pkl`)가 없어서 `face_tracker_worker(brain=None)`으로만 쓸 수 있다 — 얼굴 인식(누가 왔는지 식별)이 안 되므로 `shared_state['detected_user']`가 갱신되지 않는다. 포팅하려면 v2의 `vision/vision_brain.py`를 그대로 가져오면 됨(설계 변경 불필요, `docs/architecture.md` §07).
- **새로 발견된 이슈**: 스피커+마이크를 이어폰 없이 같이 쓰면(=로봇의 실제 구성과 동일) 스피커 소리를 마이크가 되먹여서 서버 VAD가 오탐(`interrupted=True`가 저절로 뜸)한다. `launcher.py` 오디오 루프를 만들 때 AEC 또는 "TTS 재생 중 마이크 입력 무시" 처리가 필요할 것으로 보임 — 아직 코드 없음, `docs/progress.md` 4단계 항목 참고.

## hardware/motion.py — `play_manual_motion`이 아직 아무 데도 안 붙어 있음

- `play_manual_motion(name, port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue)`는 `scripts/test_motions.py`의 키보드 메뉴로만 트리거된다. Gemini가 스스로 호출할 수 있는 툴이 아직 아니다.
- **할 일**: `core/memory_tools.py`·`core/emotion_tools.py`와 같은 패턴으로 `core/motion_tools.py`를 만들어 `make_play_motion_tool(port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue)` 클로저를 정의하고, Cognition 세션의 `tools=[...]`에 `remember_fact`, `set_emotion`과 함께 추가한다.
- `core/utils.py`의 `build_persona_system_instruction`에도 이 툴을 언제 쓸지 안내하는 절(section 7의 `set_emotion` 가이드와 같은 형태)을 추가해야 한다. 지금은 존재하지 않음.
- ID 5·11 관련 이름 정리(§05)는 끝났지만 **ID 10 모터의 정체는 여전히 불명**이다. 실물 로봇으로 어떤 관절인지 확인되면 `hardware/config.py`에 정식 이름을 붙이고 `hardware/init.py`의 "용도 불명" 주석을 지운다.
- Layer 2(`express_gesture`, 파라미터화된 프리미티브)는 아직 코드가 없다. 안전 범위 표는 `docs/architecture.md` §05에 이미 있으므로, 구현 시 그 표를 그대로 `hardware/motion.py`에 옮기면 된다.

## core/profile_manager.py — 아직 세션 생명주기에 연결 안 됨

- `load_profile_for_chat(name)`과 `is_known(name)`은 완성됐지만, 지금은 테스트 스크립트가 직접 호출할 때만 쓰인다.
- **할 일**: art_brain(얼굴인식, 아직 미포팅)이 이름을 확정하는 지점에서 `profiles.load_profile_for_chat(name)`을 호출해 `build_persona_system_instruction(name, facts_summary)`에 넘기는 코드가 필요하다. 이게 §08 시나리오 A/B("신규"/"재회")를 실제로 갈라주는 지점이다.
- `report_manager.py`(세션 종료 시 "마음처방전" 생성, v2에 있었음)는 아직 포팅 안 됨. 포팅할 때 v2의 고정 `user_info` 딕셔너리가 아니라 새 `facts` 배열 스키마를 입력으로 받도록 새로 짜야 한다.
- facts가 세션을 거듭할수록 계속 쌓이기만 하는데, 오래되거나 상충하는 항목을 정리하는 로직(v2의 `batch_update_summary`가 하던 일)은 없다. 지금 당장 필요하진 않지만(정정은 덮어쓰기라 무한정 늘어나진 않음), 세션이 많이 쌓였을 때 재검토할 것.

## core/utils.py — 감정 툴 가이드만 있고 제스처 툴 가이드는 없음, `[대화종료]` 태그 소비자 없음

- `build_persona_system_instruction`의 "5번 룰"에 `[대화종료]` 텍스트 태그를 출력하라는 지시가 v2 그대로 남아있다. 지금은 이걸 파싱해서 세션을 끝내는 코드가 어디에도 없다.
- **할 일**: Cognition 루프(4단계 이후)를 만들 때 응답 텍스트에서 `[대화종료]`를 감지 → 발화 전에 태그를 잘라내고 → 세션 종료/플러시 트리거로 쓰는 로직이 필요하다. (v2 `gemini_api.py`의 처리 방식을 참고하되 batch/Live 어느 쪽이든 적용 가능하게 짤 것.)

## core/emotion_tools.py — 콘솔 로그로만 동작 중

- `make_set_emotion_tool(emotion_queue=None)`은 `emotion_queue`가 없으면 그냥 `print()`만 한다 (display가 없으므로 현재는 의도된 동작).
- **할 일**: `display/main.py`를 포팅하면, 세션을 만들 때 실제 `queue.Queue()`를 `make_set_emotion_tool(emotion_queue)`에 넘기기만 하면 된다 — 그 외 수정 불필요.

## vision/ — 통째로 비어 있음

- `vision/face.py`(얼굴추적 PID + 입모양 VAD), `vision/vision_brain.py`(art_brain FuzzyART 얼굴 인식)가 아직 없다.
- **할 일**: architecture.md §07에 따르면 이 둘은 v1/v2와 설계 변경 없이 그대로 포팅 가능하다 — v2의 `core/suppress.py`(mediapipe/cv2 로그 억제 유틸)도 함께 필요하니 같이 가져올 것.
- `shared_state['detected_user']`가 갱신되는 지점이 바로 위 profile_manager 연결 지점과 만나는 곳이다.

## display/ — 통째로 비어 있음

- `display/main.py`, `display/emotions/*.py`(12종 표정), `display/subtitle.py`가 아직 없다.
- **할 일**: architecture.md §06에 따르면 구조 변경 없이 포팅 가능. `emotion_queue`는 위 emotion_tools 항목과 연결.

## media/ — 통째로 비어 있음

- 마이크 녹음(v2 `media/audio_manager.py`)과 TTS(v2 `media/tts_manager.py`, Typecast 기반 — 이건 설계상 폐기하고 Live API 오디오 출력 또는 Gemini TTS로 교체하기로 했었음, §03)가 없다.
- 이건 로드맵 4단계(Live API PoC) 자체의 일부라 별도 "TODO"라기보다 다음 작업 그 자체임.

## Cognition 오케스트레이션 — 아직 launcher.py도, 세션 매니저도 없음

가장 큰 공백. 지금까지 만든 `remember_fact`/`set_emotion`/`build_persona_system_instruction`은 전부 테스트 스크립트(`scripts/test_live_poc.py`, `test_live_audio.py`)가 손으로 조립해서 쓰고 있다. 실제로는:
1. art_brain이 이름을 확정(또는 미확정)
2. `profiles.load_profile_for_chat(name)` 호출
3. `build_persona_system_instruction(name, facts_summary)`로 시스템 인스트럭션 생성
4. `tools=[remember_fact, set_emotion, (미래의)play_gesture]`로 모델/세션 생성
5. 오디오 in/out 연결 (마이크→`send_realtime_input(audio=...)`, 스피커←`message.data`)
6. `message.tool_call` 수신 시 직접 함수 실행 후 `send_tool_response` — **Live 세션은 batch의 `enable_automatic_function_calling`처럼 자동 실행을 해주지 않는다.** `scripts/test_live_poc.py`의 루프를 그대로 launcher 코드로 옮기면 됨.
7. `[대화종료]` 감지 시 세션 종료·리포트 생성

이 전체를 묶는 코드가 필요하다 — 이게 사실상 launcher.py 작업의 본체다. 툴 실행 루프 자체는 4단계에서 이미 검증됐으니 그대로 재사용하면 된다.

모델명은 이미 분리해뒀다: batch 폴백용 `MODEL_NAME`(기본값 `gemini-3.1-flash-lite`), Live API용 `LIVE_MODEL_NAME`(기본값 `models/gemini-3.1-flash-live-preview`, `.env.example`에 기록됨). `client.models.list()`로 실제 `bidiGenerateContent` 지원 여부를 확인했음 — 모델명이 바뀌면 이 방법으로 재확인.
