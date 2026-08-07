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
    # 포기 마커 부분 문자열 오탐 방지(2026-08-07) — 아래는 전부 포기가 아니라 실제 답
    # 시도/난이도 언급/문제 신고이므로 dont_know로 오인되면 안 된다(특히 짜증유발
    # 모드에서 오인되면 즉시 정답이 공개되어 "매번 거절" 조작이 깨진다).
    ("그만큼 어렵네요", False, False),          # "그만" 오탐
    ("패스츄리 아니에요?", False, False),        # "패스" 오탐(실제 답 시도)
    ("화면이 안 넘어가는데요?", False, False),    # "넘어가" 오탐(문제 신고)
    # 중화 목록이 진짜 포기/스킵 표현까지 잡아먹으면 안 된다.
    ("이제 그만할래요", False, True),
    ("패스할게요", False, True),
    ("다음 문제로 넘어가줘", False, True),
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
