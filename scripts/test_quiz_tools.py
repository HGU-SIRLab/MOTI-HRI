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
qt.REVEAL_HOLD_SEC = 0.05
qt.POST_SPEECH_DRAIN_SEC = 0.01

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
    # 실제로는 launcher.py의 recv_loop가 갱신하는 이벤트 — 로봇이 지금 말하는 중이
    # 아닐 때만 set된다. 테스트는 오디오 재생 자체를 시뮬레이션하지 않으므로 항상
    # set된 채로 둔다(정답 공개가 speaking_done을 기다리는 로직 자체는 여전히 실행되고,
    # POST_SPEECH_DRAIN_SEC을 짧게 패치해둔 덕에 대기 시간도 작다).
    speaking_done = asyncio.Event()
    speaking_done.set()
    start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early, session = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
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
    # asyncio.sleep이어야 한다 — reveal push는 이제 speaking_done을 기다리는 태스크 안에서
    # 일어나므로(2026-07-31), 이벤트 루프에 제어권을 넘기지 않는 time.sleep으론 그 태스크가
    # 진행되지 않는다.
    await asyncio.sleep(0.1)
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
    await asyncio.sleep(0.1)
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
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
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
    await asyncio.sleep(0.1)  # reveal push가 speaking_done 대기 태스크를 통해 일어날 시간을 준다
    msg = quiz_ui_q.get()
    ok &= check("imperfect paired guess pushes reveal", msg["type"] == "reveal" and "정답0" in msg["text"])
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("imperfect paired guess advances UI to next question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))

    # 짜증유발 모드 힌트 스톨 + 지연 주입 — 2026-07-31 재설계(3차): 거절만 하고 멈춘다.
    # 정답 공개/전진은 없다("거절해놓고 몇 초 뒤 정답을 알려주면 사실 알고 있었던 것"으로
    # 보인다는 지적으로 "거절 후 자동 공개" 설계를 되돌림).
    start2, select2, submit2, hint2, end2, sess2 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=1,
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
    ok &= check("refusal alone never advances or pushes anything to the UI",
                sess2.index == 0 and len(sess2.results) == 0 and quiz_ui_q.empty())

    # 힌트를 짧은 간격으로 두 번 요청해도 지연 주입은 한 번만 걸려야 한다(중복 발화 방지).
    injected.clear()
    start3, select3, submit3, hint3, end3, sess3 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=1,
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

    # 짜증유발 모드에서 실제 답 제출(submit_guess) — 오답이든 정답이든 매번 거절만 하고
    # 절대 진행하지 않는다. 명시적으로 포기/스킵을 요청할 때만 그 자리에서 곧장(뜸들임
    # 없이) 정답이 공개되고 전진한다.
    injected.clear()
    calls.clear()
    emotion_calls.clear()
    startA, selectA, submitA, hintA, endA, sessA = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
    )
    startA()
    quiz_ui_q.get()
    selectA("annoying")
    quiz_ui_q.get()
    r = submitA("user", "땡땡땡")
    ok &= check("annoying wrong guess returns filler only (no answer leak)", "정답0" not in r)
    ok &= check("annoying wrong guess does not push anything to the UI yet", quiz_ui_q.empty())
    ok &= check("annoying wrong guess triggers the thinking-stall motion", ("thinking_stall",) in calls)
    # 표정 시퀀스(2026-08-07): 답 시도를 받으면 뜸들이는 동안 THINKING.
    ok &= check("annoying attempt switches the face to THINKING for the stall",
                emotion_calls[-1] == "THINKING")
    await asyncio.sleep(0.15)  # STALL_MIN/MAX_SEC이 지나갈 때까지 대기
    ok &= check("delayed injection fires the exact refusal line for a wrong guess",
                any(qt.MODE3_REFUSAL_LINE in t for t in injected))
    # 거절 대사가 주입되는 순간 무표정으로 복귀해야 한다(THINKING인 채로 거절하면 안 됨).
    ok &= check("the face returns to NEUTRAL when the refusal is delivered",
                emotion_calls[-1] == "NEUTRAL")
    ok &= check("refusal alone never advances the session (no more auto-reveal)",
                sessA.index == 0 and len(sessA.results) == 0 and quiz_ui_q.empty())

    # 정답을 말해도 마찬가지로 그냥 거절만 당한다 — 절대 알려주지 않는다.
    injected.clear()
    r = submitA("user", "정답0")
    ok &= check("even a correct guess returns filler only (no confirmation)",
                "정답0" not in r and "정답입니다" not in r)
    await asyncio.sleep(0.15)
    ok &= check("correct guess also just gets refused, still does not advance",
                any(qt.MODE3_REFUSAL_LINE in t for t in injected)
                and sessA.index == 0 and len(sessA.results) == 0 and quiz_ui_q.empty())

    # 답답해서 명시적으로 포기/스킵을 요청하면 그 자리에서 곧장(뜸들임 없이) 정답 공개 + 전진.
    injected.clear()
    r = submitA("user", "그냥 다음 문제로 넘어가줘")
    ok &= check("an explicit give-up request immediately reveals, no stalling",
                "정답0" in r and "화면에 적힌" in r)
    # 포기로 정답을 공개할 때도 THINKING이 남지 않고 무표정으로 돌아온다.
    ok &= check("the face returns to NEUTRAL on give-up reveal too",
                emotion_calls[-1] == "NEUTRAL")
    ok &= check("give-up advances the session right away", sessA.index == 1 and len(sessA.results) == 1)
    ok &= check(
        "the logged result preserves the last real (correct) attempt's correctness",
        sessA.results[0].user_correct is True and sessA.results[0].user_dont_know is True,
    )
    # 실험 지표(docs/experiment_design.md §5): 이 문제에서 참가자는 거절을 정확히 2번
    # (오답 1회 + 정답 1회 시도) 겪은 뒤 포기했다 — 실제로 발화 주입된 횟수만 세야 한다.
    # 문제당 소요시간도 문제 push 시점부터 기록돼 있어야 한다.
    ok &= check(
        "the logged result captures delivered refusal count and elapsed time",
        sessA.results[0].annoying_refusals == 2 and sessA.results[0].elapsed_sec is not None,
    )
    await asyncio.sleep(0.1)  # reveal push가 speaking_done 대기 태스크를 통해 일어날 시간을 준다
    msg = quiz_ui_q.get()
    ok &= check("give-up reveal pushes the original photo + answer", msg["type"] == "reveal" and "정답0" in msg["text"])
    await asyncio.sleep(0.15)
    msg = quiz_ui_q.get()
    ok &= check("give-up reveal advances UI to the next question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))
    ok &= check("no stray refusal was injected for the give-up request itself",
                not any(t == qt.MODE3_REFUSAL_LINE for t in injected))

    # 회귀 방지: 오답을 방금 제출해 거절 스톨이 아직 대기 중인 상태에서, 곧바로 포기/스킵을
    # 요청하면 그 스톨은 취소되고 즉시 정답이 공개돼야 한다 — 안 그러면 이미 다음 문제로
    # 넘어갔는데 뒤늦게 이전 문제의 거절 대사가 튀어나오는 사고가 난다.
    injected.clear()
    startB, selectB, submitB, hintB, endB, sessB = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=1,
    )
    startB()
    quiz_ui_q.get()
    selectB("annoying")
    quiz_ui_q.get()
    submitB("user", "땡땡땡")  # 거절 스톨 예약(아직 안 걸림)
    submitB("user", "그냥 공개해줘")  # 곧바로 포기 -> 스톨은 취소되고 즉시 정답 공개
    ok &= check(
        "an immediate give-up cancels the pending refusal stall and resolves right away",
        sessB.index == 1 and len(sessB.results) == 1,
    )
    # 예약만 되고 취소된 스톨은 참가자가 실제로 겪은 거절이 아니다 — 0으로 남아야 한다.
    ok &= check("a cancelled (never-delivered) refusal is not counted",
                sessB.results[0].annoying_refusals == 0)
    await asyncio.sleep(0.1)  # reveal push가 speaking_done 대기 태스크를 통해 일어날 시간을 준다
    quiz_ui_q.get()  # reveal
    await asyncio.sleep(0.15)  # REVEAL_HOLD_SEC 경과 + 취소된 스톨이 원래 발화했을 시간까지 대기
    msg = quiz_ui_q.get()
    ok &= check("give-up advances UI to hide (last question)", msg["type"] == "hide")
    ok &= check("the cancelled stall never injects the refusal line",
                not any(qt.MODE3_REFUSAL_LINE in t for t in injected))

    # 힌트 요청(스톨 예약) 직후 바로 포기/스킵을 요청해도 마찬가지로 스톨이 취소되고
    # 즉시 정답이 공개돼야 한다.
    injected.clear()
    start4, select4, submit4, hint4, end4, sess4 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
    )
    start4()
    quiz_ui_q.get()
    select4("annoying")
    quiz_ui_q.get()
    hint4()  # 힌트 거절 스톨 예약
    submit4("user", "정답 알려줘")  # 곧바로 포기 요청 -> 스톨 취소, 즉시 정답 공개
    ok &= check("give-up right after a hint request resolves immediately",
                sess4.index == 1 and len(sess4.results) == 1)
    await asyncio.sleep(0.1)  # reveal push가 speaking_done 대기 태스크를 통해 일어날 시간을 준다
    quiz_ui_q.get()  # reveal
    await asyncio.sleep(0.15)  # REVEAL_HOLD_SEC 경과 + 취소된 힌트 스톨이 원래 발화했을 시간까지 대기
    msg = quiz_ui_q.get()
    ok &= check("advances UI to the next question",
                msg["type"] == "question" and msg["image_path"].replace("\\", "/").endswith("q1.jpg"))
    ok &= check("the cancelled hint stall never injects the refusal line",
                not any(qt.MODE3_REFUSAL_LINE in t for t in injected))

    # 모드가 이미 선택된 뒤 재선택을 시도하면 거부돼야 한다 — 안 그러면 index가 0으로
    # 리셋돼 같은 문제가 연구 결과 로그에 중복 기록될 위험이 있다.
    start5, select5, submit5, hint5, end5, sess5 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
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
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=2,
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
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done, emotion_queue=emotion_queue, num_questions=1,
    )
    start7()
    quiz_ui_q.get()
    select7("all_knowing")
    quiz_ui_q.get()
    submit7("user", "정답0")  # 유일한 문제를 답변 -> 퀴즈 자연 종료(active=False)
    await asyncio.sleep(0.1)  # reveal push가 speaking_done 대기 태스크를 통해 일어날 시간을 준다
    quiz_ui_q.get()  # reveal
    await asyncio.sleep(0.15)
    quiz_ui_q.get()  # REVEAL_HOLD_SEC 경과 후 hide
    ok &= check("queue drained after quiz naturally ends", quiz_ui_q.empty())
    submit7("user", "아무말")  # 퀴즈가 이미 끝난 뒤 비정상적으로 재제출
    ok &= check("submitting after the quiz already ended pushes nothing", quiz_ui_q.empty())

    # 회귀 방지(2026-07-31): 로봇이 아직 말하는 중(speaking_done이 clear)이면 정답 공개
    # 화면이 그동안 미뤄져야 한다 — 이게 없으면 로봇이 자기 추측/비교 멘트를 채 말하기도
    # 전에 정답 화면이 떠버리는 사고가 재현된다(실사용 피드백으로 발견).
    speaking_done_busy = asyncio.Event()  # 기본이 clear 상태 -> "로봇이 아직 말하는 중"
    start8, select8, submit8, hint8, end8, sess8 = qt.make_quiz_tools(
        quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, speaking_done_busy,
        emotion_queue=emotion_queue, num_questions=1,
    )
    start8()
    quiz_ui_q.get()
    select8("all_knowing")
    quiz_ui_q.get()
    submit8("user", "정답0")
    await asyncio.sleep(0.1)  # POST_SPEECH_DRAIN_SEC + REVEAL_HOLD_SEC 합보다 넉넉히 대기
    ok &= check("reveal is withheld while speaking_done stays clear", quiz_ui_q.empty())
    speaking_done_busy.set()  # 로봇이 이제 막 발화를 마쳤다
    await asyncio.sleep(0.1)
    msg = quiz_ui_q.get()
    ok &= check("reveal appears once speaking_done is set",
                msg["type"] == "reveal" and "정답0" in msg["text"])

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
