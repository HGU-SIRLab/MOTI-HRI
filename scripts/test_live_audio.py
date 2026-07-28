"""Live API 실제 음성 테스트 — barge-in(끼어들기) 검증용.

이건 사람이 직접 마이크에 대고 말하면서 실행해야 의미가 있는 스크립트다.
텍스트만으로는 끼어들기를 재현할 수 없다(겹쳐 말해야 서버가 감지함).

무엇을 확인해야 하는가:
  1. 모티가 말하는 도중에 말을 걸어보세요(끼어들기). 콘솔에
     "🚨 interrupted=True — 재생 중단" 이 뜨고 스피커 소리가 즉시 멈추면 성공.
  2. 첫 마디가 나오기까지 체감 지연이 얼마나 되는지.
  3. 오류 없이 계속 대화가 이어지는지 (Ctrl+C로 종료).

사용:
    python scripts/test_live_audio.py
"""
import asyncio
import os
import queue
import sys
import threading

import numpy as np
import sounddevice as sd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_remember_fact_tool
from core.utils import build_persona_system_instruction

LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")
INPUT_RATE = 16000
OUTPUT_RATE = 24000
TEST_USER = "__테스트유저__"

# 목소리 A/B 테스트용 — 30개 프리셋 중 하나를 골라 들어본다(기본값 Leda="Youthful").
# python scripts/test_live_audio.py Puck 처럼 인자로 다른 후보를 바로 시도할 수 있다.
VOICE_NAME = sys.argv[1] if len(sys.argv) > 1 else os.getenv("LIVE_VOICE_NAME", "Leda")

# voice_name만으로는 성인스러운 느낌이 남아있어서, 실제 딜리버리(억양/텐션)를 프롬프트로
# 더 밀어붙여보는 실험 — 옛 Typecast 목소리처럼 "귀엽고 하찮은" 느낌을 노려본다.
# STYLE_HINT="" (빈 문자열) 이면 이 지시 없이 순수 voice_name 효과만 다시 들어볼 수 있다.
STYLE_HINT = os.getenv("VOICE_STYLE_HINT", (
    "\n\n[음성 스타일 지시 — 이 세션의 모든 발화에 최우선 적용]\n"
    "문장의 철자/맞춤법은 항상 정상적으로 쓰세요(예: '모야아', '싶어어'처럼 글자를 "
    "늘리거나 발음을 일부러 흐트러뜨려 쓰지 마세요 — 그렇게 표기하면 오히려 부자연스럽게 "
    "들립니다). 대신 목소리의 피치·속도·리듬만으로 귀엽고 하찮은 느낌을 내세요.\n"
    "특히 피치(음높이)를 지금 기본 음역보다 눈에 띄게 더 높게, 가성에 가까운 하이톤으로 "
    "말하세요 — 살짝 높이는 정도가 아니라 명확하게 한 톤 이상 더 높여야 합니다. "
    "말하는 속도는 조금 빠르고 통통 튀게, 텐션은 밝고 명랑하게. "
    "차분하고 낮은 어른스러운 톤은 절대 피하세요."
))


class MicStreamer:
    """sounddevice 콜백(별도 스레드)에서 들어오는 마이크 청크를 asyncio 쪽으로 넘긴다."""

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
    """받은 오디오를 재생 큐에 쌓고, interrupted 신호가 오면 큐를 비워 즉시 멈춘다."""

    def __init__(self):
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._leftover = b""
        self._stream = sd.OutputStream(
            samplerate=OUTPUT_RATE, channels=1, dtype="int16",
            blocksize=2400, callback=self._callback,
        )

    def _callback(self, outdata, frames, time_info, status):
        need = frames * 2  # int16 = 2 bytes/frame
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
        """barge-in 감지 시 호출 — 밀린 재생을 전부 버린다."""
        with self._q.mutex:
            self._q.queue.clear()
        self._leftover = b""

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


async def run():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
    except ImportError:
        pass

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 종료합니다.")
        return

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    profiles.forget_user(TEST_USER)
    remember_fact = make_remember_fact_tool({"name": TEST_USER})
    set_emotion = make_set_emotion_tool()
    tool_fns = {"remember_fact": remember_fact, "set_emotion": set_emotion}

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=build_persona_system_instruction(name=None, facts_summary=None) + STYLE_HINT,
        tools=[remember_fact, set_emotion],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME)
            )
        ),
    )

    loop = asyncio.get_event_loop()
    # 마이크로 말할 수 없는 상황(목소리 톤만 빨리 들어보고 싶을 때)을 위한 자동 모드 —
    # 사용자 음성 입력 없이 텍스트로 첫 턴만 트리거하고, 그 응답이 끝나면 자동 종료한다.
    auto_speak = os.getenv("AUTO_SPEAK", "1") != "0"
    print(f"모델: {LIVE_MODEL} / 목소리: {VOICE_NAME}"
          + (" — 자동 트리거 모드(마이크 입력 없이 한 턴만 듣고 종료)" if auto_speak
             else " — 마이크에 대고 말해보세요. Ctrl+C로 종료."))

    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        with MicStreamer(loop) as mic, Speaker() as speaker:
            stop_event = asyncio.Event()

            if auto_speak:
                await session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=(
                            "(지금은 사용자가 마이크로 말할 수 없는 상황이라 텍스트로 대화를 "
                            "시작합니다. 평소처럼 자연스럽게 첫 인사말을 짧게 건네주세요.)"
                        ))],
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
                async for message in session.receive():
                    sc = message.server_content
                    if sc and sc.interrupted:
                        print("🚨 interrupted=True — 재생 중단")
                        speaker.stop_immediately()

                    if message.data:
                        speaker.play(message.data)

                    if sc and sc.input_transcription and sc.input_transcription.text:
                        print(f"  (내가 한 말: {sc.input_transcription.text})", end="", flush=True)
                    if sc and sc.output_transcription and sc.output_transcription.text:
                        print(sc.output_transcription.text, end="", flush=True)

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
                        print()
                        if auto_speak:
                            # 재생이 끝날 시간을 잠깐 준 뒤 자동 종료(오디오가 남아있는데
                            # 바로 스트림을 닫으면 마지막 부분이 잘려 들릴 수 있어서).
                            await asyncio.sleep(1.5)
                            stop_event.set()
                            break

            await asyncio.gather(send_loop(), recv_loop())


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n종료.")
    finally:
        profiles.forget_user(TEST_USER)
