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
    user_result/에 대화록+결과지가 남는다.
"""
import asyncio
import os
import queue
import sys
import threading
import time

from dynamixel_sdk import PacketHandler, PortHandler

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core import report_manager
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_forget_me_tool, make_remember_fact_tool
from core.motion_tools import make_motion_tools
from core.utils import build_persona_system_instruction, extract_exit_tag
from display.main import RobotFaceApp
from hardware import config as C
from hardware import init as I
from media.audio_manager import ENABLE_AEC, INPUT_RATE, OUTPUT_RATE, EchoCanceller, MicStreamer, Speaker
from media.voice_shift import ENABLE_VOICE_SHIFT, VoiceShifter
from vision import face as F
from vision.vision_brain import RobotBrain

LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")
# 30종 프리셋 중 Fenrir + 피치/포먼트 시프트(media/voice_shift.py)가 "귀엽고 하찮은" 톤에
# 제일 가깝다고 실제로 들어보고 결정했었으나(voice_picker 실험), 교수님 피드백으로 Zephyr로
# 교체함(2026-07-28, docs/progress.md 참고).
LIVE_VOICE_NAME = os.getenv("LIVE_VOICE_NAME", "Zephyr")
IDENTIFY_TIMEOUT_SEC = 8.0


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
                            motion_tools: list | None = None, shared_state: dict | None = None, brain=None):
    """Live 세션을 열고 대화가 끝날 때까지 실행한다. 종료 시 세션 로그를 반환.

    name_state: {"name": <세션 시작 시점 확정된 이름 또는 None>}. 처음 보는 사람이면
    remember_fact의 첫 호출(field="name")이 이 딕셔너리를 제자리에서 갱신한다 —
    호출자는 이 함수가 끝난 뒤 name_state["name"]으로 최종 확정 이름을 읽을 수 있다."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 대화를 시작할 수 없습니다.")
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    tools = []
    tool_fns = {}
    remember_fact = make_remember_fact_tool(name_state, shared_state=shared_state, brain=brain)
    tools.append(remember_fact)
    tool_fns["remember_fact"] = remember_fact

    forget_me = make_forget_me_tool(name_state, shared_state=shared_state, brain=brain)
    tools.append(forget_me)
    tool_fns["forget_me"] = forget_me

    set_emotion = make_set_emotion_tool(emotion_queue)
    tools.append(set_emotion)
    tool_fns["set_emotion"] = set_emotion

    for tool_fn in motion_tools or []:
        tools.append(tool_fn)
        tool_fns[tool_fn.__name__] = tool_fn

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

    session_history: list[str] = []
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    print(f"모델: {LIVE_MODEL} — 마이크에 대고 말해보세요. Ctrl+C로 언제든 종료.")

    # 스피커→마이크 에코로 인한 barge-in 오탐 대응(docs/integration-points.md 참고) — AEC
    # 엔진 하나를 만들어 마이크/스피커 콜백이 공유한다. ENABLE_AEC=false로 끌 수 있다.
    echo_canceller = EchoCanceller() if ENABLE_AEC else None

    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        with MicStreamer(loop, echo_canceller=echo_canceller) as mic, Speaker(echo_canceller=echo_canceller) as speaker:
            # Fenrir 프리셋은 여전히 성인 성우 음색이라, 재생 직전에 피치+포먼트를 시프트해
            # 더 앳된 톤으로 바꾼다(media/voice_shift.py). 버퍼링 때문에 발화 시작마다
            # ~0.5~0.8초 지연이 추가되지만, 스트리밍 자체가 계속 밀리지는 않는다.
            shifter = VoiceShifter(speaker.play, sample_rate=OUTPUT_RATE) if ENABLE_VOICE_SHIFT else None
            if shifter:
                shifter.start()

            turn_user, turn_moti = [], []

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

                        if message.data:
                            if shifter:
                                shifter.feed(message.data)
                            else:
                                speaker.play(message.data)

                        if sc and sc.input_transcription and sc.input_transcription.text:
                            turn_user.append(sc.input_transcription.text)
                        if sc and sc.output_transcription and sc.output_transcription.text:
                            turn_moti.append(sc.output_transcription.text)

                        if message.tool_call:
                            responses = []
                            for fc in message.tool_call.function_calls:
                                fn = tool_fns.get(fc.name)
                                result = fn(**(fc.args or {})) if fn else f"unknown tool {fc.name}"
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

            await asyncio.gather(send_loop(), recv_loop())
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
    play_gesture, express_gesture = make_motion_tools(
        port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue
    )
    display_stop = threading.Event()

    def run_display():
        app = RobotFaceApp(emotion_queue=emotion_queue, stop_event=display_stop)
        app.run()

    t_display = threading.Thread(target=run_display, name="display", daemon=True)
    t_display.start()

    session_history: list[str] = []
    try:
        session_history = asyncio.run(
            run_conversation(
                name_state, facts_summary, emotion_queue,
                motion_tools=[play_gesture, express_gesture],
                shared_state=shared_state, brain=brain,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt — 대화를 종료합니다.")
    finally:
        display_stop.set()

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


if __name__ == "__main__":
    main()
