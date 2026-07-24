"""로봇 없이 되는 모든 조각을 하나로 이어붙인 통합 데모.

웹캠(얼굴인식) → 프로필 로드 → 페르소나 조립 → Live API 음성 대화(remember_fact,
set_emotion 툴 연결) → 표정 UI(display) → [대화종료] 감지 시 결과지 생성, 이 흐름을
전부 실제로 연결한다. 모터가 필요한 부분(vision/face.py의 팬/틸트 추적,
core/motion_tools.py의 제스처)은 로봇이 없어서 이 스크립트엔 없다 — 로봇이 생기면
이 파일을 launcher.py로 확장하면서 그 둘을 추가하면 된다(docs/integration-points.md).

**알려진 제한**: 웹캠으로 얼굴을 못 알아보면(처음 보는 사람이거나 art_brain에 등록 안 됨)
이번 세션은 이름 없이 진행되고 remember_fact 툴 자체를 붙이지 않는다 — 이름 없이는
무엇의 profile에 저장할지 알 수 없기 때문. 얼굴을 미리 등록해두려면
`scripts/test_vision_brain.py`를 먼저 실행해서 'r' 키로 등록할 것.

사용:
    python scripts/run_no_robot.py [카메라 인덱스]
    Ctrl+C 또는 대화 중 자연스러운 작별 인사([대화종료])로 종료 — 이름을 아는 경우
    user_result/에 대화록+결과지가 남는다.
"""
import asyncio
import os
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core import report_manager
from core import suppress
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_remember_fact_tool
from core.utils import build_persona_system_instruction, extract_exit_tag
from display.main import RobotFaceApp
from vision.vision_brain import RobotBrain

LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")
INPUT_RATE = 16000
OUTPUT_RATE = 24000
IDENTIFY_TIMEOUT_SEC = 8.0


def identify_user_via_webcam(camera_index: int, brain: RobotBrain, timeout: float) -> str | None:
    """웹캠으로 최대 timeout초 동안 얼굴 인식을 시도한다. 못 찾으면 None."""
    cv2, _ = suppress.import_cv2_mp()
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"⚠️ 카메라({camera_index}) 열기 실패 — 이름 모른 채 진행합니다.")
        return None

    print(f"👀 얼굴 인식 시도 중 (최대 {timeout:.0f}초)...")
    deadline = time.monotonic() + timeout
    name = None
    try:
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            _, recognized = brain.recognize_face(frame)
            if recognized not in (None, "Thinking..."):
                name = recognized
                break
            time.sleep(0.15)
    finally:
        cap.release()

    if name:
        print(f"✅ 인식됨: {name}")
    else:
        print("🤷 인식 실패 — 이름 모른 채 진행합니다.")
    return name


class MicStreamer:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self._stream = sd.InputStream(
            samplerate=INPUT_RATE, channels=1, dtype="int16",
            blocksize=1600, callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        chunk = bytes(indata)
        self._loop.call_soon_threadsafe(self.queue.put_nowait, chunk)

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


class Speaker:
    def __init__(self):
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._leftover = b""
        self._stream = sd.OutputStream(
            samplerate=OUTPUT_RATE, channels=1, dtype="int16",
            blocksize=2400, callback=self._callback,
        )

    def _callback(self, outdata, frames, time_info, status):
        need = frames * 2
        buf = self._leftover
        while len(buf) < need:
            try:
                buf += self._q.get_nowait()
            except queue.Empty:
                break
        chunk, self._leftover = buf[:need], buf[need:]
        chunk = chunk + b"\x00" * (need - len(chunk))
        outdata[:] = np.frombuffer(chunk, dtype="int16").reshape(-1, 1)

    def play(self, pcm_bytes: bytes):
        self._q.put(pcm_bytes)

    def stop_immediately(self):
        with self._q.mutex:
            self._q.queue.clear()
        self._leftover = b""

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


async def run_conversation(name: str | None, facts_summary: str | None, emotion_queue: "queue.Queue"):
    """Live 세션을 열고 대화가 끝날 때까지 실행한다. 종료 시 세션 로그를 반환."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 대화를 시작할 수 없습니다.")
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    tools = []
    tool_fns = {}
    if name:
        remember_fact = make_remember_fact_tool(name)
        tools.append(remember_fact)
        tool_fns["remember_fact"] = remember_fact
    else:
        print("ℹ️  이름을 몰라 remember_fact 툴은 이번 세션에 붙이지 않습니다.")

    set_emotion = make_set_emotion_tool(emotion_queue)
    tools.append(set_emotion)
    tool_fns["set_emotion"] = set_emotion

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=build_persona_system_instruction(name=name, facts_summary=facts_summary),
        tools=tools,
    )

    session_history: list[str] = []
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    print(f"모델: {LIVE_MODEL} — 마이크에 대고 말해보세요. Ctrl+C로 언제든 종료.")

    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        with MicStreamer(loop) as mic, Speaker() as speaker:
            turn_user, turn_moti = [], []

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
                async for message in session.receive():
                    sc = message.server_content
                    if sc and sc.interrupted:
                        speaker.stop_immediately()

                    if message.data:
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

    return session_history


def main():
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
    except ImportError:
        pass

    print("▶ RobotBrain 초기화 중 (모델 로딩)...")
    brain = RobotBrain()
    name = identify_user_via_webcam(camera_index, brain, IDENTIFY_TIMEOUT_SEC)
    facts_summary = profiles.load_profile_for_chat(name) if name else None
    if facts_summary:
        print(f"📋 기존 프로필 로드:\n{facts_summary}")

    emotion_queue: "queue.Queue" = queue.Queue()
    display_stop = threading.Event()

    def run_display():
        app = RobotFaceApp(emotion_queue=emotion_queue, stop_event=display_stop)
        app.run()

    t_display = threading.Thread(target=run_display, name="display", daemon=True)
    t_display.start()

    session_history: list[str] = []
    try:
        session_history = asyncio.run(run_conversation(name, facts_summary, emotion_queue))
    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt — 대화를 종료합니다.")
    finally:
        display_stop.set()
        if name and session_history:
            print("💾 대화 결과지를 생성합니다...")
            report_manager.generate_and_save_reports(name, "\n".join(session_history), facts_summary)
        elif session_history:
            print("ℹ️  이름을 몰라 결과지는 생성하지 않습니다 (대화 자체는 정상 진행됨).")


if __name__ == "__main__":
    main()
