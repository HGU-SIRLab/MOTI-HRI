"""로봇의 전체 인지 루프 — scripts/run_no_robot.py를 승격한 실제 진입점(2026-07-27).

웹캠 얼굴인식 + 팬/틸트 추적(vision/face.py, 같은 스레드에서 함께 동작) → 프로필 로드 →
페르소나 조립 → Live API 음성 대화(remember_fact, set_emotion, play_gesture, express_gesture
툴 연결) → 표정 UI(display) → [대화종료] 감지 시 결과지 생성, 이 흐름을 전부 실제로 연결한다.
**로봇(다이나믹셀 모터)이 연결되어 있어야 한다** — 로봇 없이 대화 파이프라인만
테스트하려면 git 이력의 scripts/run_no_robot.py 버전 참고(현재는 이 파일에 흡수됨).

처음 보는 사람(art_brain에 등록 안 됨)이면 로봇이 먼저 이름을 물어보고, 대화 중
`remember_fact(field="name", ...)`가 처음 호출되는 순간 그 이름으로 프로필을 만들고
얼굴도 함께 등록한다(core/memory_tools.py 참고) — 별도로 미리 등록해둘 필요 없다.

스피커→마이크 에코로 인한 barge-in 오탐 방지를 위해 AEC(media/audio_manager.py,
`aec-audio-processing`/WebRTC AEC3 기반)가 기본으로 켜져 있다 — 문제가 있으면
`.env`에 `ENABLE_AEC=false`로 끌 수 있다.

사용:
    python launcher.py [카메라 인덱스]
    Ctrl+C 또는 대화 중 자연스러운 작별 인사([대화종료])로 종료 — 이름을 아는 경우
    user_result/에 대화록+결과지가 남고, 퀴즈를 진행했다면 core/quiz_export.py가
    같은 폴더 아래 참가자+시각 단위 하위 폴더에 모드(1/2/3)별 결과 파일도 남긴다.
"""
import asyncio
import multiprocessing
import os
import queue
import sys
import threading
import time
import wave

from dynamixel_sdk import PacketHandler, PortHandler

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core import report_manager
from core.emotion_tools import make_set_emotion_tool
from core.idle_watcher import IDLE_SLEEP_SEC, decide_idle_action
from core.memory_tools import make_forget_me_tool, make_remember_fact_tool
from core.motion_tools import make_motion_tools
from core.quiz_export import save_quiz_results
from core.quiz_tools import make_quiz_tools
from core.utils import build_persona_system_instruction, extract_exit_tag
from display.main import RobotFaceApp
from display.quiz_window import quiz_window_process
from hardware import config as C
from hardware import init as I
from media.audio_manager import ENABLE_AEC, INPUT_RATE, OUTPUT_RATE, EchoCanceller, MicStreamer, Speaker
from media.voice_shift import (
    ENABLE_VOICE_SHIFT,
    POST_SPEECH_DRAIN_SEC,
    VOICE_SHIFT_BUFFER_MS,
    VoiceShifter,
)
from vision import face as F
from vision.vision_brain import RobotBrain

LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")
# 퀴즈 모드가 히든 턴을 주입(inject_turn)할 때, 로봇이 아직 이전 턴을 말하는 도중이면 곧장
# 보내지 말고 기다려야 한다 — 안 그러면 새 응답 생성이 이전 발화 위에 겹쳐서 음성이
# 끊기거나 뭉개지는 사고가 난다(2026-07-30 실사용 중 발견, 하찮미 모드의 "저도 맞춰볼게요"
# 이벤트처럼 발화가 길어질수록 REVEAL_HOLD_SEC 고정 지연만으로는 부족했음). turn_complete
# 이후에도 VoiceShifter가 최대 VOICE_SHIFT_BUFFER_MS만큼 버퍼링한 오디오를 아직 재생 중일
# 수 있어, 그 버퍼가 다 흘러나갈 시간을 넉넉히(2배) 더 기다린다 — 계산 자체는
# media/voice_shift.py에 있다(core/quiz_tools.py도 같은 상수를 공유해야 해서 그쪽으로 옮김).
# 30종 프리셋 중 Fenrir + 피치/포먼트 시프트(media/voice_shift.py)가 "귀엽고 하찮은" 톤에
# 제일 가깝다고 실제로 들어보고 결정했었으나(voice_picker 실험), 교수님 피드백으로 Zephyr로
# 교체함(2026-07-28, docs/progress.md 참고).
LIVE_VOICE_NAME = os.getenv("LIVE_VOICE_NAME", "Zephyr")
IDENTIFY_TIMEOUT_SEC = 8.0
# 기본 대화 상태(퀴즈 제외)에서 IDLE_SLEEP_SEC(core/idle_watcher.py)만큼 사용자가 조용하면
# SLEEPY로 전환하고 팬/틸트 추적도 멈춘다 — 사용자 요청(2026-07-29). display/emotions/
# sleepy.py·wake.py는 v1(capston_mk1/motirobotics)에서 재이식.
IDLE_WATCHER_POLL_SEC = 1.0
# SLEEPY 상태 배경음("드르렁... 쿠우...") — scripts/generate_snore_audio.py로 한 번 생성해
# 캐싱해둔 파일을 읽기만 한다(런타임에 API를 다시 부르지 않음, 반복 간격도 그래야 안정적).
SNORE_CLIP_PATH = os.path.join(_REPO_ROOT, "assets", "audio", "snore.wav")
SNORE_GAP_SEC = 1.0
SNORE_POLL_SEC = 0.2


def _load_snore_clip() -> tuple[bytes, float] | tuple[None, None]:
    """SNORE_CLIP_PATH를 읽어 (24kHz mono int16 PCM 바이트, 길이(초))를 반환한다.
    파일이 없거나 포맷이 안 맞으면(scripts/generate_snore_audio.py를 아직 안 돌렸거나
    포맷이 바뀐 경우) (None, None) — 호출부가 스누즈 배경음 없이 진행한다."""
    if not os.path.exists(SNORE_CLIP_PATH):
        print(f"ℹ️  {SNORE_CLIP_PATH}가 없어 SLEEPY 배경음 없이 진행합니다 "
              f"(scripts/generate_snore_audio.py로 생성할 수 있습니다).")
        return None, None
    with wave.open(SNORE_CLIP_PATH, "rb") as wf:
        if wf.getframerate() != OUTPUT_RATE or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            print(f"⚠️ {SNORE_CLIP_PATH}의 포맷이 예상과 다릅니다"
                  f"({OUTPUT_RATE}Hz mono int16 필요) — SLEEPY 배경음을 건너뜁니다.")
            return None, None
        pcm = wf.readframes(wf.getnframes())
    duration_sec = len(pcm) / 2 / OUTPUT_RATE
    return pcm, duration_sec


def open_port() -> tuple[PortHandler, PacketHandler]:
    port = PortHandler(C.DEVICENAME)
    pkt = PacketHandler(C.PROTOCOL_VERSION)
    if not port.openPort():
        raise RuntimeError(f"포트를 열 수 없습니다: {C.DEVICENAME}")
    if not port.setBaudRate(C.BAUDRATE):
        raise RuntimeError(f"보드레이트 설정 실패: {C.BAUDRATE}")
    print(f"✅ 포트 연결됨: {C.DEVICENAME} @ {C.BAUDRATE}")
    return port, pkt


def wait_for_identification(shared_state: dict, timeout: float) -> str | None:
    """face_tracker_worker(brain=...)가 갱신하는 shared_state['detected_user']를 최대
    timeout초 동안 폴링한다. 얼굴인식과 팬/틸트 추적이 같은 스레드/카메라 세션에서 함께
    돌아가므로 별도로 카메라를 열고 닫을 필요가 없다(2026-07-27부터 — 이전엔 대화 시작 전
    identify_user_via_webcam()이 카메라를 따로 열어 한 번만 인식했었음). 세션 시작 시
    확정된 이름은 그 세션 내내 고정하고, 이후 다른 사람이 감지돼도 무시한다(사용자 결정,
    docs/integration-points.md — face.py의 is_initial_recognition_active가 이미 이 동작을
    구현하고 있음: 이름이 한 번 확정되면 그 뒤로는 재인식 자체를 시도하지 않는다)."""
    print(f"👀 얼굴 인식 대기 중 (최대 {timeout:.0f}초)...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        name = shared_state.get('detected_user')
        if name not in (None, "Unknown", "Thinking..."):
            print(f"✅ 인식됨: {name}")
            return name
        time.sleep(0.15)

    print("🤷 인식 실패 — 이름 모른 채 진행합니다.")
    return None


async def run_conversation(name_state: dict, facts_summary: str | None, emotion_queue: "queue.Queue",
                            motion_tools: list | None = None, shared_state: dict | None = None, brain=None,
                            quiz_ui_q=None, quiz_busy: threading.Event | None = None,
                            port=None, pkt=None, lock=None, home_pan: int | None = None, home_tilt: int | None = None,
                            quiz_num_questions: int = 5, quiz_session_out: dict | None = None,
                            history_out: list | None = None):
    """Live 세션을 열고 대화가 끝날 때까지 실행한다. 종료 시 세션 로그를 반환.

    name_state: {"name": <세션 시작 시점 확정된 이름 또는 None>}. 처음 보는 사람이면
    remember_fact의 첫 호출(field="name")이 이 딕셔너리를 제자리에서 갱신한다 —
    호출자는 이 함수가 끝난 뒤 name_state["name"]으로 최종 확정 이름을 읽을 수 있다.

    quiz_ui_q가 None이면 퀴즈 모드 자체가 비활성(기존 호출부/테스트 스크립트와 호환) —
    2026-07-28 하찮미 실험 2차용 기능이라 기본값은 항상 꺼져 있다.

    quiz_session_out({"session": None})과 history_out(빈 리스트)은 name_state와 같은
    "가변 컨테이너로 결과 돌려받기" 패턴 — 둘 다 세션이 **시작되는 시점에** 채워지므로,
    세션이 정상 종료([대화종료])가 아니라 Ctrl+C/크래시로 끝나도 호출자가 그때까지 쌓인
    퀴즈 결과(export_log)와 대화록을 잃지 않는다. 이전엔 정상 반환 경로에서만 결과를
    복사해줘서, 참가자가 작별 인사 없이 끝나 실험자가 Ctrl+C로 세션을 끊으면 그 참가자의
    퀴즈 데이터·대화록·결과지가 통째로 사라졌다(2026-08-07 전체 코드 검사로 발견)."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 대화를 시작할 수 없습니다.")
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    loop = asyncio.get_event_loop()

    # 퀴즈 모드의 짜증유발(annoying) 힌트 거절이 10~12초 지연 후 히든 턴으로 도착해야
    # 하는데, 그 시점엔 session 객체가 아직 없다(connect()가 tools=[...]를 요구하는데
    # 그 tools 안에 이 콜백을 참조하는 퀴즈 툴이 들어있어야 함) — session_holder라는
    # 가변 딕셔너리로 우회한다(remember_fact의 name_state와 같은 발상).
    session_holder = {"session": None}
    # recv_loop가 갱신한다: 오디오 데이터가 도착하는 동안은 clear, 턴이 끝나면(sc.turn_complete
    # 또는 sc.interrupted) set — inject_turn()이 "로봇이 지금 말하는 중인가"를 알 수 있는
    # 유일한 신호. 초기값은 set(아직 아무도 말하지 않음).
    speaking_done = asyncio.Event()
    speaking_done.set()

    async def inject_turn(text: str):
        s = session_holder["session"]
        if s is None:
            return
        # 로봇이 아직 이전 턴을 말하는 도중이면 곧장 보내지 않고 기다린다 — 안 그러면 새
        # 응답 생성이 이전 발화 위에 겹쳐서 음성이 끊기거나 뭉개지는 사고가 난다(2026-07-30).
        await speaking_done.wait()
        await asyncio.sleep(POST_SPEECH_DRAIN_SEC)
        await s.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )

    tools = []
    tool_fns = {}
    remember_fact = make_remember_fact_tool(name_state, shared_state=shared_state, brain=brain)
    tools.append(remember_fact)
    tool_fns["remember_fact"] = remember_fact

    forget_me = make_forget_me_tool(name_state, shared_state=shared_state, brain=brain)
    tools.append(forget_me)
    tool_fns["forget_me"] = forget_me

    for tool_fn in motion_tools or []:
        tools.append(tool_fn)
        tool_fns[tool_fn.__name__] = tool_fn

    # quiz_session을 set_emotion보다 먼저 만든다 — 짜증유발 모드에서는 표정을
    # neutral/thinking으로만 제한해야 하는데(2026-07-31), 그러려면 set_emotion이 만들어질
    # 때 이미 quiz_session 참조를 쥐고 있어야 한다(quiz_ui_q가 없으면 quiz_session은 그냥
    # None으로 남고 make_set_emotion_tool도 제한 없이 평소대로 동작한다).
    quiz_session = None
    if quiz_ui_q is not None:
        quiz_motion_ctx = (port, pkt, lock, shared_state, home_pan, home_tilt)
        (start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early,
         quiz_session) = make_quiz_tools(
            quiz_ui_q, quiz_busy or threading.Event(), quiz_motion_ctx, inject_turn, loop,
            speaking_done, emotion_queue=emotion_queue, num_questions=quiz_num_questions,
        )
        for quiz_tool_fn in (start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early):
            tools.append(quiz_tool_fn)
            tool_fns[quiz_tool_fn.__name__] = quiz_tool_fn
    if quiz_session_out is not None:
        # 세션이 어떻게 끝나든 호출자가 export_log()를 뽑을 수 있도록 참조를 지금 넘겨둔다
        # (위 docstring 참고 — 정상 반환 경로에서만 복사하면 Ctrl+C 시 데이터가 유실된다).
        quiz_session_out["session"] = quiz_session

    set_emotion = make_set_emotion_tool(emotion_queue, quiz_session=quiz_session)
    tools.append(set_emotion)
    tool_fns["set_emotion"] = set_emotion

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=build_persona_system_instruction(name=name_state["name"], facts_summary=facts_summary),
        tools=tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE_NAME)
            )
        ),
    )

    # history_out이 주어지면 그 리스트에 직접 누적한다 — 세션이 Ctrl+C로 끊겨도
    # 호출자가 그때까지의 턴 기록을 그대로 들고 있게 하기 위함(위 docstring 참고).
    session_history: list[str] = history_out if history_out is not None else []
    stop_event = asyncio.Event()

    print(f"모델: {LIVE_MODEL} — 마이크에 대고 말해보세요. Ctrl+C로 언제든 종료.")

    # 스피커→마이크 에코로 인한 barge-in 오탐 대응(docs/integration-points.md 참고) — AEC
    # 엔진 하나를 만들어 마이크/스피커 콜백이 공유한다. ENABLE_AEC=false로 끌 수 있다.
    echo_canceller = EchoCanceller() if ENABLE_AEC else None

    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        session_holder["session"] = session
        with MicStreamer(loop, echo_canceller=echo_canceller) as mic, Speaker(echo_canceller=echo_canceller) as speaker:
            # Fenrir 프리셋은 여전히 성인 성우 음색이라, 재생 직전에 피치+포먼트를 시프트해
            # 더 앳된 톤으로 바꾼다(media/voice_shift.py). 버퍼링 때문에 발화 시작마다
            # ~0.5~0.8초 지연이 추가되지만, 스트리밍 자체가 계속 밀리지는 않는다.
            shifter = VoiceShifter(speaker.play, sample_rate=OUTPUT_RATE) if ENABLE_VOICE_SHIFT else None
            if shifter:
                shifter.start()

            # SLEEPY 배경음 — API를 다시 부르지 않고 캐시된 클립만 읽는다(_load_snore_clip
            # 참고). 파일이 없으면 (None, None)이라 snore_player()가 그냥 아무것도 안 함.
            snore_pcm, snore_duration_sec = _load_snore_clip()

            turn_user, turn_moti = [], []
            # idle_watcher()와 recv_loop() 둘 다 읽고/쓰는 공유 상태라 nonlocal 없이
            # 클로저에서 갱신 가능하도록 리스트(가변 컨테이너)로 감싼다(core/quiz_tools.py의
            # _pending_stall 딕셔너리 패턴과 같은 이유). "활동"은 사용자 발화뿐 아니라
            # 로봇이 말하는 중(오디오 청크 도착)이거나 제스처/춤/퀴즈 리액션 모션을
            # 실행 중인(quiz_busy가 곧 motion_busy) 경우도 포함한다 — 로봇이 뭔가
            # 하고 있는 동안은 사용자가 IDLE_SLEEP_SEC만큼 조용해도 잠들면 안 된다는 요구사항.
            last_activity_time = [time.monotonic()]
            is_sleeping = [False]

            # Live API는 기본적으로 사용자 입력을 기다렸다가 응답한다 — 로봇이 먼저 인사를
            # 건네게 하려면 연결 직후 텍스트 턴을 하나 보내 말문을 열어줘야 한다(공식 가이드
            # 권장 패턴). 오디오 경로가 아니라 input_transcription에도 안 잡힌다.
            await session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text="(방금 사용자가 로봇 앞에 도착했습니다. 사용자가 말하기를 기다리지 말고, 당신이 먼저 자연스럽게 인사를 건네며 대화를 시작하세요.)")],
                ),
                turn_complete=True,
            )

            async def send_loop():
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(mic.queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={INPUT_RATE}")
                    )

            async def recv_loop():
                # session.receive()는 턴 하나짜리 스트림이라 턴이 끝나면 for문이 종료된다.
                # 다음 턴을 계속 받으려면 stop_event가 설 때까지 receive()를 다시 호출해야 한다.
                while not stop_event.is_set():
                    async for message in session.receive():
                        sc = message.server_content
                        if sc and sc.interrupted:
                            speaker.stop_immediately()
                            if shifter:
                                shifter.reset()
                            # 재생을 강제로 끊었으니(barge-in 등) 더 이상 "말하는 중"이 아니다 —
                            # inject_turn()이 여기서 계속 기다리며 멈춰있지 않게 한다.
                            speaking_done.set()

                        if message.data:
                            if shifter:
                                shifter.feed(message.data)
                            else:
                                speaker.play(message.data)
                            # 로봇이 말하는 중 — 사용자가 조용히 듣고만 있어도 idle-sleep이
                            # 끼어들면 안 되고, inject_turn()도 이 턴이 끝날 때까지 기다려야
                            # 겹쳐 말하지 않는다(2026-07-30).
                            last_activity_time[0] = time.monotonic()
                            speaking_done.clear()

                        if sc and sc.input_transcription and sc.input_transcription.text:
                            turn_user.append(sc.input_transcription.text)
                            last_activity_time[0] = time.monotonic()
                        if sc and sc.output_transcription and sc.output_transcription.text:
                            turn_moti.append(sc.output_transcription.text)

                        if message.tool_call:
                            responses = []
                            for fc in message.tool_call.function_calls:
                                fn = tool_fns.get(fc.name)
                                try:
                                    result = fn(**(fc.args or {})) if fn else f"unknown tool {fc.name}"
                                except Exception as e:
                                    # 2026-07-31 코드 리뷰로 발견: 여기서 예외가 나면(툴 구현
                                    # 버그, 모델이 잘못된 인자를 준 경우 등) asyncio.gather를
                                    # 통해 전파되어 세션 전체가 죽고, session_history/quiz_log가
                                    # 빈 채로 남아 대화록·결과지·퀴즈 결과가 통째로 유실됐다
                                    # (물리적 안전 정리는 launcher.py의 finally가 여전히 실행
                                    # 하므로 모터 쪽은 안전함 — 유실되는 건 연구/대화 데이터).
                                    # 툴 하나의 실패를 그 자리에서 흡수해 세션이 계속되게 한다.
                                    print(f"❌ 툴 호출 실패: {fc.name}({fc.args}) -> {e!r}")
                                    result = f"tool call failed: {e}"
                                print(f"\n  🔧 {fc.name}({fc.args}) -> {result}")
                                responses.append(
                                    types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result})
                                )
                            await session.send_tool_response(function_responses=responses)

                        if sc and sc.turn_complete:
                            if shifter:
                                # 버퍼 임계값(기본 500ms) 미만으로 남은 발화 꼬리를 흘려보낸다
                                # — 안 하면 매 턴 마지막 조각이 조용히 잘려나간다.
                                shifter.flush()
                            # 서버가 이 턴의 생성을 끝냈다 — inject_turn()이 기다리고 있었다면
                            # 이제 진행해도 된다(POST_SPEECH_DRAIN_SEC만큼 더 기다려 로컬
                            # 버퍼/재생 꼬리까지 흘려보낸 뒤 다음 턴을 보낸다).
                            speaking_done.set()
                            u = "".join(turn_user).strip()
                            m_raw = "".join(turn_moti).strip()
                            m_clean, should_end = extract_exit_tag(m_raw)
                            if u or m_clean:
                                session_history.append(f"User: {u} | Moti: {m_clean}")
                                print(f"\n[나] {u}\n[모티] {m_clean}")
                            turn_user.clear()
                            turn_moti.clear()
                            if should_end:
                                print("\n👋 [대화종료] 감지 — 세션을 마칩니다.")
                                stop_event.set()
                                break
                        if stop_event.is_set():
                            break

            async def idle_watcher():
                """기본 대화 상태(퀴즈 제외)에서 IDLE_SLEEP_SEC 동안 아무 활동도 없으면
                SLEEPY로 전환하고 팬/틸트 추적을 멈춘다(shared_state['mode']='sleeping' —
                vision/face.py가 'tracking'이 아닌 모드는 추적을 자동으로 건너뜀, 퀴즈 모드의
                시선회피 모션과 같은 방식). "활동"은 사용자 발화뿐 아니라 로봇이 말하는 중
                (recv_loop의 message.data 처리부에서 갱신)이거나 제스처/춤/퀴즈 리액션 모션을
                실행 중인 경우도 포함 — 로봇이 뭔가 하고 있으면 사용자가 조용해도 잠들면 안
                된다는 요구사항(quiz_busy는 core/motion_tools.py/core/quiz_tools.py가 공유하는
                busy 게이트라 Layer 1/2 제스처든 퀴즈 리액션이든 전부 여기서 잡힌다).
                판단 자체는 core/idle_watcher.py의 순수 함수가 하고, 여기서는 그 결과에 따라
                emotion_queue/shared_state만 건드린다. 사용자가 다시 말하기 시작하면
                (quiz_session 진행 중이든 아니든 우선 깨움) 즉시 추적을 재개하고 AWAKENING을
                한 번 보여준다 — WAKE 애니메이션(약 2.5초)이 끝나면 시각적으로 NEUTRAL과
                동일해지므로(display/emotions/wake.py) 별도로 되돌릴 필요 없음(모델이 이어서
                set_emotion을 부르면 그게 그대로 반영됨)."""
                while not stop_event.is_set():
                    await asyncio.sleep(IDLE_WATCHER_POLL_SEC)
                    quiz_active = quiz_session is not None and quiz_session.active
                    # 퀴즈 진행 중엔(문제를 오래 들여다보며 생각하는 조용한 구간 포함) 그
                    # 자체를 활동으로 본다 — 안 그러면 사용자/로봇 둘 다 조용한 "생각하는
                    # 시간"이 누적되다가, 퀴즈가 막 끝난 시점에 그 누적된 idle_for가 그대로
                    # 남아 있어 곧바로(대기하던 타이머가) SLEEPY로 튀어버리는 사고가 났었음
                    # (실측으로 재현: 퀴즈 종료 직후 즉시 sleepy 전환).
                    if quiz_active or (quiz_busy is not None and quiz_busy.is_set()):
                        last_activity_time[0] = time.monotonic()
                    idle_for = time.monotonic() - last_activity_time[0]
                    action = decide_idle_action(idle_for, is_sleeping[0], quiz_active)

                    if action == "wake":
                        is_sleeping[0] = False
                        print("👀 사용자 발화 감지 — 깨어납니다.")
                        if shared_state is not None:
                            shared_state['mode'] = 'tracking'
                        emotion_queue.put("AWAKENING")
                        # 코골이 소리가 재생/대기 중이었다면 즉시 끊는다 — snore_player()의
                        # 다음 폴링을 기다리면(최대 SNORE_POLL_SEC) 깬 직후에도 잠깐 더
                        # 들릴 수 있어서, 깨우는 시점에 확실하게 끊어준다.
                        speaker.stop_immediately()
                    elif action == "sleep":
                        is_sleeping[0] = True
                        print(f"💤 {IDLE_SLEEP_SEC:.0f}초간 조용해서 sleepy 상태로 전환합니다.")
                        if shared_state is not None:
                            shared_state['mode'] = 'sleeping'
                        emotion_queue.put("SLEEPY")

            async def snore_player():
                """SLEEPY인 동안 캐시된 코골이 클립을 SNORE_GAP_SEC 간격으로 반복 재생한다
                ("드르렁... 쿠우..." 1초 정적 반복, 사용자 요청). 큰 통짜 sleep 대신
                SNORE_POLL_SEC 단위로 쪼개 대기해야, 자는 도중 깨어났을 때(is_sleeping[0]이
                False로 바뀔 때) 다음 재생 전에 빠르게 멈출 수 있다(추가로 idle_watcher의
                wake 분기가 speaker.stop_immediately()로 즉시 끊기도 함)."""
                if snore_pcm is None:
                    return
                while not stop_event.is_set():
                    if not is_sleeping[0]:
                        await asyncio.sleep(SNORE_POLL_SEC)
                        continue
                    speaker.play(snore_pcm)
                    remaining = snore_duration_sec + SNORE_GAP_SEC
                    while remaining > 0 and is_sleeping[0] and not stop_event.is_set():
                        await asyncio.sleep(SNORE_POLL_SEC)
                        remaining -= SNORE_POLL_SEC

            await asyncio.gather(send_loop(), recv_loop(), idle_watcher(), snore_player())
            if shifter:
                shifter.close()
            if speaker.underrun_count:
                print(f"⚠️ 스피커 언더런 {speaker.underrun_count}회, 총 {speaker.underrun_ms_total:.0f}ms 무음 재생됨 "
                      f"— VoiceShifter 처리가 실시간을 못 따라간 신호. VOICE_SHIFT_BUFFER_MS를 늘려볼 것.")

    return session_history


def main():
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
    except ImportError:
        pass

    port, pkt = open_port()
    lock = threading.Lock()
    shared_state = {"mode": "tracking"}
    I.initialize_robot(port, pkt, lock)
    home_pan = I.MOTOR_HOME_POSITIONS.get(C.PAN_ID, 2081)
    home_tilt = I.MOTOR_HOME_POSITIONS.get(C.TILT_ID, 2071)

    print("▶ RobotBrain 초기화 중 (모델 로딩)...")
    brain = RobotBrain()

    # 얼굴인식(brain)과 팬/틸트 추적을 같은 스레드/카메라 세션에서 함께 돌린다 — 더 이상
    # 대화 시작 전 카메라를 따로 열어 한 번만 인식하지 않는다(2026-07-27). 이름은 세션
    # 시작 시 확정되면 고정되고 이후 다른 사람이 감지돼도 무시한다(wait_for_identification
    # 참고, 사용자 결정).
    track_stop = threading.Event()
    video_frame_q: "queue.Queue" = queue.Queue(maxsize=1)
    t_face = threading.Thread(
        target=F.face_tracker_worker,
        args=(port, pkt, lock, track_stop, video_frame_q, shared_state),
        kwargs=dict(camera_index=camera_index, draw_mesh=False, print_debug=False, brain=brain),
        name="face", daemon=True,
    )
    t_face.start()

    name = wait_for_identification(shared_state, IDENTIFY_TIMEOUT_SEC)
    facts_summary = profiles.load_profile_for_chat(name) if name else None
    if facts_summary:
        print(f"📋 기존 프로필 로드:\n{facts_summary}")

    # remember_fact가 대화 중 처음으로 이름을 알아내면 이 딕셔너리를 제자리에서 갱신한다
    # (core/memory_tools.py 참고) — 세션이 끝난 뒤 name_state["name"]으로 최종 이름을 읽는다.
    name_state = {"name": name}

    emotion_queue: "queue.Queue" = queue.Queue()
    # Layer 1/2(LLM이 부르는 제스처)와 퀴즈 모드 리액션 모션(core/quiz_tools.py)이 같은
    # busy 게이트를 공유해야 서로 다른 관절이라도 동시에 실행되며 충돌하지 않는다.
    motion_busy = threading.Event()
    play_gesture, express_gesture = make_motion_tools(
        port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue, busy=motion_busy
    )
    display_stop = threading.Event()

    def run_display():
        app = RobotFaceApp(emotion_queue=emotion_queue, stop_event=display_stop)
        app.run()

    t_display = threading.Thread(target=run_display, name="display", daemon=True)
    t_display.start()

    # 퀴즈 모드 사진 창 — display/main.py의 pygame 얼굴 UI와 완전히 별개 프로세스라
    # 이 신규 기능의 버그가 검증된 얼굴 UI를 절대 건드리지 않는다(2026-07-28 하찮미
    # 실험 2차, docs/progress.md 참고). quiz_ui_q가 항상 존재하므로 퀴즈 기능은 사실상
    # 상시 활성 — 트리거되지 않으면(사용자가 "퀴즈 풀자" 등을 말하지 않으면) 그냥 안 쓰인다.
    quiz_ui_q: "multiprocessing.Queue" = multiprocessing.Queue()
    quiz_proc = multiprocessing.Process(target=quiz_window_process, args=(quiz_ui_q,), daemon=True)
    quiz_proc.start()

    # 둘 다 run_conversation이 "시작 시점에" 채우는 가변 컨테이너 — 세션이 [대화종료]가
    # 아니라 Ctrl+C로 끝나도 아래 finally에서 그때까지의 대화록/퀴즈 결과를 저장할 수 있다.
    session_history: list[str] = []
    quiz_holder: dict = {"session": None}
    try:
        asyncio.run(
            run_conversation(
                name_state, facts_summary, emotion_queue,
                motion_tools=[play_gesture, express_gesture],
                shared_state=shared_state, brain=brain,
                quiz_ui_q=quiz_ui_q, quiz_busy=motion_busy,
                port=port, pkt=pkt, lock=lock, home_pan=home_pan, home_tilt=home_tilt,
                quiz_session_out=quiz_holder, history_out=session_history,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt — 대화를 종료합니다(지금까지의 대화록/퀴즈 결과는 저장됩니다).")
    finally:
        display_stop.set()

        quiz_ui_q.put("__QUIT__")
        quiz_proc.join(timeout=3.0)

        track_stop.set()
        t_face.join(timeout=5.0)
        print("▶️  종료 — 모든 모터 토크 OFF")
        I.shutdown_all_motors(port, pkt, lock)
        port.closePort()

        final_name = name_state["name"]
        if final_name:
            # facts가 너무 많이 쌓였으면(v2의 batch_update_summary에 대응 — v3엔 없었음,
            # docs/integration-points.md) 세션 종료마다 유사 항목을 병합/압축한다. forget_me로
            # 이름 자체가 사라진 경우(final_name은 남아있어도 프로필은 이미 삭제됨) get_facts가
            # 빈 리스트를 반환해 조용히 스킵된다.
            profiles.consolidate_facts(final_name)

        if final_name and session_history:
            print("💾 대화 결과지를 생성합니다...")
            # 대화 중 remember_fact로 새로 저장된 사실을 반영하려면 세션 시작 전에
            # 로드해둔 facts_summary가 아니라 지금 시점 기준으로 다시 읽어야 한다.
            latest_facts_summary = profiles.load_profile_for_chat(final_name)
            report_manager.generate_and_save_reports(final_name, "\n".join(session_history), latest_facts_summary)
        elif session_history:
            print("ℹ️  이름을 몰라 결과지는 생성하지 않습니다 (대화 자체는 정상 진행됨).")

        # 연구 데이터 — 문항별 모드/정답여부/힌트요청여부/타임스탬프(core/quiz_state.py
        # export_log() 참고)를 1/2/3번 모드별 파일로 나눠 저장한다(core/quiz_export.py).
        # export_log()를 여기서(세션 종료 방식과 무관하게 항상 실행되는 finally에서) 직접
        # 뽑는다 — Ctrl+C로 끊긴 세션도 그때까지 채점된 문항은 전부 보존된다.
        quiz_session = quiz_holder["session"]
        quiz_log = quiz_session.export_log() if quiz_session is not None else []
        save_quiz_results(final_name, quiz_log)


if __name__ == "__main__":
    main()
