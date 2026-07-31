"""start_quiz/select_quiz_mode/submit_guess/request_hint/end_quiz_early — Gemini 툴로
core/quiz_state.py의 QuizSession을 노출한다. core/motion_tools.py와 같은 클로저 패턴
(busy 가드 공유, 블로킹 하드웨어 호출은 백그라운드 스레드로 넘김).

짜증유발 모드의 10~12초 스톨+정확한 거절 대사는 이 파일에서만 다룬다 —
core/quiz_state.py는 순수 로직이라 asyncio/모션을 모른다. request_hint() 툴은
Live 세션의 tool_call 처리가 동기적이라 즉시 반환해야 하므로, 실제 지연은
asyncio 태스크로 예약하고 그 태스크가 나중에 `inject_turn()`으로 새 히든 턴을 보낸다.
"""
import asyncio
import random
import threading

from core.quiz_bank import load_question_bank
from core.quiz_state import MODE3_REFUSAL_LINE, VALID_MODES, QuizSession
from hardware.motion import play_express_gesture, play_look_away_motion, play_thinking_stall

STALL_MIN_SEC = 10.0
STALL_MAX_SEC = 12.0
# 짜증유발 모드에서 사용자가 실제로 정답을 맞혔을 때 뜸들이는 시간 — 오답 거절
# (STALL_MIN/MAX_SEC)보다 조금 더 길게 잡아서, 맞혀도 곧바로 안 알려주는 짓궂음을
# 강조한다(2026-07-31 사용자 지정).
CORRECT_STALL_MIN_SEC = 10.0
CORRECT_STALL_MAX_SEC = 20.0
# 정답 공개 화면(원본 사진)을 다음 문제로 넘기기 전 유지하는 시간 — 로봇이 정답을
# 말하는 동안 참가자가 원본 사진을 볼 시간을 준다.
REVEAL_HOLD_SEC = 4.0


def make_quiz_tools(quiz_ui_q, busy: threading.Event, motion_ctx, inject_turn, loop,
                     emotion_queue=None, num_questions: int = 5):
    """motion_ctx = (port, pkt, lock, shared_state, home_pan, home_tilt) — launcher.py가
    core.motion_tools.make_motion_tools에 넘기는 것과 같은 튜플. busy도 그쪽과 같은
    threading.Event를 공유해야 퀴즈 리액션 모션과 LLM이 부르는 제스처가 같은 모터를
    동시에 건드리지 않는다. inject_turn(text)는 launcher.py가 만드는 코루틴으로,
    session.send_client_content(...)를 호출해 새 히든 턴을 보낸다. loop는 이 코루틴이
    도는 asyncio 이벤트 루프(asyncio.get_event_loop()) — request_hint의 지연 태스크
    예약에 쓰인다.
    """
    port, pkt, lock, shared_state, home_pan, home_tilt = motion_ctx
    session = QuizSession(load_question_bank(), num_questions=num_questions)
    # asyncio.create_task()가 만든 태스크는 어딘가에서 강하게 참조하고 있지 않으면
    # 이벤트 루프가 약한 참조만 들고 있어서 도중에 가비지 컬렉션될 수 있다(공식 문서
    # 경고) — 지연된 거절 대사가 통째로 사라지는 사고를 막기 위해 여기 붙잡아둔다.
    # "kind"는 "wrong"(거절 예정) 또는 "correct"(정답 확정 예정) — 정답/오답 사이에
    # 마음이 바뀌었을 때만(예: 오답 뒤 정답으로 정정) 취소 후 재스케줄해야 하고, 같은
    # 종류가 반복되면(오답을 계속 말하는 등) 원래 타이머를 그대로 둬야 한다(2026-07-31,
    # 안 그러면 참가자가 대기 중 계속 말할 때마다 타이머가 리셋되어 영원히 결론이 안
    # 나는 사고가 실제로 났었다). 아래 참고.
    _pending_stall = {"task": None, "kind": None}
    # _pending_stall과 같은 이유로(가비지 컬렉션 방지) 붙잡아둔다 — 정답 공개 화면을
    # REVEAL_HOLD_SEC 뒤 다음 문제로 넘기는 지연 태스크.
    _pending_reveal_transition = {"task": None}

    def _push_question_or_hide():
        q = session.current_question
        if q is not None:
            quiz_ui_q.put({
                "type": "question", "index": session.index, "total": session.total_questions,
                "image_path": q.image_path, "prompt": "이 물건은 무엇일까요?",
            })
        else:
            quiz_ui_q.put({"type": "hide"})

    def _push_reveal(question):
        """방금 채점이 끝난 문제의 원본(크롭 전) 사진과 정답 텍스트를 UI에 보여준다.
        원본 사진이 없으면(구형 데이터 등) 확대 사진을 그대로 재사용한다."""
        if question is None:
            return
        quiz_ui_q.put({
            "type": "reveal",
            "image_path": question.full_image_path or question.image_path,
            "text": f"정답: {question.answer}",
        })

    def _cancel_pending_reveal_transition():
        task = _pending_reveal_transition["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_reveal_transition["task"] = None

    async def _delayed_advance():
        await asyncio.sleep(REVEAL_HOLD_SEC)
        _pending_reveal_transition["task"] = None
        _push_question_or_hide()
        # 화면이 실제로 다음 문제로 바뀐 이 순간에만 모델에게 물어보라고 알린다 —
        # session.resolve_*_guess()가 곧장 돌려주면 아직 이전 문제 reveal 화면인 채로
        # 모델이 먼저 물어봐서 말/화면이 어긋나는 문제(2026-07-30)가 있었다.
        if session.active and session.current_question is not None:
            await inject_turn(session.next_question_prompt())

    def _schedule_reveal_transition():
        _cancel_pending_reveal_transition()
        _pending_reveal_transition["task"] = loop.create_task(_delayed_advance())

    def _run_guarded(fn, *args):
        """busy가 이미 set이면(다른 제스처/퀴즈 모션 실행 중) 아무것도 안 하고 스킵한다."""
        if busy.is_set():
            print(f"⚠️ 이미 다른 동작이 실행 중이라 퀴즈 리액션 모션을 건너뜁니다: {fn.__name__}")
            return
        busy.set()

        def _wrapped():
            try:
                fn(*args)
            finally:
                busy.clear()

        threading.Thread(target=_wrapped, daemon=True).start()

    def _play_proud_motion():
        play_express_gesture("right_arm", 1.0, "fast", 1, port, pkt, lock, shared_state)
        play_express_gesture("left_arm", 1.0, "fast", 1, port, pkt, lock, shared_state)

    async def _delayed_refusal():
        await asyncio.sleep(random.uniform(STALL_MIN_SEC, STALL_MAX_SEC))
        _pending_stall["task"] = None
        _pending_stall["kind"] = None
        if session.mode != "annoying" or session.current_question is None:
            return
        question_before = session.current_question
        await inject_turn(
            "이제 정확히 이 문장만 그대로 말하세요(단어 하나도 바꾸거나 덧붙이지 마세요): "
            f"\"{MODE3_REFUSAL_LINE}\""
        )
        # 거절만 하고 끝나면 참가자가 끝내 정답을 못 맞히는 한 다음 문제로 영영 못
        # 넘어가는 사고가 실제로 났다(2026-07-31 실물 테스트) — 짜증나게 거절하되
        # 결국은 정답을 공개하고 전진시키는 것이 이 모드의 실제 의도였다.
        text = session.reveal_annoying_wrong()
        _cancel_pending_reveal_transition()
        _push_reveal(question_before)
        _schedule_reveal_transition()
        await inject_turn(text)

    def _schedule_stall_refusal():
        """이미 대기 중인 지연 주입이 있으면 또 예약하지 않는다 — 참가자가 대기 중
        계속 말할 때마다(추가 오답, 재차 힌트 요청 등) 매번 취소하고 다시 예약하면
        타이머가 영원히 리셋되어 절대 끝나지 않는 사고가 난다(2026-07-31 실물 테스트로
        발견). submit_guess()는 정답으로 마음이 바뀐 경우에만(kind가 달라질 때만)
        먼저 _cancel_pending_stall()을 불러 이 가드를 무력화시킨다(아래 참고)."""
        existing = _pending_stall["task"]
        if existing is not None and not existing.done():
            return
        _pending_stall["kind"] = "wrong"
        _pending_stall["task"] = loop.create_task(_delayed_refusal())

    async def _delayed_correct_confirm():
        await asyncio.sleep(random.uniform(CORRECT_STALL_MIN_SEC, CORRECT_STALL_MAX_SEC))
        _pending_stall["task"] = None
        _pending_stall["kind"] = None
        # 뜸들이는 동안 실험자가 퀴즈를 조기 종료했거나 모드가 바뀌는 등 상태가 변했을
        # 수 있다 — 그런 드문 경우엔 그냥 조용히 아무 것도 안 한다(엉뚱한 히든 턴을
        # 주입하지 않기 위함).
        if session.mode != "annoying" or not session.annoying_pending_correct:
            return
        question_before = session.current_question
        text = session.confirm_annoying_correct()
        _cancel_pending_reveal_transition()
        _push_reveal(question_before)
        _schedule_reveal_transition()
        await inject_turn(text)

    def _schedule_correct_confirm():
        """_schedule_stall_refusal()과 같은 이유로 이미 대기 중이면 재예약하지 않는다."""
        existing = _pending_stall["task"]
        if existing is not None and not existing.done():
            return
        _pending_stall["kind"] = "correct"
        _pending_stall["task"] = loop.create_task(_delayed_correct_confirm())

    def _cancel_pending_stall():
        """질문이 넘어가거나(정답 제출), 모드가 바뀌거나, 퀴즈가 끝나면 그 이전 문제의
        지연된 거절 대사가 엉뚱한 타이밍에 튀어나오지 않도록 취소한다."""
        task = _pending_stall["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_stall["task"] = None
        _pending_stall["kind"] = None

    def start_quiz() -> str:
        """Begin the zoomed-in-photo quiz game.

        Call this when the user expresses boredom or asks to play a quiz/game
        (e.g. "심심해", "퀴즈 풀자", "재밌는 거 하자"). Takes no arguments.
        """
        if session.active:
            return "이미 퀴즈가 진행 중입니다 — 다시 시작하지 마세요."
        _cancel_pending_stall()
        _cancel_pending_reveal_transition()
        text = session.start()
        quiz_ui_q.put({"type": "rules", "text": "부분 확대 사진 퀴즈를 시작합니다!"})
        return text

    def select_quiz_mode(mode: str) -> str:
        """Set which quiz mode to play, right after the user states the mode
        number/name the experimenter assigned them.

        Args:
            mode: exactly one of "all_knowing", "imperfect", "annoying" — map the
                user's spoken words to these: "1번"/"척척박사" -> "all_knowing",
                "2번"/"하찮미" -> "imperfect", "3번"/"짜증유발" -> "annoying".
                If what they said is ambiguous, ask again instead of guessing.
        """
        if mode not in VALID_MODES:
            return f"'{mode}'는 알 수 없는 모드입니다 — all_knowing/imperfect/annoying 중 하나로 다시 호출하세요."
        if session.mode is not None:
            # 모델이 실수로 두 번 호출하면 index가 0으로 리셋돼 같은 문제가 결과 로그에
            # 중복 기록될 위험이 있다(연구 데이터 무결성 문제) — start_quiz()의 재시작
            # 가드와 같은 이유로 재선택을 막는다.
            return "이미 모드가 선택되어 있습니다 — 다시 선택하지 마세요."
        _cancel_pending_stall()
        _cancel_pending_reveal_transition()
        text = session.choose_mode(mode)
        _push_question_or_hide()
        return text

    def submit_guess(speaker: str, guess_text: str) -> str:
        """Report an attempted answer to the current quiz question so Python can judge it.

        Args:
            speaker: "user" when reporting what the person just said. "robot" ONLY
                in imperfect mode, immediately after you (the robot) improvise your
                own guess out loud — you MUST call this a second time with
                speaker="robot" whenever the "user" call's result tells you to.
            guess_text: the guessed object name, as plain text.
        """
        if speaker not in ("user", "robot"):
            return "speaker는 'user' 또는 'robot'이어야 합니다."

        # 2026-07-30까지는 "사용자 차례 + 하찮미 모드"면 무조건 채점을 미루는(staging)
        # 호출이라고 미리 가정하고 여기서 판단했었다. 이제 하찮미 모드의 실제 답 시도는
        # 이벤트 없이 곧장 채점/전진할 수 있어서 그 가정이 깨졌다 — 미리 판단하는 대신
        # resolve_*_guess를 실제로 호출한 뒤 session.results가 늘었는지(advanced)로만
        # 판단한다. "방금 답하던" 문제는 advanced 여부와 무관하게 호출 전 상태를 붙잡아둬야
        # 하므로 미리 저장해둔다(advanced가 False면 그냥 버려짐).
        question_before_call = session.current_question
        results_count_before = len(session.results)
        mode_before = session.mode

        if speaker == "robot":
            text = session.resolve_robot_guess(guess_text)
            if session.results and session.results[-1].robot_guess_text == guess_text:
                robot_correct = session.results[-1].robot_correct
                if robot_correct:
                    if emotion_queue:
                        emotion_queue.put("EXCITED")
                    _run_guarded(_play_proud_motion)
                elif robot_correct is False:
                    _run_guarded(play_look_away_motion, port, pkt, lock, shared_state, home_pan)
        else:
            text = session.resolve_user_guess(guess_text)
            if mode_before == "annoying" and question_before_call is not None:
                # 짜증유발 모드는 이 턴에서 곧장 결과를 알려주지 않는다 — 힌트 거절과
                # 같은 지연 패턴(뜸들이기 후 히든 턴)으로 나중에 알려준다. 마음이 바뀐
                # 경우(예: 오답 뒤 정답으로 정정, 또는 그 반대)에만 이전 스케줄을
                # 취소하고 새로 건다 — 같은 종류가 반복될 때(오답을 계속 말하는 등)
                # 매번 취소/재예약하면 타이머가 영원히 리셋되는 사고가 난다(위 주석 참고).
                _run_guarded(play_thinking_stall, port, pkt, lock, shared_state, emotion_queue)
                desired_kind = "correct" if session.annoying_pending_correct else "wrong"
                if _pending_stall["kind"] not in (None, desired_kind):
                    _cancel_pending_stall()
                if desired_kind == "correct":
                    _schedule_correct_confirm()
                else:
                    _schedule_stall_refusal()

        advanced = len(session.results) > results_count_before
        if advanced:
            # 실제로 채점/전진했을 때만 — 이전 문제에서 걸어둔 지연된 거절 대사/정답 공개
            # 전환이 남아있으면 지금 취소하고, 방금 답한 문제의 정답 공개 화면을 띄운다.
            # resolve_*_guess가 상황이 안 맞으면(퀴즈 비활성, pending_user_guess 없음, 하찮미
            # 포기 신호로 staging만 한 경우 등) 아무것도 기록/전진하지 않을 수 있는데, 그런
            # 무효/staging 호출에 reveal을 띄우면 아직 안 풀린 문제의 "정답 공개" 화면이
            # 잘못 뜨는 사고가 난다.
            _cancel_pending_stall()
            _cancel_pending_reveal_transition()
            _push_reveal(question_before_call)
            _schedule_reveal_transition()

        return text

    def request_hint() -> str:
        """Call this when the user explicitly asks for a hint, or asks you to
        guess the answer for them, during an active quiz question. Takes no arguments.
        """
        mode_before = session.mode
        text = session.request_hint()
        if mode_before == "annoying":
            _run_guarded(play_thinking_stall, port, pkt, lock, shared_state, emotion_queue)
            _schedule_stall_refusal()
        return text

    def end_quiz_early() -> str:
        """End the quiz session early — call only if the experimenter or
        participant explicitly asks to stop before all questions are done.
        Takes no arguments.
        """
        _cancel_pending_stall()
        _cancel_pending_reveal_transition()
        text = session.end_early()
        quiz_ui_q.put({"type": "hide"})
        return text

    return start_quiz, select_quiz_mode, submit_guess, request_hint, end_quiz_early, session
