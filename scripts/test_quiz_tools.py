"""core/quiz_tools.py의 Gemini 툴 배선을 검증한다 — 실제 하드웨어/Live API 없이,
모션 함수들을 목(mock)으로 교체하고 가짜 inject_turn으로 짜증유발 모드의 지연 주입과
라운드 자동 전환(45단계)까지 확인한다. API 키/로봇 불필요.

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


def _make_temp_bank(tmpdir, n=2):
    path = os.path.join(tmpdir, "questions.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([
            {"id": f"q{i}", "image_path": f"q{i}.jpg", "answer": f"정답{i}", "alternates": []}
            for i in range(n)
        ], f)
    return path


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


async def _tick_turn_seq(turn_seq: list, interval=0.02):
    """실제로는 launcher.py의 recv_loop가 로봇의 턴이 끝날 때마다(turn_complete 또는
    interrupted) turn_seq[0]을 올린다(2026-08-08 도입 — core/quiz_tools.py의
    _wait_for_turn_after 참고, 정답 공개가 로봇이 말하기도 전에 뜨던 사고의 재발 방지용
    구조 변경). 이 테스트는 실제 모델 응답을 시뮬레이션하지 않으므로, 대신 일정 간격으로
    계속 올려서 "턴은 결국 끝난다"는 흐름만 흉내낸다 — 정확한 턴 경계 타이밍이 아니라
    _wait_for_turn_after()가 실제로 기다렸다가 진행하는지가 이 테스트의 관심사다."""
    while True:
        await asyncio.sleep(interval)
        turn_seq[0] += 1


async def main():
    ok = True
    tmpdir = tempfile.mkdtemp()
    qt.load_question_bank = lambda: load_question_bank(_make_temp_bank(tmpdir, 6))

    quiz_ui_q = Queue()
    busy = threading.Event()
    motion_ctx = (None, None, threading.Lock(), {"mode": "tracking"}, 2081, 2071)
    emotion_calls = []
    emotion_queue = type("FakeQueue", (), {"put": lambda self, x: emotion_calls.append(x)})()
    injected = []

    async def fake_inject_turn(text):
        injected.append(text)

    loop = asyncio.get_event_loop()
    # 실제로는 launcher.py의 recv_loop가 관리하는 1칸짜리 리스트 — 턴이 끝날 때마다
    # 올라간다. 이 ticker가 계속 돌면서 "턴은 결국 끝난다"를 흉내낸다(POST_SPEECH_DRAIN_SEC을
    # 짧게 패치해둔 덕에 대기 시간도 작다).
    turn_seq = [0]
    ticker_task = asyncio.create_task(_tick_turn_seq(turn_seq))

    def make_tools(num_questions, modes, ts=None, **kw):
        return qt.make_quiz_tools(
            quiz_ui_q, busy, motion_ctx, fake_inject_turn, loop, ts or turn_seq,
            emotion_queue=emotion_queue, num_questions=num_questions, mode_order=modes, **kw)

    async def collect_until(msg_type, timeout=3.0):
        """quiz_ui_q에 msg_type 메시지가 나타날 때까지 모아서 돌려준다(타임아웃되면 그때까지 모은 것)."""
        deadline = time.monotonic() + timeout
        msgs = []
        while time.monotonic() < deadline:
            while not quiz_ui_q.empty():
                m = quiz_ui_q.get()
                msgs.append(m)
                if m.get("type") == msg_type:
                    return msgs
            await asyncio.sleep(0.01)
        return msgs

    async def drain_ui(settle=0.4):
        """백그라운드 전환 태스크가 남긴 메시지까지 다 흘려보낸 뒤 큐를 비운다."""
        await asyncio.sleep(settle)
        while not quiz_ui_q.empty():
            quiz_ui_q.get()

    # ---------------------------------------------------------------------
    # 1. start_quiz -> (전체 안내 발화) -> 첫 라운드 자동 시작 -> 첫 문제
    #    45단계(2026-08-10, 교수님 지시): 참가자는 모드를 고르지 않는다. 진행자가 .env로
    #    정한 순서대로 라운드가 자동으로 이어진다.
    # ---------------------------------------------------------------------
    start_quiz, submit_guess, request_hint, end_quiz_early, session = make_tools(2, ["imperfect", "all_knowing"])

    intro = start_quiz()
    ok &= check("start_quiz returns the whole-quiz briefing (not a mode question)",
                "총 4문제" in intro and "묻지 말고" in intro)
    msgs = await collect_until("question")
    types = [m["type"] for m in msgs]
    ok &= check(f"start_quiz pushes rules, then the round label, then the first question ({types})",
                types[:3] == ["rules", "rules", "question"])
    ok &= check("the round label names the assigned first mode", "2번 하찮미" in msgs[1]["text"])
    # core/quiz_bank.py가 상대경로 image_path를 저장소 루트 기준 절대경로로 풀어주므로
    # (CWD에 의존하지 않게 하는 의도된 동작) 파일명만 확인한다.
    ok &= check("the first question photo is pushed",
                msgs[2]["image_path"].replace("\\", "/").endswith("q0.jpg"))
    ok &= check("the mode intro was injected as a hidden turn before the photo",
                any("하찮미" in t and "미리 묻지 마세요" in t for t in injected))
    ok &= check("and the question prompt comes only after the photo",
                any("첫 문제(1/2)" in t for t in injected))
    ok &= check("the round actually runs in the assigned mode", session.mode == "imperfect")

    # 진행 중에 start_quiz를 또 부르면 거절하되 빠져나갈 길을 알려준다.
    r = start_quiz()
    ok &= check("re-calling start_quiz mid-quiz is refused with an escape hatch",
                "이미 퀴즈가 진행 중" in r and "end_quiz_early" in r)

    submit_guess("user", "모르겠어요")  # 포기 신호 -> "저도 맞춰볼게요" 이벤트로 감
    submit_guess("robot", "정답0")  # 로봇이 우연히 맞춤
    ok &= check("robot-correct triggers EXCITED emotion", emotion_calls[-1] == "EXCITED")
    # asyncio.sleep이어야 한다 — reveal push는 turn_seq를 기다리는 태스크 안에서
    # 일어나므로, 이벤트 루프에 제어권을 넘기지 않는 time.sleep으론 그 태스크가
    # 진행되지 않는다.
    await asyncio.sleep(0.1)
    ok &= check("robot-correct triggers proud arm motions", ("express", "right_arm") in calls and ("express", "left_arm") in calls)
    msgs = await collect_until("reveal")
    ok &= check("reveals original photo + answer before advancing",
                msgs[-1]["type"] == "reveal" and "정답0" in msgs[-1]["text"]
                and msgs[-1]["image_path"].replace("\\", "/").endswith("q0.jpg"))
    ok &= check("deferred reveal speech (robot correct on give-up path) was injected as a hidden turn",
                any("뿌듯" in t for t in injected))
    msgs = await collect_until("question")
    ok &= check("advances to the second question after the reveal hold",
                msgs[-1]["image_path"].replace("\\", "/").endswith("q1.jpg"))

    # ---------------------------------------------------------------------
    # 2. 라운드 자동 전환 — 마지막 문제가 끝나면 다음 모드 안내 후 새 문제 세트로 이어진다.
    # ---------------------------------------------------------------------
    injected.clear()
    submit_guess("user", "정답 알려줘")
    submit_guess("robot", "틀린답")  # 로봇도 틀림 -> 1라운드(2문제) 소진
    await asyncio.sleep(0.1)
    ok &= check("robot-wrong triggers look-away motion", ("look_away",) in calls)
    ok &= check("the quiz stays active between rounds (no idle-sleep window)",
                session.active and session.round_finished)
    msgs = await collect_until("question", timeout=4.0)
    types = [m["type"] for m in msgs]
    ok &= check(f"round 2 starts automatically: reveal -> round label -> next question ({types})",
                "reveal" in types and types[-1] == "question" and types[-2] == "rules")
    ok &= check("the round-2 label names the next assigned mode", "1번 척척박사" in msgs[-2]["text"])
    ok &= check("round 2 mode is the second entry of the assigned order", session.mode == "all_knowing")
    ok &= check("round 2 announces the mode change out loud",
                any("모드가 바뀝니다" in t for t in injected))
    ok &= check("round 2 uses a fresh photo set (no repeat of round 1)",
                msgs[-1]["image_path"].replace("\\", "/").endswith("q2.jpg"))

    # 배정된 라운드를 전부 마치면 화면이 정리되고 마무리 인사만 남는다.
    injected.clear()
    submit_guess("user", "정답2")
    msgs = await collect_until("question", timeout=4.0)
    ok &= check("the final round advances to its last question",
                msgs and msgs[-1]["image_path"].replace("\\", "/").endswith("q3.jpg"))
    submit_guess("user", "정답3")
    msgs = await collect_until("hide", timeout=4.0)
    ok &= check(f"after the final round the screen hides ({[m['type'] for m in msgs]})",
                msgs and msgs[-1]["type"] == "hide")
    ok &= check("the wrap-up mentions the grand total", any("4문제가 모두 끝났습니다" in t for t in injected))
    ok &= check("the session is finished", not session.active)

    r = start_quiz()
    ok &= check("restarting after all assigned rounds are done is refused",
                "이미 마쳤습니다" in r)

    # ---------------------------------------------------------------------
    # 3. 하찮미 — 실제 답 시도도 즉시 채점하지 않고 로봇 추측과 나란히 비교(2026-07-31)
    # ---------------------------------------------------------------------
    await drain_ui()
    start1b, submit1b, hint1b, end1b, sess1b = make_tools(2, ["imperfect"])
    start1b()
    await collect_until("question")

    r = submit1b("user", "정답0")
    ok &= check("imperfect real guess triggers the kickoff event, no immediate verdict",
                "맞혔습니다" not in r and "submit_guess" in r)
    ok &= check("imperfect real guess does not advance yet", sess1b.index == 0)
    ok &= check("imperfect real guess pushes nothing to the UI yet", quiz_ui_q.empty())

    r = submit1b("robot", "정답0")  # 로봇도 우연히 맞춤 -> 둘 다 맞음, 이제야 전진
    ok &= check("paired guess advances the session", sess1b.index == 1)
    msgs = await collect_until("reveal")
    ok &= check("imperfect paired guess pushes reveal", msgs[-1]["type"] == "reveal" and "정답0" in msgs[-1]["text"])
    msgs = await collect_until("question")
    ok &= check("imperfect paired guess advances UI to next question",
                msgs[-1]["image_path"].replace("\\", "/").endswith("q1.jpg"))

    # ---------------------------------------------------------------------
    # 4. 짜증유발 모드 힌트 스톨 + 지연 주입 — 2026-07-31 재설계(3차): 거절만 하고 멈춘다.
    # ---------------------------------------------------------------------
    await drain_ui()
    injected.clear()
    start2, submit2, hint2, end2, sess2 = make_tools(1, ["annoying"])
    start2()
    await collect_until("question")
    filler = hint2()
    ok &= check("annoying hint returns filler only, no refusal line yet", qt.MODE3_REFUSAL_LINE not in filler)
    await asyncio.sleep(0.2)
    ok &= check("delayed injection eventually fires the exact refusal line",
                any(qt.MODE3_REFUSAL_LINE in t for t in injected))
    ok &= check("annoying hint triggers thinking-stall motion", ("thinking_stall",) in calls)
    ok &= check("refusal alone never advances or pushes anything to the UI",
                sess2.index == 0 and len(sess2.results) == 0 and quiz_ui_q.empty())

    # 힌트를 짧은 간격으로 두 번 요청해도 지연 주입은 한 번만 걸려야 한다(중복 발화 방지).
    injected.clear()
    start3, submit3, hint3, end3, sess3 = make_tools(1, ["annoying"])
    start3()
    await collect_until("question")
    injected.clear()
    hint3()
    hint3()  # 곧바로 또 요청 — 새 태스크가 중복 예약되면 안 됨
    await asyncio.sleep(0.2)
    ok &= check("duplicate hint requests only inject the refusal once",
                sum(1 for t in injected if qt.MODE3_REFUSAL_LINE in t) == 1)

    # 짜증유발 모드에서 실제 답 제출 — 오답이든 정답이든 매번 거절만 하고 절대 진행하지
    # 않는다. 명시적으로 포기/스킵을 요청할 때만 그 자리에서 곧장 정답이 공개되고 전진한다.
    await drain_ui()
    injected.clear()
    calls.clear()
    emotion_calls.clear()
    startA, submitA, hintA, endA, sessA = make_tools(2, ["annoying"])
    startA()
    await collect_until("question")
    injected.clear()
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
    ok &= check("an explicit give-up request keeps the immediate response silent (no answer leak)",
                "정답0" not in r and "화면에 적힌" not in r)
    # 포기로 정답을 공개할 때도 THINKING이 남지 않고 무표정으로 돌아온다 — 이건
    # submit_guess() 래퍼가 판정 직후 동기적으로 처리하므로 sleep 없이도 바로 확인된다.
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
    msgs = await collect_until("reveal")
    ok &= check("give-up reveal pushes the original photo + answer",
                msgs[-1]["type"] == "reveal" and "정답0" in msgs[-1]["text"])
    ok &= check(
        "deferred reveal speech frames it as reading the screen, not the robot's own knowledge",
        any("정답0" in t and "화면에 적힌" in t for t in injected),
    )
    msgs = await collect_until("question")
    ok &= check("give-up reveal advances UI to the next question",
                msgs[-1]["image_path"].replace("\\", "/").endswith("q1.jpg"))
    ok &= check("no stray refusal was injected for the give-up request itself",
                not any(t == qt.MODE3_REFUSAL_LINE for t in injected))

    # 회귀 방지: 오답을 방금 제출해 거절 스톨이 아직 대기 중인 상태에서, 곧바로 포기/스킵을
    # 요청하면 그 스톨은 취소되고 즉시 정답이 공개돼야 한다 — 안 그러면 이미 다음 문제로
    # 넘어갔는데 뒤늦게 이전 문제의 거절 대사가 튀어나오는 사고가 난다.
    await drain_ui()
    injected.clear()
    startB, submitB, hintB, endB, sessB = make_tools(1, ["annoying"])
    startB()
    await collect_until("question")
    injected.clear()
    submitB("user", "땡땡땡")  # 거절 스톨 예약(아직 안 걸림)
    submitB("user", "그냥 공개해줘")  # 곧바로 포기 -> 스톨은 취소되고 즉시 정답 공개
    ok &= check(
        "an immediate give-up cancels the pending refusal stall and resolves right away",
        sessB.index == 1 and len(sessB.results) == 1,
    )
    # 예약만 되고 취소된 스톨은 참가자가 실제로 겪은 거절이 아니다 — 0으로 남아야 한다.
    ok &= check("a cancelled (never-delivered) refusal is not counted",
                sessB.results[0].annoying_refusals == 0)
    msgs = await collect_until("hide")
    ok &= check("give-up advances UI to hide (last question of the only round)",
                msgs[-1]["type"] == "hide")
    ok &= check("the cancelled stall never injects the refusal line",
                not any(qt.MODE3_REFUSAL_LINE in t for t in injected))

    # 힌트 요청(스톨 예약) 직후 바로 포기/스킵을 요청해도 마찬가지로 스톨이 취소되고
    # 즉시 정답이 공개돼야 한다.
    await drain_ui()
    injected.clear()
    start4, submit4, hint4, end4, sess4 = make_tools(2, ["annoying"])
    start4()
    await collect_until("question")
    injected.clear()
    hint4()  # 힌트 거절 스톨 예약
    submit4("user", "정답 알려줘")  # 곧바로 포기 요청 -> 스톨 취소, 즉시 정답 공개
    ok &= check("give-up right after a hint request resolves immediately",
                sess4.index == 1 and len(sess4.results) == 1)
    msgs = await collect_until("question")
    ok &= check("advances UI to the next question",
                msgs[-1]["image_path"].replace("\\", "/").endswith("q1.jpg"))
    ok &= check("the cancelled hint stall never injects the refusal line",
                not any(qt.MODE3_REFUSAL_LINE in t for t in injected))

    # ---------------------------------------------------------------------
    # 5. 무효 호출 방어 — 아무것도 기록/전진하지 않은 호출에 reveal이 뜨면 안 된다.
    # ---------------------------------------------------------------------
    await drain_ui()
    start6, submit6, hint6, end6, sess6 = make_tools(2, ["imperfect"])
    start6()
    await collect_until("question")
    r = submit6("robot", "아무거나")  # 사용자 차례 없이 곧바로 로봇 추측(비정상 순서)
    ok &= check("invalid robot guess without a pending user guess is ignored", "무시" in r)
    ok &= check("no spurious reveal is pushed for a no-op resolve", quiz_ui_q.empty())

    # 퀴즈가 이미 자연 종료된 뒤에 다시 답을 제출해도(비정상) reveal이 뜨면 안 된다.
    await drain_ui()
    start7, submit7, hint7, end7, sess7 = make_tools(1, ["all_knowing"])
    start7()
    await collect_until("question")
    submit7("user", "정답0")  # 유일한 문제를 답변 -> 퀴즈 자연 종료
    await collect_until("hide")
    ok &= check("queue drained after quiz naturally ends", quiz_ui_q.empty())
    submit7("user", "아무말")  # 퀴즈가 이미 끝난 뒤 비정상적으로 재제출
    await asyncio.sleep(0.2)
    ok &= check("submitting after the quiz already ended pushes nothing", quiz_ui_q.empty())

    # ---------------------------------------------------------------------
    # 6. 회귀 방지(2026-07-31, 2026-08-08 turn_seq로 재작성): 판정 툴 호출을 포함한 턴이
    #    아직 안 끝났으면(로봇이 여전히 뭔가 말하는 중이면) 정답 공개 화면이 그동안
    #    미뤄져야 한다. turn_seq_stalled는 일부러 ticker를 안 붙여서 "턴이 하나도 안 끝난"
    #    상태를 고정 — _wait_for_turn_after()가 실제로 대기하는지 직접 검증한다.
    # ---------------------------------------------------------------------
    await drain_ui()
    turn_seq_stalled = [0]
    start8, submit8, hint8, end8, sess8 = make_tools(1, ["all_knowing"], ts=turn_seq_stalled)
    start8()
    await asyncio.sleep(0.1)
    ok &= check("the round does not even begin while the briefing turn is still going",
                quiz_ui_q.qsize() == 1)  # start_quiz의 rules만 들어있어야 한다
    turn_seq_stalled[0] += 1   # 전체 안내 턴 종료
    await asyncio.sleep(0.1)
    turn_seq_stalled[0] += 1   # 모드 안내 턴 종료
    msgs = await collect_until("question")
    ok &= check("the first question appears only after both intro turns finished",
                msgs[-1]["type"] == "question")

    submit8("user", "정답0")
    await asyncio.sleep(0.1)  # POST_SPEECH_DRAIN_SEC + REVEAL_HOLD_SEC 합보다 넉넉히 대기
    ok &= check("reveal is withheld while the judging turn hasn't completed yet", quiz_ui_q.empty())
    turn_seq_stalled[0] += 1  # 침묵 지시를 받은 판정 턴이 이제 막 끝났다고 흉내낸다
    await asyncio.sleep(0.1)
    msgs = await collect_until("reveal")
    ok &= check("reveal appears once the judging turn completes",
                msgs[-1]["type"] == "reveal" and "정답0" in msgs[-1]["text"])
    ok &= check("deferred reveal speech (all_knowing correct) was injected as a hidden turn",
                any("맞혔습니다" in t for t in injected))
    # _delayed_reveal_and_advance는 reveal 이미지를 띄운 뒤 반응 텍스트를 또 다른 히든
    # 턴으로 주입하고, REVEAL_HOLD_SEC을 세기 전에 "그 턴도" 끝나길 기다린다 — 이것도
    # 마저 흉내내지 않으면 백그라운드 태스크가 영원히 멈춰있게 된다.
    turn_seq_stalled[0] += 1

    # ---------------------------------------------------------------------
    # 7. 40단계(2026-08-10) 실물 로그에서 나온 회귀 방지
    # ---------------------------------------------------------------------
    # (1) 유령 submit_guess: 사용자가 아무 말도 안 했는데 모델이 guess_text="사용자의 말을
    # 기다리는 중" 같은 걸로 판정을 요청해, 참가자가 보지도 못한 문항이 오답으로 소모되던
    # 사고(실물 로그에서 5문항 중 2문항 손실). user_spoke 신호가 없으면 판정 자체를 거부한다.
    await drain_ui()
    user_spoke = [False]
    mute_speech = [False]
    start9, submit9, hint9, end9, sess9 = make_tools(
        2, ["all_knowing"], mute_speech=mute_speech, user_spoke=user_spoke)
    start9()
    # 새 사진이 뜨기 전에 한 말은 그 문제의 답이 될 수 없다 — 사진을 push하는 경로
    # (_push_question_or_hide)가 신호를 버려야 한다. 실물에서 참가자가 로봇이 조용하니
    # 이전 문제의 답을 다시 말했고, 그게 다음 문항의 답으로 채점돼 문항을 잃었다.
    user_spoke[0] = True
    await collect_until("question")
    ok &= check("showing a new question discards speech from before it appeared",
                user_spoke[0] is False)

    before = len(sess9.results)
    r = submit9("user", "사용자의 말을 기다리는 중")
    ok &= check("phantom submit_guess (user never spoke) is refused",
                "아직" in r and len(sess9.results) == before)
    ok &= check("phantom submit_guess consumes no question", sess9.current_question is not None)
    ok &= check("phantom submit_guess leaves speech unmuted", mute_speech[0] is False)

    # 사용자가 실제로 말하면(launcher.py가 input_transcription에서 올려줌) 정상 판정된다.
    user_spoke[0] = True
    r = submit9("user", "정답0")
    ok &= check("real guess is judged once the user actually spoke", len(sess9.results) == before + 1)
    # (2) 판정 턴 음소거: 침묵 지시를 어기고 나오는 오디오를 파이썬이 실제로 막는다.
    ok &= check("judging turn mutes playback until the turn ends", mute_speech[0] is True)
    ok &= check("user_spoke is consumed after a successful judgement", user_spoke[0] is False)

    r = submit9("user", "정답1")
    ok &= check("the same utterance cannot be judged twice", "아직" in r)

    await asyncio.sleep(0.15)  # ticker가 turn_seq를 올려 판정 턴이 끝났다고 흉내낸다
    ok &= check("playback unmutes automatically once the judging turn ends", mute_speech[0] is False)

    # (3) 정답 반응 히든 턴에는 "다음 문제를 미리 묻지 말라"는 제약이 항상 함께 간다 —
    # 이게 없으면 모델이 반응에 이어 "이 물건은 무엇일까요?"까지 말해버려, 아직 뜨지도 않은
    # 문제를 질문하고 잠시 뒤 같은 질문이 한 번 더 나온다(실물 제보의 원인).
    ok &= check("reveal speech carries the do-not-ask-next-question constraint",
                any("미리 묻지 마세요" in t for t in injected))

    # (4) 41단계 후속(2026-08-10 하찮미 실물): 로봇이 자기 추측을 말해놓고
    # submit_guess(speaker="robot")를 호출하지 않으면 그 문제에서 퀴즈가 영영 멈췄다.
    # 이제 한 번 재촉하고, 그래도 안 오면 사용자 답만으로 채점하고 전진해야 한다.
    qt.ROBOT_GUESS_GRACE_SEC = 0.05
    await drain_ui(0.6)
    startA2, submitA2, hintA2, endA2, sessA2 = make_tools(2, ["imperfect"])
    startA2()
    await collect_until("question")
    injected.clear()
    submitA2("user", "빵")  # 하찮미: 로봇 추측을 기다리는 상태로 staging됨
    ok &= check("imperfect stages a pending robot guess", sessA2.pending_user_guess is not None)
    await asyncio.sleep(0.25)
    ok &= check("a missing robot guess gets nudged once",
                any("submit_guess" in t and "robot" in t for t in injected))
    await asyncio.sleep(0.45)
    ok &= check("a never-arriving robot guess no longer freezes the quiz",
                len(sessA2.results) == 1 and sessA2.pending_user_guess is None)
    ok &= check("the recovered result keeps the user's answer but records no robot guess",
                sessA2.results[0].user_guess_text == "빵"
                and sessA2.results[0].robot_guess_text is None)
    msgs = await collect_until("reveal")
    ok &= check("the recovered question still reveals its answer on screen", msgs[-1]["type"] == "reveal")

    # 정상 경로(로봇이 제때 호출)는 감시가 조용히 물러나야 한다 — 억지 채점이 끼어들면 안 됨.
    await drain_ui()
    startA3, submitA3, hintA3, endA3, sessA3 = make_tools(2, ["imperfect"])
    startA3()
    await collect_until("question")
    submitA3("user", "빵")
    submitA3("robot", "정답0")
    await asyncio.sleep(0.5)
    ok &= check("a timely robot guess is recorded normally (watchdog stays out of the way)",
                len(sessA3.results) == 1 and sessA3.results[0].robot_guess_text == "정답0")

    # (5) 조기 종료 후에도 남은 라운드는 복구 가능해야 한다 — 모델이 end_quiz_early를
    # 실수로 부르는 사고가 나도 그 참가자 세션이 통째로 죽으면 안 된다(44단계의 교훈).
    await drain_ui()
    startC, submitC, hintC, endC, sessC = make_tools(2, ["all_knowing", "imperfect", "annoying"])
    startC()
    await collect_until("question")
    endC()
    ok &= check("end_quiz_early hides the screen and stops the round",
                not sessC.active)
    await drain_ui()
    r = startC()
    ok &= check("the quiz can be restarted after an early end", "이미 퀴즈가 진행 중" not in r)
    msgs = await collect_until("question")
    ok &= check("the restart resumes with the next assigned mode (not the finished one)",
                sessC.mode == "imperfect")

    # (6) 라운드 전환 중에 퀴즈를 끊으면(end_quiz_early) 다음 라운드가 뒤늦게 살아나면
    # 안 된다 — 전환 흐름은 정답 공개 태스크 안에서 이어지므로, 그 태스크가 취소 가능한
    # 상태로 등록돼 있어야 한다(안 그러면 퀴즈가 끝난 뒤 다음 모드 사진이 화면에 뜬다).
    # 타이밍에 기대지 않도록 턴 카운터를 직접 조작해 "전환이 확실히 진행 중인 순간"을
    # 만든다 — 전환 안내를 주입한 뒤 그 턴이 끝나길 기다리는 지점에서 멈춰 세운다.
    await drain_ui()
    ts_d = [0]
    startD, submitD, hintD, endD, sessD = make_tools(1, ["all_knowing", "imperfect"], ts=ts_d)
    startD()
    ts_d[0] += 1                      # 전체 안내 턴 종료
    await asyncio.sleep(0.05)
    ts_d[0] += 1                      # 1라운드 모드 안내 턴 종료 -> 첫 문제 push
    await collect_until("question")
    submitD("user", "정답0")           # 1라운드(1문항) 소진
    ts_d[0] += 1                      # 판정(침묵) 턴 종료 -> 정답 공개 push + 반응 주입
    await asyncio.sleep(0.05)
    ts_d[0] += 1                      # 정답 반응 턴 종료 -> REVEAL_HOLD 후 라운드 전환 시작
    await asyncio.sleep(0.15)         # 전환 안내가 주입되고 그 턴 종료를 기다리는 지점까지
    ok &= check("the transition to the next round is actually in progress",
                sessD.mode == "imperfect")
    endD()                            # 바로 그 순간 실험자가 중단
    ts_d[0] += 1                      # (전환이 살아있었다면 여기서 다음 문제가 떴을 것)
    await asyncio.sleep(0.3)
    pushed = []
    while not quiz_ui_q.empty():
        pushed.append(quiz_ui_q.get()["type"])
    ok &= check(f"ending mid-transition never pushes the next round's question ({pushed})",
                "question" not in pushed and not sessD.active)

    # (7) 45단계 점검에서 발견: 상태 기계는 이미 다음 문제를 가리키는데 화면은 아직 안
    # 바뀐 구간(정답 공개 유지 중 / 모드 전환 안내 중)에 판정을 요청하면, 참가자가 보지도
    # 못한 문항이 소모된다(40단계에서 실제로 겪은 사고와 같은 종류). user_spoke 리셋은
    # "사진이 뜨는 순간"만 덮으므로 이 구간을 못 막는다 — 화면에 떠 있는 문제 id로 막는다.
    await drain_ui()
    ts_e = [0]
    us_e = [False]
    startE, submitE, hintE, endE, sessE = make_tools(
        2, ["all_knowing", "imperfect"], ts=ts_e, user_spoke=us_e)
    startE()
    ts_e[0] += 1
    await asyncio.sleep(0.05)
    ts_e[0] += 1
    await collect_until("question")

    us_e[0] = True
    submitE("user", "정답0")          # 1번 문제 정상 채점 -> 정답 공개 구간으로 진입
    ok &= check("the first question is judged normally", len(sessE.results) == 1)
    us_e[0] = True                    # 참가자가 정답 공개를 보며 뭔가 더 말함
    before = len(sessE.results)
    r = submitE("user", "아 맞다 이거 알아요")
    ok &= check("a guess during the reveal hold cannot consume the unseen next question",
                "아직 화면에 뜨지 않았습니다" in r and len(sessE.results) == before
                and sessE.current_question is not None)
    # 사진이 실제로 뜨면 그때부터는 정상 판정된다.
    ts_e[0] += 1
    await asyncio.sleep(0.05)
    ts_e[0] += 1
    await collect_until("question")
    us_e[0] = True
    submitE("user", "정답1")
    ok &= check("once the photo is actually on screen the guess is judged again",
                len(sessE.results) == before + 1)

    # 모드 전환 안내를 말하는 동안에도 마찬가지로 막혀야 한다(라운드 1이 방금 끝났고
    # 라운드 2의 첫 문제는 아직 화면에 없다).
    ts_e[0] += 1
    await asyncio.sleep(0.05)
    ts_e[0] += 1                      # 정답 반응 턴 종료 -> REVEAL_HOLD -> 라운드 전환 시작
    await asyncio.sleep(0.15)
    ok &= check("the next round has started in the state machine", sessE.mode == "imperfect")
    us_e[0] = True
    before = len(sessE.results)
    r = submitE("user", "이번엔 뭐지")
    ok &= check("a guess during the mode-change announcement is refused too",
                "아직 화면에 뜨지 않았습니다" in r and len(sessE.results) == before)

    ticker_task.cancel()

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
