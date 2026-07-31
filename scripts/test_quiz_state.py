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

    # 2. 하찮미 — 2026-07-31부터 포기/위임 신호든 실제 답 시도든 전부 "로봇도 같이
    # 추측해서 나란히 비교" 이벤트로 통일됐다. 즉시 정오를 알려주면(19~20단계 방식) 로봇이
    # 이미 정답을 아는 것처럼 보여 조작 점검 문항("로봇이 정답을 모르는 상태로 함께
    # 풀었다")과 모순된다는 실물 테스트 피드백으로 재설계.
    s2 = QuizSession(make_questions(3), num_questions=3)
    s2.start()
    txt = s2.choose_mode("imperfect")
    ok &= check("imperfect withholds answer at mode-select", "정답0" not in txt)

    # 2a. 진짜 답 시도도(맞았든 틀렸든) 즉시 정오를 알려주지 않고 로봇도 같이 추측하는
    # 이벤트로 감 — 여기서는 사용자가 실제로 맞혔고 로봇도 맞히면 "둘 다 맞음" 팀워크 반응.
    txt = s2.resolve_user_guess("정답0")
    ok &= check(
        "real correct guess triggers the kickoff event instead of an immediate verdict",
        "맞혔습니다" not in txt and "submit_guess" in txt and s2.pending_user_guess == "정답0"
        and s2.index == 0 and len(s2.results) == 0,
    )
    txt = s2.resolve_robot_guess("정답0")
    ok &= check(
        "both user and robot correct -> teamwork reaction",
        "팀워크" in txt and s2.index == 1 and len(s2.results) == 1
        and s2.results[0].user_correct is True and s2.results[0].robot_correct is True,
    )

    # 2a'. 사용자는 맞혔지만 로봇은 틀림 -> 로봇이 살짝 시무룩하되 사용자를 인정.
    txt = s2.resolve_user_guess("정답1")
    ok &= check("real correct guess also triggers the kickoff event (not judged immediately)",
                "submit_guess" in txt and s2.pending_user_guess == "정답1")
    txt = s2.resolve_robot_guess("로봇오답")
    ok &= check(
        "user correct + robot wrong -> robot is a little dejected but congratulates",
        "부럽다" in txt and s2.results[1].user_correct is True and s2.results[1].robot_correct is False,
    )

    # 2a''. 둘 다 틀림 -> 함께 아쉬워함.
    s2.resolve_user_guess("땡땡")
    txt = s2.resolve_robot_guess("로봇도땡")
    ok &= check(
        "both wrong -> mutual disappointment",
        "둘 다 틀렸네요" in txt and s2.results[2].user_correct is False and s2.results[2].robot_correct is False,
    )
    ok &= check("round ends after all 3 questions", not s2.active)

    # 2b. "모르겠어요" — 포기 신호는 실제 답이 없으므로 "둘 다"라는 비교 틀이 안 맞아,
    # 기존처럼 로봇 자신의 결과만으로 반응한다.
    s2b = QuizSession(make_questions(2), num_questions=2)
    s2b.start()
    s2b.choose_mode("imperfect")
    txt = s2b.resolve_user_guess("모르겠어요")
    ok &= check("give-up phrase triggers kickoff event", "submit_guess" in txt and s2b.pending_user_guess == "모르겠어요")
    txt = s2b.resolve_robot_guess("정답0")
    ok &= check("robot guess correct -> proud (give-up path uses the 2-way reaction)",
                "뿌듯" in txt and s2b.pending_user_guess is None and s2b.index == 1)
    r0 = s2b.results[0]
    ok &= check("paired result recorded (robot correct)", r0.user_guess_text == "모르겠어요" and r0.robot_correct is True)

    # 2c. "정답 알려줘" — 힌트류 포기 신호도 같은 이벤트로 감.
    txt = s2b.resolve_user_guess("정답 알려줘")
    ok &= check("hand-it-to-me phrase also triggers kickoff event", "submit_guess" in txt and s2b.pending_user_guess == "정답 알려줘")
    txt = s2b.resolve_robot_guess("로봇오답")
    ok &= check("robot guess wrong -> cutely disappointed (give-up path)", "아쉽다" in txt and not s2b.active)
    ok &= check("second result recorded (robot wrong)", s2b.results[1].robot_correct is False)

    # 2d. request_hint()도 같은 "저도 맞춰볼게요" 이벤트로 통일됨.
    s2c = QuizSession(make_questions(1), num_questions=1)
    s2c.start()
    s2c.choose_mode("imperfect")
    txt = s2c.request_hint()
    ok &= check(
        "imperfect hint request triggers kickoff event",
        "submit_guess" in txt and s2c.pending_user_guess is not None,
    )
    txt = s2c.resolve_robot_guess("정답0")
    ok &= check("hint-triggered robot guess resolves and advances", "뿌듯" in txt and not s2c.active)

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

    # 4b. 2026-07-31 1차 수정: 짜증유발 모드는 오답이든 정답이든 이 턴에서 곧장 알려주면
    # 안 된다(정답을 몰라야 하는데 즉시 "땡! 정답은 ~"으로 새버렸던 실제 버그) — 힌트
    # 거절과 같은 필러만 반환하고, pending_user_guess/annoying_pending_correct로 core/
    # quiz_tools.py가 지연 스케줄링을 결정한다. 2차 수정: 오답은 거절 대사만 하고 끝나면
    # 안 되고(정답을 못 맞히면 영원히 다음 문제로 못 넘어가는 사고가 실제 테스트에서
    # 났음), 거절 직후 곧장 reveal_annoying_wrong()으로 정답을 공개하고 전진해야 한다.
    s4b = QuizSession(make_questions(2), num_questions=2)
    s4b.start()
    s4b.choose_mode("annoying")
    txt = s4b.resolve_user_guess("땡땡땡")
    ok &= check(
        "annoying wrong guess withholds answer, no advance yet, filler only",
        "정답0" not in txt and s4b.index == 0 and len(s4b.results) == 0
        and s4b.pending_user_guess == "땡땡땡" and s4b.annoying_pending_correct is False,
    )
    # core/quiz_tools.py가 거절 대사(MODE3_REFUSAL_LINE)를 말한 직후 이걸 호출해야
    # 비로소 기록/전진하고 정답을 공개한다 — 정답을 맞히지 못해도 다음 문제로 넘어간다.
    txt = s4b.reveal_annoying_wrong()
    ok &= check(
        "reveal_annoying_wrong records the wrong guess, advances, and reveals the answer",
        "정답0" in txt and s4b.index == 1 and len(s4b.results) == 1
        and s4b.results[0].user_correct is False and s4b.results[0].user_guess_text == "땡땡땡"
        and s4b.pending_user_guess is None,
    )

    # 두 번째(마지막) 문제 — 이번엔 정답을 맞히면 즉시 확정하지 않고 pending에만 저장.
    txt = s4b.resolve_user_guess("정답1")
    ok &= check(
        "annoying correct guess ALSO withholds confirmation for now (stashed as pending)",
        "정답1" not in txt and s4b.index == 1 and len(s4b.results) == 1
        and s4b.pending_user_guess == "정답1" and s4b.annoying_pending_correct is True,
    )
    # 정답을 맞힌 뒤 core/quiz_tools.py가 뜸들이기 지연 끝에 이걸 호출해야 실제로 기록/전진한다.
    txt = s4b.confirm_annoying_correct()
    ok &= check(
        "confirm_annoying_correct records the correct guess and advances",
        "정답입니다" in txt and s4b.index == 2 and len(s4b.results) == 2
        and s4b.pending_user_guess is None and s4b.results[1].user_correct is True
        and not s4b.active,
    )
    # 방어 가드: annoying_pending_correct가 없는데(예: 이미 확정된 뒤) 잘못 호출되면 무시.
    txt2 = s4b.confirm_annoying_correct()
    ok &= check("confirm_annoying_correct is a no-op without a pending correct guess", "무시" in txt2)

    # 방어 가드: 짜증유발 모드가 아니면 reveal_annoying_wrong도 무시해야 한다.
    s4c = QuizSession(make_questions(1), num_questions=1)
    s4c.start()
    s4c.choose_mode("all_knowing")
    txt = s4c.reveal_annoying_wrong()
    ok &= check("reveal_annoying_wrong ignored outside annoying mode", "무시" in txt)

    # 회귀 방지: 정답을 말해 annoying_pending_correct가 True인 상태에서, 확정 전에 다시
    # 오답을 말하면 그 정답 상태는 지워져야 한다 — 안 그러면 core/quiz_tools.py가 오답
    # 다음에도 "정답 확정" 지연을 잘못 스케줄해버린다(코드 리뷰로 발견).
    s4d = QuizSession(make_questions(1), num_questions=1)
    s4d.start()
    s4d.choose_mode("annoying")
    s4d.resolve_user_guess("정답0")
    ok &= check("correct guess sets the pending-correct flag",
                s4d.annoying_pending_correct is True and s4d.pending_user_guess == "정답0")
    s4d.resolve_user_guess("이번엔땡")
    ok &= check(
        "a wrong guess right after a pending correct clears the stale correct flag",
        s4d.annoying_pending_correct is False and s4d.pending_user_guess == "이번엔땡",
    )

    # request_hint()도 pending을 "모름"으로 명시해야, 이전에 남아있던 오답 텍스트가
    # 엉뚱하게 다음 reveal에 기록되지 않는다.
    s4e = QuizSession(make_questions(1), num_questions=1)
    s4e.start()
    s4e.choose_mode("annoying")
    s4e.resolve_user_guess("아까오답")
    s4e.request_hint()
    ok &= check(
        "annoying hint request clears any earlier pending wrong-guess text",
        s4e.pending_user_guess is None and s4e.annoying_pending_dont_know is True,
    )
    s4e.reveal_annoying_wrong()
    ok &= check(
        "reveal after a bare hint request records a None/dont-know guess, not the stale wrong text",
        s4e.results[0].user_guess_text is None and s4e.results[0].user_dont_know is True,
    )

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

    # 2026-07-31 재설계: 하찮미 모드는 이제 답 시도만으로 전진하지 않고, 로봇도 같이
    # 추측해야(resolve_robot_guess) 비로소 기록/전진한다.
    s7.resolve_user_guess("정답2")
    s7.resolve_robot_guess("아무말")
    s7.resolve_user_guess("정답3")
    s7.resolve_robot_guess("아무말")  # 라운드2 종료

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
