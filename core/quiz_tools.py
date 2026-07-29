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
    _pending_stall = {"task": None}
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
        await inject_turn(
            "이제 정확히 이 문장만 그대로 말하세요(단어 하나도 바꾸거나 덧붙이지 마세요): "
            f"\"{MODE3_REFUSAL_LINE}\""
        )

    def _schedule_stall_refusal():
        """이미 대기 중인 지연 주입이 있으면 또 예약하지 않는다 — 사용자가 짧은 간격으로
        힌트를 여러 번 요청하면 거절 대사가 여러 번 겹쳐 발화되는 사고를 막기 위함."""
        existing = _pending_stall["task"]
        if existing is not None and not existing.done():
            return
        _pending_stall["task"] = loop.create_task(_delayed_refusal())

    def _cancel_pending_stall():
        """질문이 넘어가거나(정답 제출), 모드가 바뀌거나, 퀴즈가 끝나면 그 이전 문제의
        지연된 거절 대사가 엉뚱한 타이밍에 튀어나오지 않도록 취소한다."""
        task = _pending_stall["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_stall["task"] = None

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

        was_imperfect_user_turn = speaker == "user" and session.mode == "imperfect"
        if not was_imperfect_user_turn:
            # 이 호출로 문제가 넘어갈 예정이니(하찮미 모드의 사용자 차례만 예외), 이전
            # 문제에서 걸어둔 지연된 거절 대사/정답 공개 전환이 있으면 지금 취소한다.
            _cancel_pending_stall()
            _cancel_pending_reveal_transition()

        # resolve_*_guess가 호출되면 session.current_question이 다음 문제로 넘어가므로,
        # 정답 공개에 쓸 "방금 답한" 문제는 반드시 호출 전에 붙잡아둬야 한다.
        answered_question = session.current_question if not was_imperfect_user_turn else None
        # resolve_*_guess는 상황이 안 맞으면(퀴즈 비활성, pending_user_guess 없음 등) 아무것도
        # 기록/전진하지 않고 안내 문구만 반환할 수 있다 — 그런 무효 호출에도 reveal을 띄우면
        # 아직 안 풀린 문제의 "정답 공개" 화면이 잘못 뜨는 사고가 난다. 실제로 결과가
        # 기록됐을 때만(진짜로 전진했을 때만) reveal을 띄운다.
        results_count_before = len(session.results)

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

        advanced = len(session.results) > results_count_before
        if not was_imperfect_user_turn and advanced:
            _push_reveal(answered_question)
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
