"""core/quiz_state.py의 QuizSession을 3개 모드 전체 흐름으로 구동해 검증한다.
API 키/로봇 불필요 — 모드별 정보 비대칭(척척박사만 정답을 앎)과 하찮미 모드의
페어드 리빌(사용자 추측 + 로봇 추측을 함께 채점) 로직이 이 테스트의 핵심.

사용: python scripts/test_quiz_state.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.quiz_bank import QuizQuestion
from core.quiz_state import MODE3_REFUSAL_LINE, QuizSession


def make_questions(n):
    return [QuizQuestion(id=f"q{i}", image_path=f"q{i}.jpg", answer=f"정답{i}", alternates=[f"답{i}"])
            for i in range(n)]


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True

    # 1. 척척박사 — 정답을 처음부터 알고, 오답이면 바로 공개
    s = QuizSession(make_questions(3), num_questions=3)
    s.start()
    ok &= check("invalid mode rejected", s.choose_mode("invalid") is None)
    txt = s.choose_mode("all_knowing")
    ok &= check("all_knowing reveals answer at mode-select", "정답0" in txt)
    txt = s.resolve_user_guess("땡땡땡")
    ok &= check("wrong guess reveals answer + advances", "정답0" in txt and s.index == 1)
    txt = s.resolve_user_guess("정답1")
    ok &= check("correct guess praised", "맞혔습니다" in txt and s.index == 2)
    txt = s.resolve_user_guess("오답")
    ok &= check("last question ends session", "모든 문제가 끝났습니다" in txt and not s.active)
    ok &= check("all_knowing logged 3 results", len(s.results) == 3)

    # 2. 하찮미 — 정답을 모르고, 사용자/로봇 추측을 페어로 채점
    s2 = QuizSession(make_questions(2), num_questions=2)
    s2.start()
    txt = s2.choose_mode("imperfect")
    ok &= check("imperfect withholds answer at mode-select", "정답0" not in txt)
    txt = s2.resolve_user_guess("내추측")
    ok &= check("user guess staged, not judged yet", "submit_guess" in txt and s2.pending_user_guess == "내추측")
    txt = s2.resolve_robot_guess("정답0")
    ok &= check("robot guess correct -> proud", "뿌듯" in txt and s2.pending_user_guess is None and s2.index == 1)
    r0 = s2.results[0]
    ok &= check("paired result recorded (robot correct)", r0.user_guess_text == "내추측" and r0.robot_correct is True)

    s2.resolve_user_guess("사용자오답")
    txt = s2.resolve_robot_guess("로봇오답")
    ok &= check("robot guess wrong -> embarrassed", "부끄" in txt and not s2.active)
    ok &= check("second result recorded (robot wrong)", s2.results[1].robot_correct is False)

    # 3. 방어 가드 — imperfect 아닐 때 resolve_robot_guess는 무시
    s3 = QuizSession(make_questions(1), num_questions=1)
    s3.start()
    s3.choose_mode("all_knowing")
    txt = s3.resolve_robot_guess("아무말")
    ok &= check("resolve_robot_guess ignored outside imperfect mode", "무시" in txt)

    # 4. 짜증유발 — quiz_state.py 자체는 필러만 반환, 거절 대사는 core/quiz_tools.py가 지연 주입으로 처리
    s4 = QuizSession(make_questions(1), num_questions=1)
    s4.start()
    s4.choose_mode("annoying")
    txt = s4.request_hint()
    ok &= check("annoying hint does NOT contain the refusal line itself", MODE3_REFUSAL_LINE not in txt)

    # 5. 조기 종료
    s5 = QuizSession(make_questions(5), num_questions=5)
    s5.start()
    s5.choose_mode("all_knowing")
    s5.end_early()
    ok &= check("end_early deactivates session", not s5.active)

    # 7. select_quiz_mode를 건너뛰고(모델이 실제로 호출하지 않고 말로만 진행한 척한
    # 경우, 2026-07-29 실물 테스트에서 실제로 발생) submit_guess를 부르면, "퀴즈가
    # 진행 중이 아닙니다"라는 막연한 문구가 아니라 "먼저 select_quiz_mode를 호출하라"는
    # 구체적인 복구 지시가 나와야 한다.
    s6 = QuizSession(make_questions(1), num_questions=1)
    s6.start()  # choose_mode를 의도적으로 호출하지 않음
    txt = s6.resolve_user_guess("아무말")
    ok &= check("skipping select_quiz_mode gives an actionable recovery message",
                "select_quiz_mode" in txt)

    # 6. export_log 필드
    log = s2.export_log()
    ok &= check("export_log has expected fields", len(log) == 2 and "question_id" in log[0] and "timestamp" in log[0])

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
