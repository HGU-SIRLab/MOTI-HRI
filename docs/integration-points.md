# 통합 시 연결해야 할 지점 (Integration TODOs)

로드맵 §10 1~8단계(로봇 없이 가능한 작업 전부 + 모터/팬틸트/얼굴인식 통합 + Layer 2 + 먼저 인사·자동등록 + AEC)까지 `launcher.py`(구 `scripts/run_no_robot.py`, 2026-07-27 승격)가 실제로 이어붙였다. **핵심 기능은 이걸로 전부 완성 — 2026-07-27 이어폰 없이 실제 대화로 AEC까지 최종 검증 완료됨(사용자 확인: "성공, 대박").** 남은 건 우선순위 낮은 항목뿐. 이 문서는 **아직 진짜로 안 이어진 지점만** 남긴다 — 뭔가 새로 만들 때 여기 항목도 같이 지워나갈 것(파일 자체의 오래된 원칙).

## 아직 안 이어진 지점

### Layer 3 폴백 — 코드 없음

`docs/architecture.md` §05가 설명하는 "얼굴이 사라지면 최소 동작으로 대체" 같은 명시적 폴백은 없다. 지금은 `play_gesture`/`express_gesture`가 알 수 없는 이름/관절이면 그냥 무시하는 정도만 구현됨(그 자체로 최소한의 Layer 3이긴 하지만, 문서가 원래 의도한 "대체 동작"까지는 아님).

### ~~ID 10 모터 — 정체 여전히 불명~~ (조사 불필요로 확정, 2026-07-28)

사용자 확인: 그냥 안 쓰는 모터. `hardware/init.py`/`shutdown_all_motors`가 이름 없이 리터럴 `10`으로 초기화/종료만 하는 현재 상태 그대로 두면 됨 — 더 이상 조사하지 않는다.

## 로봇과 무관하게 남은 것 (우선순위 낮음)

- ~~`subtitle.py`의 번들 폰트가 런타임에 자동 등록 안 됨~~ — 자막 기능 자체가 필요 없다고 판단(2026-07-27)한 데 이어, 어디에도 연결되지 않은 고아 코드였던 `display/subtitle.py`와 `display/fonts/`를 삭제함(2026-07-28). `requirements.txt`의 `screeninfo`는 `vision/face.py`(카메라 창 배치)가 별도로 쓰고 있어 그대로 둠.

## facts 정리 + 사용자 삭제 요청 (완료, 2026-07-28)

- **facts 정리**: `core/profile_manager.py`에 `consolidate_facts(name, max_facts=20)` 추가 — field가 완전 자유형이라(memory_tools.py 계약) 모델이 "고민"/"요즘고민"처럼 같은 의미를 다른 이름으로 저장하면 무한정 늘어날 수 있었던 문제(v2엔 있던 `batch_update_summary`가 v3엔 없었음). `MODEL_NAME` LLM 1회 호출로 유사 field 병합 + 최대 개수 압축, 파싱 실패 시 원본을 그대로 두고 `False` 반환(데이터 손실 방지). `launcher.py`가 세션 종료 시(결과지 생성 직전) `final_name`이 있으면 항상 호출. 실제 Gemini 호출로 검증(25개 중복 섞인 facts → 6개로 병합, "고민"/"요즘고민"과 "취미"/"hobby" 정상 병합 확인).
- **사용자 삭제 요청("내 정보 지워줘")**: `user_profiles.json`만 지우면 `art_brain.pkl`(얼굴인식)엔 얼굴이 그대로 남아 "아는 얼굴인데 정보 없음" 상태가 될 위험이 있었음(사용자가 직접 지적) → `vision/vision_brain.py`의 `RobotBrain.forget_face(name)`(같은 이름의 FuzzyART 카테고리를 전부 제거 — 인식 임계값 드리프트로 한 사람이 여러 카테고리로 나뉘어 저장돼 있을 수 있어 전부 제거) + `core/memory_tools.py`의 `make_forget_me_tool(name_state, shared_state, brain)`로 프로필 삭제와 얼굴 삭제를 한 번에 묶은 Gemini 툴 `forget_me` 추가. 페르소나(`core/utils.py`)엔 "삭제 전 한 번은 되물어 확인" 지시 추가. 오프라인 유닛 테스트(가짜 brain)로 원자적 삭제(name_state/shared_state 리셋 포함) 검증, FuzzyART 필터 로직도 별도 검증(같은 이름의 중복 카테고리 2개 동시 제거 확인).

## 완료된 통합 (참고용 — `launcher.py`)

1. `vision_brain.RobotBrain` + `vision.face.face_tracker_worker(..., brain=brain)`로 얼굴인식과 팬/틸트 추적을 같은 스레드/카메라 세션에서 함께 수행 — ✅ (2026-07-27 통합. 세션 시작 시 확정된 이름을 그 세션 내내 고정하고 이후 다른 사람이 감지돼도 무시함 — `is_initial_recognition_active`가 이미 이 동작을 구현하고 있어 그대로 활용. `launcher.py`의 `wait_for_identification()` 참고)
2. `profiles.load_profile_for_chat(name)` 호출(이름 확정 시) — ✅
3. `build_persona_system_instruction(name, facts_summary)` — ✅
4. `tools=[remember_fact, set_emotion, play_gesture, express_gesture]` — ✅ `remember_fact`는 이름을 몰라도 항상 붙는다(아래 8번 참고).
5. 오디오 in/out + AEC (`media/audio_manager.py`의 `MicStreamer`/`Speaker`/`EchoCanceller`) — ✅ (2026-07-27. `launcher.py` 인라인 클래스를 `media/`로 분리하면서 AEC 연결. 실제 오디오 장치로 2초간 End-to-end 스모크 테스트 통과 → **이어폰 없이 실제 대화로 최종 검증까지 완료**, 기본 `AEC_STREAM_DELAY_MS`(100ms) 그대로 잘 작동함 — 튜닝 불필요했음)
6. `message.tool_call` 수동 처리 — ✅
7. `extract_exit_tag()`로 `[대화종료]` 감지 → `report_manager.generate_and_save_reports(name, log, 최신_facts_summary)` — ✅
8. **처음 보는 사람 자동 등록 + 로봇이 먼저 인사** — ✅ (2026-07-27) `core/memory_tools.py`의 `make_remember_fact_tool`이 `name` 문자열 대신 `name_state`(예: `{"name": None}`) 가변 딕셔너리를 받도록 재설계. Live API는 세션 도중 tools를 못 바꾸므로, 이름을 몰라도 `remember_fact`를 처음부터 항상 붙여두고 모델이 처음으로 `remember_fact(field="name", value=...)`를 호출하는 순간 그 값으로 이름을 확정 + `shared_state['detected_user']` 갱신 + 그 시점 `shared_state['current_face_embedding']`으로 `RobotBrain.register_face()` 호출까지 한 번에 처리. 대화 시작은 연결 직후 `session.send_client_content(...)`로 "사용자가 말하기를 기다리지 말고 먼저 인사하라"는 텍스트 턴을 보내 트리거(공식 문서 권장 패턴). 페르소나(`core/utils.py`)에도 상시 "먼저 말을 거세요" 지시 추가.
9. 모터 초기화/안전 종료(`hardware.init.initialize_robot`/`shutdown_all_motors`) — ✅ (스레드 join 후 포트 종료 순서 준수. 얼굴추적 스레드를 띄우는 쪽은 종료 시 `stop_event.set()` → `join()` → `port.closePort()` 순서를 반드시 지킬 것 — 순서를 안 지키면 워커의 정리 코드가 이미 닫힌 포트에 쓰다가 `'NoneType' object has no attribute 'hEvent'` 에러가 남, `test_vision.py`에서 실측)
10. Layer 2 파라미터 제스처(`hardware.motion.play_express_gesture`, `core.motion_tools.express_gesture`) — ✅ (2026-07-27. 팔/고개는 rest↔안전범위 끝 선형보간, 어깨는 좌우 대칭 wiggle. `play_gesture`와 `busy` 가드 공유)
11. 목소리 피치/포먼트 시프트(`media/voice_shift.py`의 `VoiceShifter`) — ✅ (2026-07-28. `speech_config`로 `LIVE_VOICE_NAME`(기본 Zephyr — 처음엔 Fenrir였으나 교수님 피드백으로 교체) 지정 + 재생 직전 pyworld로 더 앳된 톤 후처리. 파라미터는 실사용 피드백으로 +4st/×1.15 → **+3.5st/×1.12**로 확정. `sc.interrupted` 시 `shifter.reset()`, `sc.turn_complete` 시 `shifter.flush()`도 같이 연결해야 함 — 안 하면 각각 "끼어들기 후 뒤늦게 재생"/"발화 꼬리 유실" 버그가 남. 실사용 중 지직거림/음성 드롭 제보로 `VOICE_SHIFT_BUFFER_MS`(500→700ms)와 `Speaker.underrun_count` 진단 로그 추가함(`docs/progress.md` 11단계) — **다음 실물 테스트에서 언더런 로그가 뜨는지, 700ms로 충분한지 확인 필요.**)
모델명: batch 폴백용 `MODEL_NAME`(기본값 `gemini-3.1-flash-lite`), Live API용 `LIVE_MODEL_NAME`(기본값 `models/gemini-3.1-flash-live-preview`), `.env.example`에 기록됨. `client.models.list()`로 실제 `bidiGenerateContent` 지원 여부 재확인 가능.
