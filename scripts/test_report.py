"""세션 종료 보고서(core/report_manager.py) 독립 테스트 도구.

GOOGLE_API_KEY가 있어야 실제로 결과지를 생성해볼 수 있다(batch generate_content 호출).
로봇 불필요.

사용:
    python scripts/test_report.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import profile_manager as profiles
from core import report_manager

TEST_USER = "__테스트유저__"


def main():
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

    profiles.forget_user(TEST_USER)
    profiles.remember_fact(TEST_USER, "grade", "1학년", "certain")
    profiles.remember_fact(TEST_USER, "major", "전산전자공학부", "certain")
    profiles.remember_fact(TEST_USER, "mbti", "INFP", "inferred")
    facts_summary = profiles.load_profile_for_chat(TEST_USER)
    print("facts_summary:\n", facts_summary)

    conversation_log = (
        "User: 안녕 모티야, 오늘 팀플 때문에 너무 지쳐 | Moti: 팀플 때문에 많이 지치셨군요, 어떤 부분이 제일 힘드셨어요?\n"
        "User: 조원들이 다 잠수타서 나 혼자 다 했어 | Moti: 혼자서 다 짊어지셨다니 정말 고생 많으셨어요. 오늘 밤엔 좀 쉬어야겠어요."
    )

    print("⏳ 보고서 생성 중 (Gemini 호출)...")
    report_manager.generate_and_save_reports(TEST_USER, conversation_log, facts_summary)

    result_dir = os.path.join(_REPO_ROOT, "user_result")
    files = [f for f in os.listdir(result_dir) if TEST_USER in f] if os.path.exists(result_dir) else []
    print(f"\n생성된 파일: {files}")
    assert len(files) == 2, f"대화록+결과지 2개가 생성되어야 하는데 {len(files)}개 생성됨"
    print("✅ 통과 — 대화록과 결과지가 모두 생성되었습니다. 내용은 user_result/ 폴더에서 직접 확인하세요.")

    profiles.forget_user(TEST_USER)


if __name__ == "__main__":
    main()
