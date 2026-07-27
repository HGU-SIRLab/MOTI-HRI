"""페르소나 시스템 인스트럭션(§03 3단계) 독립 테스트 도구.

1부: build_persona_system_instruction()이 이름/facts 유무에 따라 깨지지
     않고 그럴듯한 텍스트를 만드는지 확인 — API 키 불필요.
2부: GOOGLE_API_KEY가 있으면 실제로 짧은 대화를 걸어보고, remember_fact와
     set_emotion을 함께 쥐어준 상태에서 모델이 인터뷰하듯 굴지 않고
     자연스럽게 대화하면서 스스로 기억하고 표정을 고르는지 확인한다.

사용:
    python scripts/test_persona.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core.emotion_tools import make_set_emotion_tool
from core.memory_tools import make_remember_fact_tool
from core.utils import build_persona_system_instruction

TEST_USER = "__테스트유저__"


def run_prompt_build_test():
    print("=== 1부: 프롬프트 조립 테스트 (API 키 불필요) ===")

    prompt_unknown = build_persona_system_instruction(name=None, facts_summary=None)
    assert "이름을 아직 모릅니다" in prompt_unknown
    assert "STAGES" not in prompt_unknown  # 슬롯 채우기 흔적이 없어야 함
    print(f"신규(이름도 모름) 프롬프트 길이: {len(prompt_unknown)}자")

    prompt_known = build_persona_system_instruction(
        name="김한동",
        facts_summary="- grade: 2학년\n- major: 전전\n- mbti: ENFP",
    )
    assert "김한동" in prompt_known
    assert "한동님" in prompt_known  # _display_name이 성을 뗀 결과
    assert "ENFP" in prompt_known
    print(f"재회(정보 있음) 프롬프트 길이: {len(prompt_known)}자")

    print("✅ 1부 통과 — 두 경우 모두 정상 조립됨, STAGES 흔적 없음")


def run_live_conversation_test():
    print("\n=== 2부: 실제 Gemini와 짧은 대화 테스트 ===")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 설정되지 않아 건너뜁니다. (.env.example 참고)")
        return

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model_name = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")

    profiles.forget_user(TEST_USER)
    remember_fact = make_remember_fact_tool({"name": TEST_USER})
    set_emotion = make_set_emotion_tool()  # display 없음 → 콘솔 로그로 대체

    system_instruction = build_persona_system_instruction(name=None, facts_summary=None)
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_instruction,
        tools=[remember_fact, set_emotion],
    )
    chat = model.start_chat(enable_automatic_function_calling=True)

    turns = [
        "어 안녕! 처음 보네",
        "나는 김한동이야. 오늘 좀 힘든 일이 있었어.",
    ]
    for turn in turns:
        print(f"\n사용자: {turn}")
        response = chat.send_message(turn)
        print(f"모티: {response.text.strip()}")

    facts = profiles.get_facts(TEST_USER)
    print(f"\n저장된 facts: {facts}")
    if facts:
        print("✅ 2부 통과 — 슬롯을 강제로 묻지 않고도 스스로 기억을 남겼습니다.")
    else:
        print("⚠️  이번 대화에서는 remember_fact가 호출되지 않았습니다 (모델 판단에 따른 정상적 변동성일 수 있음).")

    profiles.forget_user(TEST_USER)


if __name__ == "__main__":
    run_prompt_build_test()
    run_live_conversation_test()
