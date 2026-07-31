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

    # 2. 하찮미 — 2026-07-30부터 실제 답 시도는 이벤트 없이 그냥 채점하고, "모르겠다"/
    # "정답 알려줘"/힌트 요청 같은 포기 신호일 때만 로봇이 자기 추측을 하는 페어드 리빌로 감.
    s2 = QuizSession(make_questions(3), num_questions=3)
    s2.start()
    txt = s2.choose_mode("imperfect")
    ok &= check("imperfect withholds answer at mode-select", "정답0" not in txt)

    # 2a. 진짜 오답 시도 — 2026-07-30 피드백: 정답을 바로 공개하고 넘어가면 척척박사와
    # 구분이 안 되고, 화면도 곧장 정답 공개로 넘어가버리는 문제가 있었다. 이제는 전진/기록
    # 없이 같은 문제에 머물면서 재도전을 유도해야 한다(정답 단어 자체를 노출하면 안 됨).
    txt = s2.resolve_user_guess("땡땡땡")
    ok &= check(
        "imperfect wrong guess stays on question (no advance, no reveal, no answer leak)",
        "정답0" not in txt and "submit_guess" not in txt and s2.index == 0 and len(s2.results) == 0,
    )
    txt = s2.resolve_user_guess("또땡땡땡")
    ok &= check("imperfect wrong guess can be retried repeatedly", s2.index == 0 and len(s2.results) == 0)

    # 진짜 답 시도(정답) — 이벤트 없이 바로 채점하고 전진, pending_user_guess도 안 건드림.
    txt = s2.resolve_user_guess("정답0")
    ok &= check(
        "imperfect real guess judged immediately (no kickoff event)",
        "맞혔습니다" in txt and "submit_guess" not in txt and s2.pending_user_guess is None and s2.index == 1,
    )

    # 2b. "모르겠어요" — 포기 신호라 로봇이 자기 추측을 하는 이벤트로 감.
    txt = s2.resolve_user_guess("모르겠어요")
    ok &= check("give-up phrase triggers kickoff event", "submit_guess" in txt and s2.pending_user_guess == "모르겠어요")
    txt = s2.resolve_robot_guess("정답1")
    ok &= check("robot guess correct -> proud", "뿌듯" in txt and s2.pending_user_guess is None and s2.index == 2)
    r1 = s2.results[1]
    ok &= check("paired result recorded (robot correct)", r1.user_guess_text == "모르겠어요" and r1.robot_correct is True)

    # 2c. "정답 알려줘" — 힌트류 포기 신호도 같은 이벤트로 감.
    txt = s2.resolve_user_guess("정답 알려줘")
    ok &= check("hand-it-to-me phrase also triggers kickoff event", "submit_guess" in txt and s2.pending_user_guess == "정답 알려줘")
    txt = s2.resolve_robot_guess("로봇오답")
    ok &= check("robot guess wrong -> cutely disappointed", "아쉽다" in txt and not s2.active)
    ok &= check("third result recorded (robot wrong)", s2.results[2].robot_correct is False)

    # 2d. request_hint()도 같은 "저도 맞춰볼게요" 이벤트로 통일됨.
    s2b = QuizSession(make_questions(1), num_questions=1)
    s2b.start()
    s2b.choose_mode("imperfect")
    txt = s2b.request_hint()
    ok &= check(
        "imperfect hint request triggers kickoff event",
        "submit_guess" in txt and s2b.pending_user_guess is not None,
    )
    txt = s2b.resolve_robot_guess("정답0")
    ok &= check("hint-triggered robot guess resolves and advances", "뿌듯" in txt and not s2b.active)

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
    ok &= check("export_log has expected fields", len(log) == 3 and "question_id" in log[0] and "timestamp" in log[0])

    # 8. 한 세션 안에서 여러 라운드(모드) 연속 진행 — 2026-07-31: 참가자가 1/2/3번 모드를
    # 로봇 재시작 없이 한 세션 안에서 이어서 진행하는 실험 운영 방식이 확정되면서, 라운드마다
    # 다른 문제 세트를 보여줘야 앞 라운드에서 공개된 정답이 다음 라운드를 오염시키지 않는다.
    s7 = QuizSession(make_questions(6), num_questions=2)  # 은행 6개, 라운드당 2개 -> 3라운드 정확히 소진
    s7.start()
    s7.choose_mode("all_knowing")
    round1_ids = [q.id for q in s7.questions]
    s7.resolve_user_guess("정답0")
    s7.resolve_user_guess("정답1")  # 라운드1 종료(2문제 다 풀림)
    ok &= check("round 1 ends session", not s7.active)

    s7.start()  # 라운드 2 시작 — 다음 모드
    s7.choose_mode("imperfect")
    round2_ids = [q.id for q in s7.questions]
    ok &= check("round 2 uses a disjoint question set from round 1",
                set(round1_ids).isdisjoint(round2_ids))

    s7.resolve_user_guess("정답2")
    s7.resolve_user_guess("정답3")  # 라운드2 종료

    s7.start()  # 라운드 3 시작 — 마지막 모드
    s7.choose_mode("annoying")
    round3_ids = [q.id for q in s7.questions]
    ok &= check("round 3 uses a disjoint question set from rounds 1 and 2",
                set(round1_ids).isdisjoint(round3_ids) and set(round2_ids).isdisjoint(round3_ids))
    # 라운드1(2문항) + 라운드2(2문항) 결과가 세션 하나에 계속 쌓여있어야 한다(export_log가
    # 세션 종료 시 3라운드치를 한 번에 모드별로 나눌 수 있는 전제).
    ok &= check("results from rounds 1 and 2 both accumulate in one session", len(s7.results) == 4)

    # 은행이 부족하면(4라운드째 요청 등) 경고를 남기고 처음부터 재사용 — 크래시하지 않아야 함.
    s7.resolve_user_guess("정답4")
    s7.resolve_user_guess("정답5")
    s7.start()
    s7.choose_mode("all_knowing")
    round4_ids = [q.id for q in s7.questions]
    ok &= check("exhausted bank falls back to reusing questions instead of crashing",
                round4_ids == round1_ids)

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
