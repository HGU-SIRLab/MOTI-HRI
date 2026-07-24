"""Live API PoC — 로드맵 4단계.

텍스트 입력으로 연결·시스템 인스트럭션·함수호출·지연시간을 먼저 검증한다
(오디오 장비 없이도 자동 실행 가능). 실제 끼어들기(barge-in)는 마이크로
겹쳐 말해야 재현되므로 scripts/test_live_audio.py(사용자가 직접 실행)에서
별도로 확인한다.

사용:
    python scripts/test_live_poc.py
"""
import asyncio
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_remember_fact_tool
from core.utils import build_persona_system_instruction

TEST_USER = "__테스트유저__"
LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")


async def run_text_poc():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
    except ImportError:
        pass

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 Live API PoC를 건너뜁니다.")
        return

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    profiles.forget_user(TEST_USER)
    remember_fact = make_remember_fact_tool(TEST_USER)
    set_emotion = make_set_emotion_tool()
    tool_fns = {"remember_fact": remember_fact, "set_emotion": set_emotion}

    config = types.LiveConnectConfig(
        # 서버가 TEXT 단독 출력을 거부함 — 이 모델은 오디오 출력이 기본이라
        # AUDIO로 응답받고, 텍스트 확인용으로 output_audio_transcription만 켠다.
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=build_persona_system_instruction(name=None, facts_summary=None),
        tools=[remember_fact, set_emotion],
    )

    turns = [
        "어 안녕! 처음 보네",
        "나는 김한동이야. 오늘 좀 힘든 일이 있었어.",
    ]

    print(f"모델: {LIVE_MODEL}")
    connect_t0 = time.monotonic()
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        print(f"✅ 연결 성공 ({time.monotonic() - connect_t0:.2f}초)")

        for turn in turns:
            print(f"\n사용자: {turn}")
            t0 = time.monotonic()
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=turn)]),
                turn_complete=True,
            )

            first_chunk_latency = None
            reply_text = ""
            async for message in session.receive():
                if first_chunk_latency is None:
                    first_chunk_latency = time.monotonic() - t0
                    print(f"  (첫 응답까지 {first_chunk_latency:.2f}초)")

                sc = message.server_content
                if sc and sc.output_transcription and sc.output_transcription.text:
                    reply_text += sc.output_transcription.text

                if message.tool_call:
                    responses = []
                    for fc in message.tool_call.function_calls:
                        fn = tool_fns.get(fc.name)
                        result = fn(**(fc.args or {})) if fn else f"unknown tool {fc.name}"
                        print(f"  🔧 tool_call {fc.name}({fc.args}) -> {result}")
                        responses.append(
                            types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result})
                        )
                    await session.send_tool_response(function_responses=responses)

                if message.server_content and message.server_content.turn_complete:
                    break

            print(f"모티: {reply_text.strip()}")

    facts = profiles.get_facts(TEST_USER)
    print(f"\n저장된 facts: {facts}")
    if facts:
        print("✅ Live API 세션에서도 function calling이 정상 동작함 (수동 tool_call 처리).")
    profiles.forget_user(TEST_USER)


if __name__ == "__main__":
    asyncio.run(run_text_poc())
