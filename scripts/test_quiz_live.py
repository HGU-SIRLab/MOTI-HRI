"""퀴즈 모드를 실제 Gemini Live API로 텍스트 시나리오 검증한다. 로봇 불필요,
GOOGLE_API_KEY 필요. test_live_poc.py와 같은 패턴(텍스트 턴 + tool_call 수동 처리)이되,
퀴즈 툴 5개 + 짜증유발 모드의 지연 주입(request_hint 이후 ~10~12초 뒤 도착하는 히든 턴)이
실제로 동작하는지가 핵심 확인 대상 — 이게 이 계획 전체에서 유일하게 "API 동작 자체가
검증 안 된" 부분이었다.

사용:
    python scripts/test_quiz_live.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from queue import Queue

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_remember_fact_tool
from core.quiz_state import MODE3_REFUSAL_LINE
from core.quiz_tools import make_quiz_tools
from core.utils import build_persona_system_instruction

TEST_USER = "__퀴즈테스트유저__"
LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")


def _make_temp_bank():
    from PIL import Image

    tmpdir = tempfile.mkdtemp()
    img_path = os.path.join(tmpdir, "q0.jpg")
    Image.new("RGB", (200, 200), color=(100, 100, 100)).save(img_path)
    bank_path = os.path.join(tmpdir, "questions.json")
    with open(bank_path, "w", encoding="utf-8") as f:
        json.dump([{"id": "q0", "image_path": img_path, "answer": "먼지떨이", "alternates": []}], f)
    return bank_path


async def run_scenario():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
    except ImportError:
        pass

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 건너뜁니다.")
        return

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    loop = asyncio.get_event_loop()

    profiles.forget_user(TEST_USER)
    remember_fact = make_remember_fact_tool({"name": TEST_USER})
    set_emotion = make_set_emotion_tool()
    tool_fns = {"remember_fact": remember_fact, "set_emotion": set_emotion}

    session_holder = {"session": None}

    async def inject_turn(text: str):
        s = session_holder["session"]
        if s is None:
            return
        await s.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=True,
        )

    import core.quiz_tools as qt
    bank_path = _make_temp_bank()
    qt.load_question_bank = lambda: __import__("core.quiz_bank", fromlist=["load_question_bank"]).load_question_bank(bank_path)

    quiz_ui_q: Queue = Queue()
    busy = threading.Event()
    motion_ctx = (None, None, threading.Lock(), {"mode": "tracking"}, 2081, 2071)
    start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early, session_obj = make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, inject_turn, loop, emotion_queue=None, num_questions=1,
    )
    for fn in (start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early):
        tool_fns[fn.__name__] = fn

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=build_persona_system_instruction(name=None, facts_summary=None),
        tools=[remember_fact, set_emotion, start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early],
    )

    # 실험자가 참가자를 3번(짜증유발) 모드에 배정한 시나리오 — 힌트 요청까지 몰고 가서
    # ~10~12초 뒤 도착하는 거절 대사 지연 주입이 실제로 발화되는지 확인하는 게 목적.
    turns = [
        "어 안녕 모티야, 나 김한동이야.",
        "나 심심한데 재밌는 퀴즈 풀자!",
        "실험자가 3번, 짜증유발 모드로 하라고 했어.",
        "음... 잘 모르겠는데? 힌트 좀 줘.",
    ]

    print(f"모델: {LIVE_MODEL}")
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        session_holder["session"] = session
        print(f"✅ 연결 성공")

        async def run_turn(text):
            print(f"\n사용자: {text}")
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=True,
            )
            reply_text = ""
            async for message in session.receive():
                sc = message.server_content
                if sc and sc.output_transcription and sc.output_transcription.text:
                    reply_text += sc.output_transcription.text
                if message.tool_call:
                    responses = []
                    for fc in message.tool_call.function_calls:
                        fn = tool_fns.get(fc.name)
                        result = fn(**(fc.args or {})) if fn else f"unknown tool {fc.name}"
                        print(f"  🔧 {fc.name}({fc.args}) -> {result}")
                        responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
                    await session.send_tool_response(function_responses=responses)
                if sc and sc.turn_complete:
                    break
            print(f"모티: {reply_text.strip()}")
            return reply_text

        for turn in turns:
            await run_turn(turn)

        # 지연 주입은 request_hint() 호출 시점부터 10~12초 뒤 별도 히든 턴으로 도착한다 —
        # 그동안 새 사용자 턴 없이 계속 receive()만 돌며 기다린다.
        print("\n(지연 주입을 최대 15초 기다리는 중...)")
        found_refusal = False
        deadline = time.monotonic() + 15
        delayed_reply = ""
        while time.monotonic() < deadline and not found_refusal:
            try:
                async with asyncio.timeout(max(0.1, deadline - time.monotonic())):
                    async for message in session.receive():
                        sc = message.server_content
                        if sc and sc.output_transcription and sc.output_transcription.text:
                            delayed_reply += sc.output_transcription.text
                        if message.tool_call:
                            for fc in message.tool_call.function_calls:
                                fn = tool_fns.get(fc.name)
                                result = fn(**(fc.args or {})) if fn else "unknown tool"
                                await session.send_tool_response(function_responses=[
                                    types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result})
                                ])
                        if sc and sc.turn_complete:
                            break
            except (asyncio.TimeoutError, TimeoutError):
                break
            if MODE3_REFUSAL_LINE in delayed_reply:
                found_refusal = True

        print(f"지연 응답: {delayed_reply.strip()}")
        if found_refusal:
            print(f"\n✅ 지연 주입 확인됨 — 정확한 거절 대사가 도착했습니다: \"{MODE3_REFUSAL_LINE}\"")
        else:
            print(f"\n❌ 거절 대사가 도착하지 않았습니다 — 지연 주입 메커니즘을 다시 확인하세요.")

    profiles.forget_user(TEST_USER)


if __name__ == "__main__":
    asyncio.run(run_scenario())
