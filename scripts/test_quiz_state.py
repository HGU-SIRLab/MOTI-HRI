"""core/quiz_state.py의 QuizSession을 3개 모드 전체 흐름으로 구동해 검증한다.
API 키/로봇 불필요 — 모드별 정보 비대칭(척척박사만 정답을 앎), 하찮미 모드의
페어드 리빌(사용자 추측 + 로봇 추측을 함께 채점), 그리고 진행자가 정한 순서대로
3라운드가 자동으로 이어지는 흐름(45단계)이 이 테스트의 핵심.

사용: python scripts/test_quiz_state.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.quiz_bank import QuizQuestion
from core.quiz_state import (DEFAULT_MODE_ORDER, MODE3_REFUSAL_LINE, QuizSession,
                             mode_label, parse_mode_order)


def make_questions(n):
    return [QuizQuestion(id=f"q{i}", image_path=f"q{i}.jpg", answer=f"정답{i}", alternates=[f"답{i}"])
            for i in range(n)]


def new_session(bank_size, num_questions, modes):
    """한 모드짜리(또는 여러 모드짜리) 세션을 만들고 첫 라운드까지 시작해 돌려준다 —
    실제로는 core/quiz_tools.py가 전체 안내 발화가 끝난 뒤 begin_next_round()를 부른다."""
    s = QuizSession(make_questions(bank_size), num_questions=num_questions, mode_order=modes)
    s.start()
    intro = s.begin_next_round()
    return s, intro


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True

    # 0. .env의 QUIZ_MODE_ORDER 파싱 — 진행자가 오타를 내면 조용히 기본값으로 넘어가지
    # 않고 반드시 예외를 던져야 한다(카운터밸런싱이 깨진 데이터는 나중에 복구 불가).
    ok &= check("mode order parses numbers", parse_mode_order("2,3,1") == ["imperfect", "annoying", "all_knowing"])
    ok &= check("mode order parses Korean names",
                parse_mode_order("하찮미 짜증유발 척척박사") == ["imperfect", "annoying", "all_knowing"])
    ok &= check("empty mode order falls back to the default", parse_mode_order(None) == list(DEFAULT_MODE_ORDER))
    for bad in ("1,2", "1,2,2", "4,1,2", "척척박사,하찮미,없는모드"):
        try:
            parse_mode_order(bad)
            ok &= check(f"invalid mode order rejected: {bad!r}", False)
        except ValueError:
            ok &= check(f"invalid mode order rejected: {bad!r}", True)

    # 0b. 전체 안내(start) — 참가자에게 읽어줄 총 문항 수와 모드 구성이 들어있어야 한다.
    s0 = QuizSession(make_questions(15), num_questions=5)
    intro = s0.start()
    ok &= check("start() announces the total question count (5 x 3 = 15)", "15문제" in intro)
    ok &= check("start() announces 5 questions per mode", "5문제씩" in intro)
    ok &= check("start() names all three modes",
                all(n in intro for n in ("척척박사", "하찮미", "짜증유발")))
    ok &= check("start() states that mode 1 knows the answer and 2/3 do not",
                "1번 척척박사 모드에서는 제가 정답을 알고" in intro
                and "2번 하찮미 모드와 3번 짜증유발 모드에서는 저도 정답을 모르는" in intro)
    # 모드는 진행자가 미리 정한다(45단계) — 안내문은 "어떤 모드로 할지 묻지 말라"고
    # 명시해야 한다(예전 select_quiz_mode 시절의 "몇 번 모드로 하시겠어요?"가 남으면
    # 참가자가 순서를 고르려 들어 카운터밸런싱이 깨진다).
    ok &= check("start() tells the robot not to ask the user to pick a mode",
                "묻지 말고" in intro and "순서는 이미 정해져 있습니다" in intro)
    ok &= check("start() leaves no round running yet (the first round begins after the intro is spoken)",
                s0.mode is None and s0.current_question is None and s0.round_index == 0)
    txt = s0.resolve_user_guess("아무말")
    ok &= check("guessing during the intro is refused with an actionable message",
                "안내 중" in txt and len(s0.results) == 0)

    # 1. 척척박사 — 정답을 처음부터 알고, 오답이면 바로 공개.
    # 2026-08-08부터 판정 툴의 즉시 응답은 침묵 지시(_HOLD_FOR_REVEAL)뿐이고, 실제 정답/
    # 반응 텍스트는 core/quiz_tools.py가 정답 이미지를 띄운 뒤 별도 히든 턴으로 주입할 수
    # 있도록 session.pending_reveal_speech에 담긴다(구조 변경 이유는 core/quiz_state.py
    # 상단 _HOLD_FOR_REVEAL 주석 참고 — 정답 공개가 로봇이 말하기도 전에 뜨던 사고 대응).
    s, txt = new_session(3, 3, ["all_knowing"])
    ok &= check("all_knowing round intro reveals the first answer (internal use only)", "정답0" in txt)
    ok &= check("round intro tells the robot to stop before introducing the question",
                "미리 묻지 마세요" in txt)
    ok &= check("round intro announces which mode it is", mode_label("all_knowing") in txt)
    s.mark_question_shown()  # 실제로는 core/quiz_tools.py가 문제를 UI에 push할 때 호출
    txt = s.resolve_user_guess("땡땡땡")
    ok &= check("wrong guess withholds the answer from the immediate response",
                "정답0" not in txt and s.index == 1)
    ok &= check("wrong guess defers the answer to pending_reveal_speech",
                s.pending_reveal_speech is not None and "정답0" in s.pending_reveal_speech)
    ok &= check("elapsed time is recorded when the question was marked as shown",
                s.results[0].elapsed_sec is not None and s.results[0].elapsed_sec >= 0)
    ok &= check("annoying refusal count stays None outside annoying mode",
                s.results[0].annoying_refusals is None)
    txt = s.resolve_user_guess("정답1")
    ok &= check("correct guess withholds praise from the immediate response",
                "맞혔습니다" not in txt and s.index == 2)
    ok &= check("correct guess defers praise to pending_reveal_speech",
                s.pending_reveal_speech is not None and "맞혔습니다" in s.pending_reveal_speech)
    ok &= check("elapsed time stays None when the question was never marked as shown",
                s.results[1].elapsed_sec is None)
    txt = s.resolve_user_guess("오답")
    ok &= check("last question of the last round ends the quiz, immediate response stays silent",
                "모든 문제가 끝났습니다" not in txt and not s.active and s.round_finished)
    ok &= check("all_knowing logged 3 results", len(s.results) == 3)
    ok &= check("final_wrapup_prompt announces the quiz is over",
                "모두 끝났습니다" in s.final_wrapup_prompt())
    ok &= check("no round is left to begin", s.begin_next_round() is None)

    # 2. 하찮미 — 2026-07-31부터 포기/위임 신호든 실제 답 시도든 전부 "로봇도 같이
    # 추측해서 나란히 비교" 이벤트로 통일됐다. 즉시 정오를 알려주면(19~20단계 방식) 로봇이
    # 이미 정답을 아는 것처럼 보여 조작 점검 문항("로봇이 정답을 모르는 상태로 함께
    # 풀었다")과 모순된다는 실물 테스트 피드백으로 재설계.
    s2, txt = new_session(3, 3, ["imperfect"])
    ok &= check("imperfect round intro withholds the answer", "정답0" not in txt)
    ok &= check("imperfect round intro states the robot does not know the answer either",
                "저도 정답을 모르는 상태" in txt)
    # 2026-08-10(교수님 지시): 어려운 문제에서 참가자가 로봇에게 도움을 요청할 수 있다는
    # 걸 참가자가 알아야 한다 — 안내에 그 문장이 반드시 들어가야 한다.
    ok &= check("imperfect round intro invites the user to ask for help on hard questions",
                "도와달라고" in txt and "모르겠어요" in txt)

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
        "both user and robot correct -> teamwork reaction deferred, immediate response stays silent",
        "팀워크" not in txt and s2.index == 1 and len(s2.results) == 1
        and s2.results[0].user_correct is True and s2.results[0].robot_correct is True,
    )
    ok &= check("teamwork reaction lands in pending_reveal_speech",
                s2.pending_reveal_speech is not None and "팀워크" in s2.pending_reveal_speech)

    # 2a'. 사용자는 맞혔지만 로봇은 틀림 -> 로봇이 자기 오답을 웃음거리로 삼으며 사용자를 인정.
    txt = s2.resolve_user_guess("정답1")
    ok &= check("real correct guess also triggers the kickoff event (not judged immediately)",
                "submit_guess" in txt and s2.pending_user_guess == "정답1")
    txt = s2.resolve_robot_guess("로봇오답")
    ok &= check(
        "user correct + robot wrong -> immediate response stays silent",
        "어이쿠" not in txt and s2.results[1].user_correct is True and s2.results[1].robot_correct is False,
    )
    ok &= check("sheepishly-embarrassed reaction lands in pending_reveal_speech",
                "어이쿠" in s2.pending_reveal_speech)

    # 2a''. 둘 다 틀림 -> 로봇이 자기 오답을 더 재미있어하며 웃어넘김(하찮미다움 강화, 2026-07-31).
    s2.resolve_user_guess("땡땡")
    txt = s2.resolve_robot_guess("로봇도땡")
    ok &= check(
        "both wrong -> immediate response stays silent",
        "낙제" not in txt and s2.results[2].user_correct is False and s2.results[2].robot_correct is False,
    )
    ok &= check("both-wrong-for-laughs reaction lands in pending_reveal_speech",
                "낙제" in s2.pending_reveal_speech)
    ok &= check("round ends after all 3 questions", s2.round_finished and not s2.active)

    # 2b. "모르겠어요" — 포기 신호는 실제 답이 없으므로 "둘 다"라는 비교 틀이 안 맞아,
    # 기존처럼 로봇 자신의 결과만으로 반응한다.
    s2b, _ = new_session(2, 2, ["imperfect"])
    txt = s2b.resolve_user_guess("모르겠어요")
    ok &= check("give-up phrase triggers kickoff event", "submit_guess" in txt and s2b.pending_user_guess == "모르겠어요")
    txt = s2b.resolve_robot_guess("정답0")
    ok &= check("robot guess correct -> immediate response stays silent (give-up path)",
                "뿌듯" not in txt and s2b.pending_user_guess is None and s2b.index == 1)
    ok &= check("proud reaction (give-up path) lands in pending_reveal_speech",
                "뿌듯" in s2b.pending_reveal_speech)
    r0 = s2b.results[0]
    ok &= check("paired result recorded (robot correct)", r0.user_guess_text == "모르겠어요" and r0.robot_correct is True)

    # 2c. "정답 알려줘" — 힌트류 포기 신호도 같은 이벤트로 감.
    txt = s2b.resolve_user_guess("정답 알려줘")
    ok &= check("hand-it-to-me phrase also triggers kickoff event", "submit_guess" in txt and s2b.pending_user_guess == "정답 알려줘")
    txt = s2b.resolve_robot_guess("로봇오답")
    ok &= check("robot guess wrong -> immediate response stays silent (give-up path)",
                "헤헤" not in txt and not s2b.active)
    ok &= check("flustered-but-cute reaction (give-up path) lands in pending_reveal_speech",
                "헤헤" in s2b.pending_reveal_speech)
    ok &= check("second result recorded (robot wrong)", s2b.results[1].robot_correct is False)

    # 2d. request_hint()도 같은 "저도 맞춰볼게요" 이벤트로 통일됨.
    s2c, _ = new_session(1, 1, ["imperfect"])
    txt = s2c.request_hint()
    ok &= check(
        "imperfect hint request triggers kickoff event",
        "submit_guess" in txt and s2c.pending_user_guess is not None,
    )
    txt = s2c.resolve_robot_guess("정답0")
    ok &= check("hint-triggered robot guess resolves and advances, immediate response stays silent",
                "뿌듯" not in txt and not s2c.active)
    ok &= check("hint-triggered reaction lands in pending_reveal_speech",
                "뿌듯" in s2c.pending_reveal_speech)

    # 3. 방어 가드 — imperfect 아닐 때 resolve_robot_guess는 무시
    s3, _ = new_session(1, 1, ["all_knowing"])
    txt = s3.resolve_robot_guess("아무말")
    ok &= check("resolve_robot_guess ignored outside imperfect mode", "무시" in txt)

    # 4. 짜증유발 — quiz_state.py 자체는 필러만 반환, 거절 대사는 core/quiz_tools.py가 지연 주입으로 처리
    s4, txt = new_session(1, 1, ["annoying"])
    ok &= check("annoying round intro states the robot does not know the answer either, bluntly",
                "저도 정답을 모르는 상태" in txt and "무뚝뚝한" in txt)
    txt = s4.request_hint()
    ok &= check("annoying hint does NOT contain the refusal line itself", MODE3_REFUSAL_LINE not in txt)

    # 4b. 2026-07-31 재설계(3차): 짜증유발 모드는 실제 답 시도(맞았든 틀렸든)에 예외 없이
    # 매번 거절만 하고 절대 진행하지 않는다 — "거절해놓고 몇 초 뒤 바로 정답을 알려주면
    # 사실 알고 있었던 것처럼 보인다"는 지적으로, 이전(1~2차)의 "거절 후 자동 공개" 설계를
    # 되돌렸다. 참가자가 명시적으로 포기/스킵을 요청할 때만(judge_guess의 is_dont_know)
    # 그 자리에서 곧장 정답을 공개하고 전진한다("나는 모르지만 화면 정보를 전달한다" 서사).
    s4b, _ = new_session(2, 2, ["annoying"])
    txt = s4b.resolve_user_guess("땡땡땡")
    ok &= check(
        "annoying wrong guess withholds answer, does not advance, filler only",
        "정답0" not in txt and s4b.index == 0 and len(s4b.results) == 0
        and s4b.pending_user_guess == "땡땡땡" and s4b.annoying_pending_correct is False,
    )
    # 계속 답을 시도해도(맞아도!) 진행되지 않는다 — 정답을 맞혀도 그냥 거절만 당한다.
    txt = s4b.resolve_user_guess("정답0")
    ok &= check(
        "even a CORRECT guess still gets withheld/refused, no advance",
        "정답0" not in txt and s4b.index == 0 and len(s4b.results) == 0
        and s4b.pending_user_guess == "정답0" and s4b.annoying_pending_correct is True,
    )
    # 두 번의 시도에 대해 거절 대사가 실제로 발화됐다고 가정(실제로는 core/quiz_tools.py의
    # _delayed_refusal이 주입 직후 호출) — 포기 시점 로그에 2회로 남아야 한다.
    s4b.note_refusal_delivered()
    s4b.note_refusal_delivered()
    # 명시적으로 포기/스킵을 요청해야만 그 자리에서 곧장 정답이 공개되고 전진한다 —
    # 직전 실제 시도(정답0, 맞았음)의 정오는 로그에 보존된다.
    txt = s4b.resolve_user_guess("그냥 다음 문제로 넘어가줘")
    ok &= check(
        "an explicit give-up/skip request advances immediately, immediate response stays silent",
        "정답0" not in txt and s4b.index == 1 and len(s4b.results) == 1
        and s4b.pending_user_guess is None,
    )
    r0 = s4b.results[0]
    ok &= check(
        "the give-up result preserves the last real attempt's correctness for logging",
        r0.user_correct is True and r0.user_dont_know is True
        and r0.user_guess_text == "그냥 다음 문제로 넘어가줘",
    )
    ok &= check(
        "reveal wording (deferred) frames it as reading the screen, not the robot's own knowledge",
        s4b.pending_reveal_speech is not None and "정답0" in s4b.pending_reveal_speech
        and "화면에 적힌" in s4b.pending_reveal_speech,
    )
    ok &= check("delivered refusal count is recorded on the give-up result",
                r0.annoying_refusals == 2)

    # 두 번째(마지막) 문제 — 이번엔 실제 시도 없이 곧장 포기하면 correctness가 False로 남는다.
    txt = s4b.resolve_user_guess("정답 알려줘")
    ok &= check(
        "giving up with no prior real attempt records user_correct=False, immediate response silent",
        "정답1" not in txt and s4b.index == 2 and len(s4b.results) == 2
        and s4b.results[1].user_correct is False and s4b.results[1].user_dont_know is True
        and not s4b.active,
    )
    ok &= check("second give-up's reveal speech (deferred) contains the answer",
                s4b.pending_reveal_speech is not None and "정답1" in s4b.pending_reveal_speech)
    ok &= check("refusal counter resets per question",
                s4b.results[1].annoying_refusals == 0)

    # request_hint()도 정오 로그를 초기화해야 한다 — 힌트를 물었다는 건 실제로 맞힌 적이
    # 없다는 뜻이므로, 그 직후 포기하면 정확하게 False로 기록돼야 한다.
    s4c, _ = new_session(1, 1, ["annoying"])
    s4c.resolve_user_guess("정답0")  # 실제로 맞혔지만
    s4c.request_hint()  # 그 뒤 힌트를 물었으니 "맞힌 적 없음"으로 리셋돼야 함
    ok &= check("hint request resets the pending-correct flag even after a correct guess",
                s4c.annoying_pending_correct is False)
    txt = s4c.resolve_user_guess("그만할래")
    ok &= check("give-up after a hint request records user_correct=False",
                s4c.results[0].user_correct is False)

    # 5. 조기 종료 — 남은 라운드를 소진시키지 않아야 한다(모델이 실수로 불러도 복구 가능해야 함).
    s5, _ = new_session(15, 5, ["all_knowing", "imperfect", "annoying"])
    s5.end_early()
    ok &= check("end_early deactivates session", not s5.active)
    ok &= check("end_early keeps the remaining rounds recoverable",
                s5.round_index == 1 and s5.begin_next_round() is not None)

    # 6. export_log 필드 — elapsed_sec/annoying_refusals는 실험 지표(docs/experiment_design.md §5)
    log = s2.export_log()
    ok &= check("export_log has expected fields",
                len(log) == 3 and "question_id" in log[0] and "timestamp" in log[0]
                and "elapsed_sec" in log[0] and "annoying_refusals" in log[0])

    # 7. 진행자가 정한 순서대로 3라운드가 한 세션에서 자동으로 이어진다(45단계).
    # 라운드마다 겹치지 않는 문제 세트를 써야 앞 라운드에서 공개된 정답이 다음 라운드를
    # 오염시키지 않는다(2026-07-31 도입, 여전히 유효).
    order = ["imperfect", "annoying", "all_knowing"]  # 2-3-1 배정
    s7 = QuizSession(make_questions(6), num_questions=2, mode_order=order)
    s7.start()
    seen_ids = []
    for round_no, expected_mode in enumerate(order, start=1):
        txt = s7.begin_next_round()
        ok &= check(f"round {round_no} starts in the assigned mode ({expected_mode})",
                    txt is not None and s7.mode == expected_mode and s7.index == 0 and s7.active)
        ok &= check(f"round {round_no} intro names the mode to the participant",
                    mode_label(expected_mode) in txt)
        if round_no > 1:
            ok &= check(f"round {round_no} intro explicitly announces the mode change",
                        "모드가 바뀝니다" in txt)
        ids = [q.id for q in s7.questions]
        ok &= check(f"round {round_no} uses a question set disjoint from every earlier round",
                    set(ids).isdisjoint(seen_ids))
        seen_ids += ids
        # 두 문제를 소진 — 모드마다 "전진하는 조건"이 다르다: 하찮미는 로봇 추측까지
        # 있어야 하고, 짜증유발은 명시적 포기/스킵 요청에만 전진한다(그 외에는 매번 거절).
        for _ in range(2):
            if expected_mode == "annoying":
                s7.resolve_user_guess("그냥 다음 문제로 넘어가줘")
                continue
            s7.resolve_user_guess("아무답")
            if expected_mode == "imperfect":
                s7.resolve_robot_guess("아무추측")
        if round_no < len(order):
            ok &= check(f"quiz stays active between round {round_no} and {round_no + 1}",
                        s7.active and s7.round_finished)
    ok &= check("all rounds accumulate into one result log", len(s7.results) == 6)
    ok &= check("quiz deactivates only after the final round", not s7.active)
    ok &= check("no extra round is started past the assigned order", s7.begin_next_round() is None)
    modes_logged = [r.mode for r in s7.results]
    ok &= check("results are logged under the mode that was actually running",
                modes_logged == ["imperfect"] * 2 + ["annoying"] * 2 + ["all_knowing"] * 2)

    # 7b. 라운드 사이(전환 대기 중)에 사용자가 답을 말해도 그게 다음 라운드 문항으로
    # 채점되면 안 된다 — 전환 중이라는 안내만 돌려주고 아무것도 소모하지 않아야 한다.
    s8 = QuizSession(make_questions(4), num_questions=2, mode_order=["all_knowing", "imperfect"])
    s8.start()
    s8.begin_next_round()
    s8.resolve_user_guess("정답0")
    s8.resolve_user_guess("정답1")  # 1라운드 소진 -> 전환 대기
    ok &= check("between rounds the quiz is still active but has no current question",
                s8.active and s8.round_finished and s8.current_question is None)
    before = len(s8.results)
    txt = s8.resolve_user_guess("아무말")
    ok &= check("guessing between rounds is refused and consumes nothing",
                "모드 전환 중" in txt and len(s8.results) == before)

    # 7c. 크래시 복구(QUIZ_ROUND_OFFSET) — 이미 마친 라운드 수만큼 모드와 사진을 함께 건너뛴다.
    s9 = QuizSession(make_questions(6), num_questions=2, mode_order=order, initial_round_offset=1)
    s9.start()
    s9.begin_next_round()
    ok &= check("round offset skips the already-played mode", s9.mode == order[1])
    ok &= check("round offset also skips the already-shown photos",
                [q.id for q in s9.questions] == ["q2", "q3"])

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
