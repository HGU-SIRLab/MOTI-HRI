"""core/quiz_tools.py의 Gemini 툴 배선을 검증한다 — 실제 하드웨어/Live API 없이,
모션 함수들을 목(mock)으로 교체하고 가짜 inject_turn으로 짜증유발 모드의 지연 주입
메커니즘까지 확인한다. API 키/로봇 불필요.

사용: python scripts/test_quiz_tools.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from queue import Queue

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

import core.quiz_tools as qt
from core.quiz_bank import load_question_bank

# 실제 스톨(10~12초)/정답 공개 유지(4초)를 기다리면 테스트가 너무 오래 걸리므로 확 줄인다.
qt.STALL_MIN_SEC = 0.05
qt.STALL_MAX_SEC = 0.08
qt.CORRECT_STALL_MIN_SEC = 0.05
qt.CORRECT_STALL_MAX_SEC = 0.08
qt.REVEAL_HOLD_SEC = 0.05

calls: list[tuple] = []
qt.play_look_away_motion = lambda port, pkt, lock, shared_state, home_pan: calls.append(("look_away",))
qt.play_thinking_stall = lambda port, pkt, lock, shared_state, emotion_queue: calls.append(("thinking_stall",))
qt.play_express_gesture = lambda joint, intensity, speed, repeat, port, pkt, lock, shared_state: calls.append(("express", joint))


def _make_temp_bank(tmpdir):
    path = os.path.join(tmpdir, "questions.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([
            {"id": "q0", "image_path": "q0.jpg", "answer": "정답0", "alternates": []},
            {"id": "q1", "image_path": "q1.jpg", "answer": "정답1", "alternates": []},
        ], f)
    return path


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


async def main():
    ok = True
    tmpdir = tempfile.mkdtemp()
    bank_path = _make_temp_bank(tmpdir)
    qt.load_question_bank = lambda: load_question_bank(bank_path)

    quiz_ui_q = Queue()
    busy = threading.Event()
    motion_ctx = (None, None, threading.Lock(), {"mode": "tracking"}, 2081, 2071)
    emotion_calls = []
    emotion_queue = type("FakeQueue", (), {"put": lambda self, x: emotion_calls.append(x)})()
    injected = []

    async def fake_inject_turn(text):
        injected.append(text)

    loop = asyncio.get_event_loop()
    start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early, session = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )

    start_quiz()
    ok &= check("start_quiz pushes rules to UI", quiz_ui_q.get()["type"] == "rules")

    ok &= check("invalid mode rejected", "알 수 없는 모드" in select_quiz_mode("bogus"))
    r = select_quiz_mode("imperfect")
    ok &= check("imperfect withholds answer", "정답0" not in r)
    msg = quiz_ui_q.get()
    # core/quiz_bank.py가 상대경로 image_path를 저장소 루트 기준 절대경로로 풀어주므로
    # (CWD에 의존하지 않게 하는 의도된 동작) 파일명만 확인한다.
    ok &= check("mode-select pushes first question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q0.jpg"))

    submit_guess("user", "모르겠어요")  # 포기 신호 -> "저도 맞춰볼게요" 이벤트로 감
    submit_guess("robot", "정답0")  # 로봇이 우연히 맞춤
    ok &= check("robot-correct triggers EXCITED emotion", emotion_calls == ["EXCITED"])
    time.sleep(0.1)
    ok &= check("robot-correct triggers proud arm motions", ("express", "right_arm") in calls and ("express", "left_arm") in calls)
    msg = quiz_ui_q.get()
    ok &= check("reveals original photo + answer before advancing",
                msg["type"] == "reveal" and "정답0" in msg["text"]
                and msg["image_path"].replace("\\", "/").endswith("q0.jpg"))
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("advances to second question after reveal hold",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))

    submit_guess("user", "정답 알려줘")  # 이것도 포기 신호로 인식돼야 함
    submit_guess("robot", "틀린답")  # 로봇도 틀림
    time.sleep(0.1)
    ok &= check("robot-wrong triggers look-away motion", ("look_away",) in calls)
    msg = quiz_ui_q.get()
    ok &= check("reveals final answer", msg["type"] == "reveal")
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("quiz ends after last question", msg["type"] == "hide")

    # 2026-07-31 재설계: 하찮미 모드는 실제 답 시도도 즉시 채점하지 않고, 로봇도 같이
    # 추측해서 나란히 비교하는 이벤트로 넘어간다 — 즉시 정오를 알려주면 로봇이 이미
    # 정답을 아는 것처럼 보여 조작 점검 문항과 모순된다는 실물 테스트 피드백.
    start1b, select1b, submit1b, hint1b, end1b, sess1b = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )
    start1b()
    quiz_ui_q.get()
    select1b("imperfect")
    quiz_ui_q.get()

    r = submit1b("user", "정답0")
    ok &= check("imperfect real guess triggers the kickoff event, no immediate verdict",
                "맞혔습니다" not in r and "submit_guess" in r)
    ok &= check("imperfect real guess does not advance yet", sess1b.index == 0)
    ok &= check("imperfect real guess pushes nothing to the UI yet", quiz_ui_q.empty())

    r = submit1b("robot", "정답0")  # 로봇도 우연히 맞춤 -> 둘 다 맞음, 이제야 전진
    ok &= check("paired guess advances the session", sess1b.index == 1)
    msg = quiz_ui_q.get()
    ok &= check("imperfect paired guess pushes reveal", msg["type"] == "reveal" and "정답0" in msg["text"])
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("imperfect paired guess advances UI to next question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))

    # 짜증유발 모드 힌트 스톨 + 지연 주입(가장 중요한 케이스)
    start2, select2, submit2, hint2, end2, sess2 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=1,
    )
    start2()
    quiz_ui_q.get()
    select2("annoying")
    quiz_ui_q.get()
    filler = hint2()
    ok &= check("annoying hint returns filler only, no refusal line yet", qt.MODE3_REFUSAL_LINE not in filler)
    await asyncio.sleep(0.2)
    ok &= check("delayed injection eventually fires the exact refusal line",
                any(qt.MODE3_REFUSAL_LINE in t for t in injected))
    time.sleep(0.1)
    ok &= check("annoying hint triggers thinking-stall motion", ("thinking_stall",) in calls)
    # 2026-07-31: 거절 뒤엔 곧장 reveal_annoying_wrong()으로 이어져(질문이 1개뿐이라 곧장
    # 종료), 그 사이 reveal + hide가 quiz_ui_q에 쌓인다 — 다음 테스트를 오염시키지 않게 비운다.
    ok &= check("bare hint request also reveals+advances (ends the only question)",
                quiz_ui_q.get()["type"] == "reveal" and quiz_ui_q.get()["type"] == "hide")

    # 힌트를 짧은 간격으로 두 번 요청해도 지연 주입은 한 번만 걸려야 한다(중복 발화 방지).
    injected.clear()
    start3, select3, submit3, hint3, end3, sess3 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=1,
    )
    start3()
    quiz_ui_q.get()
    select3("annoying")
    quiz_ui_q.get()
    hint3()
    hint3()  # 곧바로 또 요청 — 새 태스크가 중복 예약되면 안 됨
    await asyncio.sleep(0.2)
    ok &= check("duplicate hint requests only inject the refusal once",
                sum(1 for t in injected if qt.MODE3_REFUSAL_LINE in t) == 1)
    quiz_ui_q.get()  # reveal (거절 뒤 자동 공개)
    quiz_ui_q.get()  # hide (문항이 1개뿐이라 곧장 종료)

    # 짜증유발 모드에서 실제 답 제출(submit_guess) — 2026-07-31 1차 수정 검증: 오답이든
    # 정답이든 이 턴에서는 필러만 반환해야 한다(이전엔 all_knowing과 똑같이 오답에도
    # 곧장 정답을 공개해버렸다 — "정답을 모르는 척척박사"가 되어버린 조작 점검 모순).
    # 2차 수정 검증: 거절만 하고 끝나면 정답을 못 맞히는 한 영원히 다음 문제로 못
    # 넘어가는 사고가 실제 로봇 테스트로 났으므로, 거절 직후 곧장 정답을 공개하고
    # 전진해야 한다(정답을 몰라도 진행은 된다).
    injected.clear()
    calls.clear()
    startA, selectA, submitA, hintA, endA, sessA = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )
    startA()
    quiz_ui_q.get()
    selectA("annoying")
    quiz_ui_q.get()
    r = submitA("user", "땡땡땡")
    ok &= check("annoying wrong guess returns filler only (no answer leak)", "정답0" not in r)
    ok &= check("annoying wrong guess does not push anything to the UI yet", quiz_ui_q.empty())
    ok &= check("annoying wrong guess triggers the thinking-stall motion", ("thinking_stall",) in calls)
    await asyncio.sleep(0.15)  # STALL_MIN/MAX_SEC이 지나갈 때까지 대기
    ok &= check("delayed injection fires the exact refusal line for a wrong guess",
                any(qt.MODE3_REFUSAL_LINE in t for t in injected))
    ok &= check("refusal is immediately followed by revealing the real answer, not left hanging",
                any("사실 정답은" in t for t in injected))
    ok &= check("wrong guess DOES eventually advance the session (no more infinite stall)",
                sessA.index == 1 and len(sessA.results) == 1 and sessA.results[0].user_correct is False)
    msg = quiz_ui_q.get()
    ok &= check("wrong-guess reveal pushes the original photo + answer", msg["type"] == "reveal" and "정답0" in msg["text"])
    await asyncio.sleep(0.15)  # REVEAL_HOLD_SEC 경과 대기
    msg = quiz_ui_q.get()
    ok &= check("wrong-guess reveal advances UI to the next question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))

    # 두 번째(마지막) 문제 — 정답을 제출해도 즉시 확정되지 않고(필러만), 뜸들이기 지연
    # 끝에 reveal + 다음 문제 전환 + 확정 히든 턴까지 이어져야 한다.
    injected.clear()
    r = submitA("user", "정답1")
    ok &= check("annoying correct guess also returns filler only for now", "정답입니다" not in r)
    ok &= check("annoying correct guess does not push anything to the UI yet", quiz_ui_q.empty())
    await asyncio.sleep(0.15)
    ok &= check("delayed correct-confirm injects the confirmation text",
                any("정답입니다" in t for t in injected))
    msg = quiz_ui_q.get()
    ok &= check("correct-confirm pushes the reveal", msg["type"] == "reveal" and "정답1" in msg["text"])
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("correct-confirm ends the quiz after the last question", msg["type"] == "hide")
    ok &= check("session recorded both results (one wrong, one correct)",
                len(sessA.results) == 2 and sessA.results[0].user_correct is False
                and sessA.results[1].user_correct is True)

    # 회귀 방지: 정답을 먼저 제출해 "정답 확정" 스톨이 걸린 상태에서, 확정되기 전에
    # (실수로) 다시 오답을 말하면 정답 확정 스케줄은 취소되고 거절 스케줄로 교체돼야
    # 한다 — 안 그러면 방금 말한 오답인데도 뒤늦게 "정답입니다"가 튀어나오는 사고가 난다.
    injected.clear()
    startB, selectB, submitB, hintB, endB, sessB = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=1,
    )
    startB()
    quiz_ui_q.get()
    selectB("annoying")
    quiz_ui_q.get()
    submitB("user", "정답0")  # 정답 확정 스톨 예약
    submitB("user", "땡땡땡")  # 확정되기 전에 번복 -> 거절 스톨로 교체돼야 함
    await asyncio.sleep(0.15)
    ok &= check(
        "a wrong guess overriding a pending correct-confirm fires the refusal, not the confirmation",
        any(qt.MODE3_REFUSAL_LINE in t for t in injected) and not any("정답입니다" in t for t in injected),
    )
    ok &= check(
        "the overridden guess still eventually reveals+advances via the wrong-answer path",
        sessB.index == 1 and len(sessB.results) == 1 and sessB.results[0].user_correct is False,
    )
    quiz_ui_q.get()  # reveal
    quiz_ui_q.get()  # hide (문항이 1개뿐이라 REVEAL_HOLD_SEC 경과 후 바로 hide)

    # 힌트 요청(스톨 예약) 직후 바로 정답을 제출하면, 힌트의 거절 스케줄은 취소되고
    # 정답 확정 스케줄로 교체되어야 한다(엉뚱한 타이밍에 거절 대사가 튀어나오는 사고 방지).
    injected.clear()
    start4, select4, submit4, hint4, end4, sess4 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )
    start4()
    quiz_ui_q.get()
    select4("annoying")
    quiz_ui_q.get()
    hint4()  # 힌트 거절 스톨 예약
    submit4("user", "정답0")  # 곧바로 정답 제출 -> 힌트의 거절 스톨은 취소되고 정답 확정 스톨로 교체됨
    await asyncio.sleep(0.15)  # 정답 확정 스톨(CORRECT_STALL_*)이 지나갈 때까지 대기
    ok &= check("submitting the correct answer cancels the pending hint refusal",
                not any(qt.MODE3_REFUSAL_LINE in t for t in injected))
    ok &= check("delayed correct-confirm eventually injects a confirmation turn",
                any("정답입니다" in t for t in injected))
    quiz_ui_q.get()  # reveal
    await asyncio.sleep(0.15)  # REVEAL_HOLD_SEC 경과 대기
    quiz_ui_q.get()  # 다음 문제 메시지까지 비워서 이후 테스트 오염 방지

    # 모드가 이미 선택된 뒤 재선택을 시도하면 거부돼야 한다 — 안 그러면 index가 0으로
    # 리셋돼 같은 문제가 연구 결과 로그에 중복 기록될 위험이 있다.
    start5, select5, submit5, hint5, end5, sess5 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )
    start5()
    quiz_ui_q.get()
    select5("imperfect")
    quiz_ui_q.get()
    r = select5("annoying")
    ok &= check("re-selecting mode after it's already chosen is rejected", "이미 모드가 선택" in r)
    ok &= check("mode stays unchanged after rejected re-selection", sess5.mode == "imperfect")

    # resolve_robot_guess가 무효 상황(사용자 차례 없이 로봇 추측부터 제출)이라 아무것도
    # 기록/전진하지 않았을 때, 그런데도 reveal이 뜨면 아직 안 풀린 문제의 "정답 공개"
    # 화면이 잘못 뜨는 사고가 난다 — 실제로 전진했을 때만 reveal이 떠야 한다.
    start6, select6, submit6, hint6, end6, sess6 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=2,
    )
    start6()
    quiz_ui_q.get()
    select6("imperfect")
    quiz_ui_q.get()
    r = submit6("robot", "아무거나")  # 사용자 차례 없이 곧바로 로봇 추측(비정상 순서)
    ok &= check("invalid robot guess without a pending user guess is ignored", "무시" in r)
    ok &= check("no spurious reveal is pushed for a no-op resolve", quiz_ui_q.empty())

    # 퀴즈가 이미 자연 종료된 뒤에 다시 답을 제출해도(비정상) reveal이 뜨면 안 된다.
    start7, select7, submit7, hint7, end7, sess7 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, emotion_queue=emotion_queue, num_questions=1,
    )
    start7()
    quiz_ui_q.get()
    select7("all_knowing")
    quiz_ui_q.get()
    submit7("user", "정답0")  # 유일한 문제를 답변 -> 퀴즈 자연 종료(active=False)
    quiz_ui_q.get()  # reveal
    await asyncio.sleep(0.15)
    quiz_ui_q.get()  # REVEAL_HOLD_SEC 경과 후 hide
    ok &= check("queue drained after quiz naturally ends", quiz_ui_q.empty())
    submit7("user", "아무말")  # 퀴즈가 이미 끝난 뒤 비정상적으로 재제출
    ok &= check("submitting after the quiz already ended pushes nothing", quiz_ui_q.empty())

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
