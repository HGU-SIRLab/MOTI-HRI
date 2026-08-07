"""start_quiz/select_quiz_mode/submit_guess/request_hint/end_quiz_early — Gemini 툴로
core/quiz_state.py의 QuizSession을 노출한다. core/motion_tools.py와 같은 클로저 패턴
(busy 가드 공유, 블로킹 하드웨어 호출은 백그라운드 스레드로 넘김).

짜증유발 모드의 10~12초 스톨+정확한 거절 대사는 이 파일에서만 다룬다 —
core/quiz_state.py는 순수 로직이라 asyncio/모션을 모른다. request_hint() 툴은
Live 세션의 tool_call 처리가 동기적이라 즉시 반환해야 하므로, 실제 지연은
asyncio 태스크로 예약하고 그 태스크가 나중에 `inject_turn()`으로 새 히든 턴을 보낸다.
"""
import asyncio
import os
import random
import threading

from core.quiz_bank import load_question_bank
from core.quiz_state import MODE3_REFUSAL_LINE, VALID_MODES, QuizSession
from hardware.motion import play_express_gesture, play_look_away_motion, play_thinking_stall
from media.voice_shift import POST_SPEECH_DRAIN_SEC

STALL_MIN_SEC = 10.0
STALL_MAX_SEC = 12.0
# 정답 공개 화면(원본 사진)을 다음 문제로 넘기기 전 유지하는 시간 — 로봇이 정답을
# 말하는 동안 참가자가 원본 사진을 볼 시간을 준다. 이 카운트는 로봇이 실제로 그 발화를
# 다 마친 순간부터 시작한다(아래 _delayed_reveal_and_advance 참고) — 툴 호출 시점부터
# 세면 아직 안 끝난 발화 도중에 화면이 넘어가버린다.
REVEAL_HOLD_SEC = 4.0


def make_quiz_tools(quiz_ui_q, busy: threading.Event, motion_ctx, inject_turn, loop,
                     speaking_done: asyncio.Event, emotion_queue=None, num_questions: int = 5):
    """motion_ctx = (port, pkt, lock, shared_state, home_pan, home_tilt) — launcher.py가
    core.motion_tools.make_motion_tools에 넘기는 것과 같은 튜플. busy도 그쪽과 같은
    threading.Event를 공유해야 퀴즈 리액션 모션과 LLM이 부르는 제스처가 같은 모터를
    동시에 건드리지 않는다. inject_turn(text)는 launcher.py가 만드는 코루틴으로,
    session.send_client_content(...)를 호출해 새 히든 턴을 보낸다. loop는 이 코루틴이
    도는 asyncio 이벤트 루프(asyncio.get_event_loop()) — request_hint의 지연 태스크
    예약에 쓰인다. speaking_done은 launcher.py가 recv_loop에서 갱신하는 이벤트로,
    로봇이 지금 발화 중이 아닐 때만 set되어 있다(inject_turn()이 이미 이걸로 겹쳐
    말하기를 막고 있음) — 정답 공개 타이밍도 같은 신호를 재사용한다.
    """
    port, pkt, lock, shared_state, home_pan, home_tilt = motion_ctx
    # 크래시 복구용(docs/experiment_design.md §1-1): 세션이 도중에 죽어 launcher를
    # 재시작해야 할 때, 참가자가 이미 마친 라운드 수를 .env의 QUIZ_ROUND_OFFSET으로
    # 지정하면 이미 정답이 공개된 사진 슬라이스를 건너뛴다(예: 1라운드 완료 후 크래시
    # -> QUIZ_ROUND_OFFSET=1로 재시작하면 다음 라운드가 6번째 사진부터 배분됨).
    round_offset = int(os.getenv("QUIZ_ROUND_OFFSET", "0"))
    if round_offset:
        print(f"ℹ️ QUIZ_ROUND_OFFSET={round_offset} — 문제 은행을 {round_offset}라운드만큼 "
              f"건너뛰고 시작합니다(크래시 복구 모드). 실험이 끝나면 .env에서 지우세요!")
    session = QuizSession(load_question_bank(), num_questions=num_questions,
                           initial_round_offset=round_offset)
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
            # "문제당 소요시간" 지표(docs/experiment_design.md §5)의 시작점 — 화면에
            # 사진이 실제로 나가는 이 지점이 유일한 push 경로라 여기서 한 번만 찍는다.
            session.mark_question_shown()
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

    async def _delayed_reveal_and_advance(question):
        # 툴 호출 시점(예: submit_guess(robot, ...))엔 로봇이 아직 직전 문장("제 생각엔
        # 이거 같아요! 비교해볼까요?")을 말하는 도중이거나, 이 호출의 응답으로 받은
        # 정답 공개/반응 대사를 이제 막 말하기 시작하려는 참이다 — 곧장 화면을 바꾸면
        # 로봇이 입을 열기도 전에 정답 사진이 뜨고, REVEAL_HOLD_SEC이 그 시점부터
        # 흘러버려서 로봇이 정답을 다 말하기도 전에 다음 문제로 넘어가버렸다(2026-07-31
        # 실사용 피드백). inject_turn()과 같은 speaking_done 신호로 이 턴의 발화가 실제로
        # 끝나길 기다린 뒤에야 화면을 바꾸고, 그 시점부터 REVEAL_HOLD_SEC을 센다.
        await speaking_done.wait()
        await asyncio.sleep(POST_SPEECH_DRAIN_SEC)
        _push_reveal(question)
        await asyncio.sleep(REVEAL_HOLD_SEC)
        _pending_reveal_transition["task"] = None
        _push_question_or_hide()
        # 화면이 실제로 다음 문제로 바뀐 이 순간에만 모델에게 물어보라고 알린다 —
        # session.resolve_*_guess()가 곧장 돌려주면 아직 이전 문제 reveal 화면인 채로
        # 모델이 먼저 물어봐서 말/화면이 어긋나는 문제(2026-07-30)가 있었다.
        if session.active and session.current_question is not None:
            await inject_turn(session.next_question_prompt())

    def _schedule_reveal(question):
        _cancel_pending_reveal_transition()
        _pending_reveal_transition["task"] = loop.create_task(_delayed_reveal_and_advance(question))

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
        if session.mode != "annoying" or session.current_question is None:
            return
        # 2026-07-31 재설계(3차): 거절만 하고 멈춘다 — 정답을 공개하지도, 전진하지도
        # 않는다. 참가자가 명시적으로 포기/스킵을 요청할 때만(judge_guess의 is_dont_know)
        # submit_guess()가 곧장(뜸들임 없이) 정답을 공개하고 전진시킨다(core/quiz_state.py
        # 참고) — 여기서 자동으로 이어붙이면 "거절해놓고 몇 초 뒤 바로 정답을 말해서
        # 사실 알고 있었던 것"처럼 보인다는 지적으로 되돌림.
        await inject_turn(
            "이제 정확히 이 문장만 그대로 말하세요(단어 하나도 바꾸거나 덧붙이지 마세요): "
            f"\"{MODE3_REFUSAL_LINE}\""
        )
        # 거절이 실제로 발화 주입된 뒤에만 센다 — 예약만 되고 취소된 스톨은 참가자가
        # 겪은 적이 없으므로 "포기까지 겪은 거절 횟수" 지표에 포함되면 안 된다.
        session.note_refusal_delivered()

    def _schedule_stall_refusal():
        """이미 대기 중인 지연 주입이 있으면 또 예약하지 않는다 — 참가자가 대기 중
        계속 말할 때마다(추가 오답, 재차 힌트 요청 등) 매번 취소하고 다시 예약하면
        타이머가 영원히 리셋되어 절대 끝나지 않는 사고가 난다(2026-07-31 실물 테스트로
        발견)."""
        existing = _pending_stall["task"]
        if existing is not None and not existing.done():
            return
        _pending_stall["task"] = loop.create_task(_delayed_refusal())

    def _cancel_pending_stall():
        """질문이 넘어가거나(포기/스킵으로 정답 공개), 모드가 바뀌거나, 퀴즈가 끝나면
        그 이전 문제의 지연된 거절 대사가 엉뚱한 타이밍에 튀어나오지 않도록 취소한다."""
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
        # 이미 진행한 모드 재선택이면 choose_mode가 상태를 바꾸지 않고 재확인 요청만
        # 돌려준다(2026-08-07) — 그때 화면을 건드리면 규칙 안내가 hide로 지워지므로,
        # 모드가 실제로 확정된 경우에만 첫 문제를 push한다.
        if session.mode == mode:
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
                if len(session.results) > results_count_before:
                    # 포기/스킵 요청으로 그 자리에서 곧장 해결됨(core/quiz_state.py가
                    # 뜸들임 없이 기록/전진까지 마쳤다) — 남아있던 거절 스톨은 이제
                    # 무의미하니 취소한다(다음 문제에 엉뚱한 타이밍으로 튀어나오면 안 됨).
                    _cancel_pending_stall()
                else:
                    # 실제 답 시도(맞았든 틀렸든) — 매번 거절만 한다. 이미 스톨이 대기
                    # 중이면 재예약하지 않는다(계속 말할 때마다 타이머가 리셋되는 사고 방지).
                    _run_guarded(play_thinking_stall, port, pkt, lock, shared_state, emotion_queue)
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
            _schedule_reveal(question_before_call)

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
