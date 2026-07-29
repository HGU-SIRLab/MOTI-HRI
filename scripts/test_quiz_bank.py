"""core/quiz_bank.py의 judge_guess()를 검증한다. API 키/로봇 불필요.

사용: python scripts/test_quiz_bank.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.quiz_bank import QuizQuestion, judge_guess

CASES = [
    # (발화, 기대 정답 여부, 기대 "모름" 여부)
    ("먼지떨이", True, False),
    ("먼지떨이예요", True, False),
    ("먼지떨이인가요?", True, False),
    ("먼지털이", True, False),  # alternate
    ("총채", True, False),  # alternate
    ("먼지떨이인 것 같아요", True, False),
    ("모르겠어요", False, True),
    ("잘 모르겠는데", False, True),
    ("빗자루", False, False),  # 인접 오답 — 정답으로 착각하면 안 됨
    ("강아지 털", False, False),
]


def main():
    question = QuizQuestion(id="q_test", image_path="x.jpg", answer="먼지떨이", alternates=["먼지털이", "총채"])
    failures = 0
    for guess, expect_correct, expect_dont_know in CASES:
        correct, dont_know = judge_guess(guess, question)
        ok = (correct, dont_know) == (expect_correct, expect_dont_know)
        if not ok:
            failures += 1
        status = "OK  " if ok else "FAIL"
        print(f"{status}: {guess!r:25s} -> correct={correct}, dont_know={dont_know} "
              f"(기대: {expect_correct}, {expect_dont_know})")

    if failures:
        print(f"\n❌ {failures}개 실패")
        sys.exit(1)
    print(f"\n✅ 전부 통과 ({len(CASES)}개 케이스)")


if __name__ == "__main__":
    main()
