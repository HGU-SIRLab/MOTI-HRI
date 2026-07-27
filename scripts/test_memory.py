"""메모리 계층(§04) 독립 테스트 도구.

1부: 저장소(core/profile_manager.py)만 검증 — API 키 불필요.
2부: GOOGLE_API_KEY가 있으면 실제 Gemini에게 remember_fact 툴을 쥐어주고,
     대화 중 스스로 언제 호출할지 판단하게 시켜본다 (batch function calling,
     Live API 아님 — 로드맵 §10 2단계가 요구하는 검증 방식).

사용:
    python scripts/test_memory.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core.memory_tools import make_remember_fact_tool

TEST_USER = "__테스트유저__"


def run_storage_test():
    print("=== 1부: 저장소 단독 테스트 (API 키 불필요) ===")
    profiles.forget_user(TEST_USER)  # 이전 실행 잔여물 제거

    profiles.remember_fact(TEST_USER, "name", TEST_USER, "certain")
    profiles.remember_fact(TEST_USER, "grade", "1학년", "certain")
    profiles.remember_fact(TEST_USER, "mbti", "ENFP", "inferred")
    print("최초 저장 후:")
    print(profiles.load_profile_for_chat(TEST_USER))

    # 같은 field로 재호출 → 정정(덮어쓰기)이지 추가가 아니어야 한다
    profiles.remember_fact(TEST_USER, "grade", "2학년", "certain")
    facts = profiles.get_facts(TEST_USER)
    grade_entries = [f for f in facts if f["field"] == "grade"]
    assert len(grade_entries) == 1, f"정정이 아니라 중복 추가됨: {grade_entries}"
    assert grade_entries[0]["value"] == "2학년"
    print("\n정정 후 (grade 항목이 1개여야 함):")
    print(profiles.load_profile_for_chat(TEST_USER))

    assert profiles.is_known(TEST_USER)
    assert not profiles.is_known("__존재하지_않는_사람__")

    profiles.forget_user(TEST_USER)
    assert not profiles.is_known(TEST_USER)
    print("\n✅ 1부 통과 (테스트 데이터 정리 완료)")


def run_live_function_calling_test():
    print("\n=== 2부: 실제 Gemini function calling 테스트 ===")
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

    model = genai.GenerativeModel(model_name, tools=[remember_fact])
    chat = model.start_chat(enable_automatic_function_calling=True)

    user_message = "안녕! 나는 새내기이고 전공은 컴퓨터공학이야. MBTI는 아직 몰라."
    print(f"사용자 발화: {user_message}")
    response = chat.send_message(user_message)
    print(f"모델 응답: {response.text}")

    facts = profiles.get_facts(TEST_USER)
    print(f"\n저장된 facts: {facts}")
    if facts:
        print("✅ 2부 통과 — 모델이 스스로 remember_fact를 호출했습니다.")
    else:
        print("⚠️  모델이 이번 발화에서는 remember_fact를 호출하지 않았습니다. "
              "(정상적인 변동성일 수 있음 — 프롬프트 없이 툴 설명만으로 판단한 결과)")

    profiles.forget_user(TEST_USER)


if __name__ == "__main__":
    run_storage_test()
    run_live_function_calling_test()
