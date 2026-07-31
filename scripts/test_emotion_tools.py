"""core/emotion_tools.py의 set_emotion 툴을 검증한다 — 특히 2026-07-31에 추가된
짜증유발(annoying) 퀴즈 모드 전용 감정 제한(neutral/thinking으로만 클램프)이 그
모드에서만 걸리고 다른 모든 상황에서는 평소대로 동작하는지 확인한다. API 키/로봇 불필요.

사용: python scripts/test_emotion_tools.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.emotion_tools import make_set_emotion_tool
from core.quiz_bank import QuizQuestion
from core.quiz_state import QuizSession


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


class FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


def make_questions(n):
    return [QuizQuestion(id=f"q{i}", image_path=f"q{i}.jpg", answer=f"정답{i}")
            for i in range(n)]


def main():
    ok = True

    # 1. quiz_session이 아예 없으면(일반 대화, 퀴즈 미사용) 기존과 동일하게 제한 없음.
    q1 = FakeQueue()
    set_emotion1 = make_set_emotion_tool(q1)
    set_emotion1("happy")
    ok &= check("no quiz_session -> emotion passes through unclamped", q1.items == ["HAPPY"])

    # 2. quiz_session은 있지만 아직 짜증유발 모드가 아니면(다른 모드거나 퀴즈 시작 전)
    # 역시 제한 없음.
    q2 = FakeQueue()
    session2 = QuizSession(make_questions(1), num_questions=1)
    set_emotion2 = make_set_emotion_tool(q2, quiz_session=session2)
    set_emotion2("excited")
    ok &= check("quiz_session before any mode chosen -> unclamped", q2.items == ["EXCITED"])
    session2.start()
    session2.choose_mode("imperfect")
    set_emotion2("sad")
    ok &= check("quiz_session in a non-annoying mode -> unclamped", q2.items[-1] == "SAD")

    # 3. 짜증유발 모드가 활성화된 동안엔 neutral/thinking 이외는 neutral로 클램프된다.
    q3 = FakeQueue()
    session3 = QuizSession(make_questions(2), num_questions=2)
    set_emotion3 = make_set_emotion_tool(q3, quiz_session=session3)
    session3.start()
    session3.choose_mode("annoying")
    set_emotion3("happy")
    ok &= check("annoying mode clamps happy -> neutral", q3.items[-1] == "NEUTRAL")
    set_emotion3("thinking")
    ok &= check("annoying mode allows thinking through unclamped", q3.items[-1] == "THINKING")
    set_emotion3("neutral")
    ok &= check("annoying mode allows neutral through unclamped", q3.items[-1] == "NEUTRAL")
    set_emotion3("excited")
    ok &= check("annoying mode clamps excited -> neutral too", q3.items[-1] == "NEUTRAL")

    # 4. 짜증유발 모드가 자연 종료되면(session.active == False) 제한도 같이 풀려야 한다
    # (퀴즈 끝난 뒤 평소 대화로 돌아왔는데 표정이 계속 눌려 있으면 안 됨).
    session3.resolve_user_guess("정답0")
    session3.confirm_annoying_correct()
    session3.resolve_user_guess("정답1")
    session3.confirm_annoying_correct()
    ok &= check("quiz naturally ended after last question", not session3.active)
    set_emotion3("happy")
    ok &= check("emotion unclamped again once the annoying round has ended", q3.items[-1] == "HAPPY")

    # 5. 알 수 없는 감정은 여전히 그냥 무시된다(클램프 로직과 무관, 기존 동작 보존).
    q5 = FakeQueue()
    set_emotion5 = make_set_emotion_tool(q5)
    result = set_emotion5("furious")
    ok &= check("unknown emotion is still ignored, not clamped", "ignored" in result and q5.items == [])

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
