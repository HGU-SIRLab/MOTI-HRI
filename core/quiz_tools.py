"""start_quiz/submit_guess/request_hint/end_quiz_early — Gemini 툴로 core/quiz_state.py의
QuizSession을 노출한다. core/motion_tools.py와 같은 클로저 패턴(busy 가드 공유,
블로킹 하드웨어 호출은 백그라운드 스레드로 넘김).

2026-08-10(교수님 지시)로 모드 선택 툴(select_quiz_mode)이 사라졌다 — 참가자가 모드를
고르는 게 아니라, 진행자가 .env의 QUIZ_MODE_ORDER로 미리 정한 순서대로 5문제씩 3라운드
(총 15문제)가 한 번에 이어서 진행된다. 라운드 전환은 모델이 아니라 이 파일의
_begin_round_flow()가 통제한다(모델이 툴을 부르는 걸 전제로 상태를 전진시키면 반드시
막다른 길이 생긴다는 40~44단계의 교훈).

짜증유발 모드의 10~12초 스톨+정확한 거절 대사는 이 파일에서만 다룬다 —
core/quiz_state.py는 순수 로직이라 asyncio/모션을 모른다. request_hint() 툴은
Live 세션의 tool_call 처리가 동기적이라 즉시 반환해야 하므로, 실제 지연은
asyncio 태스크로 예약하고 그 태스크가 나중에 `inject_turn()`으로 새 히든 턴을 보낸다.
"""
from __future__ import annotations
import asyncio
import os
import random
import threading

from core.quiz_bank import load_question_bank
from core.quiz_state import MODE3_REFUSAL_LINE, QuizSession, mode_label, parse_mode_order
from hardware.motion import play_express_gesture, play_look_away_motion, play_thinking_stall
from media.voice_shift import POST_SPEECH_DRAIN_SEC

STALL_MIN_SEC = 10.0
STALL_MAX_SEC = 12.0
# 정답 공개 화면(원본 사진)을 다음 문제로 넘기기 전 유지하는 시간 — 로봇이 정답을
# 말하는 동안 참가자가 원본 사진을 볼 시간을 준다. 이 카운트는 로봇이 실제로 그 발화를
# 다 마친 순간부터 시작한다(아래 _delayed_reveal_and_advance 참고) — 툴 호출 시점부터
# 세면 아직 안 끝난 발화 도중에 화면이 넘어가버린다.
REVEAL_HOLD_SEC = 4.0
# 하찮미 모드에서 로봇이 자기 추측을 말한 턴이 끝난 뒤, submit_guess(speaker="robot")가
# 도착하길 이만큼 더 기다려본다(모델이 다음 턴에 뒤늦게 부르는 경우가 있어 여유를 둔다).
ROBOT_GUESS_GRACE_SEC = 2.0

# 정답/반응을 말하라고 주입하는 히든 턴 뒤에 항상 붙이는 제약(2026-08-10, 40단계).
# 실물 로그에서 모델이 반응을 말한 김에 "다음 문제입니다. 이 물건은 무엇일까요?"까지
# 이어서 말해버렸다 — 그런데 그 시점엔 화면이 아직 정답 공개 사진이고, 다음 문제 사진은
# REVEAL_HOLD_SEC 뒤에야 뜬다. 그래서 참가자는 아직 보지도 못한 문제를 질문받고,
# 잠시 뒤 화면이 바뀌면서 next_question_prompt()가 같은 질문을 또 하게 된다
# ("이 물건은 무엇일까요?"가 두 번 나오는 실물 제보의 원인).
_REVEAL_SPEECH_SUFFIX = (
    " 그리고 이 턴에서는 위 반응까지만 말하고 곧바로 멈추세요 — 다음 문제를 소개하거나 "
    "\"이 물건은 무엇일까요?\"라고 미리 묻지 마세요(다음 사진은 아직 화면에 뜨지 않았습니다). "
    "다음 문제가 실제로 화면에 뜨면 그때 제가 따로 알려드립니다."
)

# 사용자가 아직 아무 말도 안 했는데 모델이 submit_guess를 부를 때 돌려주는 가드.
# 실물 로그(2026-08-10 척척박사)에서 모델이 guess_text="사용자의 말을 기다리는 중",
# "사용자의 대답을 기다리는 중"으로 두 번 호출했고, 그때마다 그 문항이 오답으로 채점되고
# 소모됐다 — 5문항 중 2문항이 참가자가 보지도 못한 채 오답으로 기록된 셈이라 연구 데이터
# 무결성 문제다. 잘못 막았더라도 사용자가 실제로 답했다면 다음 턴에 다시 호출되므로
# 손해는 턴 하나뿐이고, 반대로 안 막으면 문항이 조용히 사라진다.
_NO_USER_SPEECH_GUARD = (
    "사용자는 아직 이 문제에 대해 아무 말도 하지 않았습니다 — 방금 넘긴 것은 사용자의 답이 "
    "아닙니다. 이 툴을 부르지 말고, 아무 말도 하지 말고 사용자가 실제로 답할 때까지 조용히 "
    "기다리세요."
)

# 다음 문제가 상태 기계상으로는 이미 "현재 문제"인데 아직 화면에는 안 뜬 구간에서
# 판정을 요청할 때 돌려주는 가드(2026-08-10, 45단계 점검에서 발견). 그런 구간이 두 군데
# 있다: (a) 정답 공개 화면을 REVEAL_HOLD_SEC 동안 유지하는 동안 (b) 라운드가 바뀌어
# 모드 전환 안내를 말하는 동안. 두 경우 모두 참가자는 다음 사진을 아직 본 적이 없는데,
# 그 사이에 한 말(이전 문제에 대한 뒤늦은 반응 등)이 다음 문항의 답으로 채점되면 그
# 문항을 통째로 잃는다 — 40단계에서 실제로 겪은 사고와 같은 종류이고, 그때 넣은
# user_spoke 리셋은 "사진이 뜨는 순간"만 덮어서 이 구간을 못 막는다.
_QUESTION_NOT_ON_SCREEN_GUARD = (
    "다음 문제 사진이 아직 화면에 뜨지 않았습니다 — 사용자는 이 문제를 아직 보지 못했으므로 "
    "지금 들은 말은 이 문제의 답이 될 수 없습니다. 이 툴을 부르지 말고, 사진이 떴다는 제 "
    "안내가 올 때까지 조용히 기다리세요."
)


def make_quiz_tools(quiz_ui_q, busy: threading.Event, motion_ctx, inject_turn, loop,
                     turn_seq: list, emotion_queue=None, num_questions: int = 5,
                     drain_playback=None, mute_speech: list | None = None,
                     user_spoke: list | None = None, mode_order: list | None = None):
    """motion_ctx = (port, pkt, lock, shared_state, home_pan, home_tilt) — launcher.py가
    core.motion_tools.make_motion_tools에 넘기는 것과 같은 튜플. busy도 그쪽과 같은
    threading.Event를 공유해야 퀴즈 리액션 모션과 LLM이 부르는 제스처가 같은 모터를
    동시에 건드리지 않는다. inject_turn(text)는 launcher.py가 만드는 코루틴으로,
    session.send_client_content(...)를 호출해 새 히든 턴을 보낸다. loop는 이 코루틴이
    도는 asyncio 이벤트 루프(asyncio.get_event_loop()) — request_hint의 지연 태스크
    예약에 쓰인다. turn_seq는 launcher.py가 recv_loop에서 관리하는 1칸짜리 리스트
    ([0])로, 로봇의 턴이 끝날 때마다(turn_complete 또는 interrupted) 1씩 증가한다 —
    "지금 이 순간 이후로 턴이 몇 번 더 끝났는지"를 셀 수 있어, "지금 진행 중인(또는
    막 시작되려는) 응답이 끝날 때까지 기다린다"를 speaking_done 같은 단일 불리언
    Event보다 정확하게 표현한다(아래 _wait_for_turn_after 참고 — Event는 "이미 끝난
    상태"와 "아직 시작 전이라 우연히 끝난 것처럼 보이는 상태"를 구분 못 해 정답 공개가
    로봇이 말하기도 전에 뜨는 경합이 있었다, 2026-08-08).

    drain_playback/mute_speech/user_spoke도 launcher.py가 넘기는 오디오·발화 상태 훅이다
    (전부 선택 — 안 넘기면 오프라인 테스트에서 기존 동작 그대로 돈다):
      - drain_playback(): 아직 재생이 안 끝난 오디오가 다 빠질 때까지 기다리는 코루틴.
        안 주면 예전처럼 POST_SPEECH_DRAIN_SEC만큼 그냥 잔다.
      - mute_speech: 1칸짜리 리스트. True인 동안 launcher.py가 모델 오디오를 재생하지
        않고 버린다 — 침묵 지시를 어긴 발화를 파이썬이 강제로 막는 용도.
      - user_spoke: 1칸짜리 리스트. 사용자가 실제로 말하면 launcher.py가 True로 올리고,
        submit_guess가 판정에 쓰면서 다시 False로 소비한다(_NO_USER_SPEECH_GUARD 참고).

    mode_order: 진행자가 이 참가자에게 배정한 모드 순서. 안 주면 .env의 QUIZ_MODE_ORDER를
    읽는다(형식은 core/quiz_state.py의 parse_mode_order 참고).
    """
    port, pkt, lock, shared_state, home_pan, home_tilt = motion_ctx
    # 크래시 복구용(docs/experiment_design.md §1-1): 세션이 도중에 죽어 launcher를
    # 재시작해야 할 때, 참가자가 이미 마친 라운드 수를 .env의 QUIZ_ROUND_OFFSET으로
    # 지정하면 이미 진행한 모드와 이미 정답이 공개된 사진 슬라이스를 둘 다 건너뛴다
    # (예: 1라운드 완료 후 크래시 -> QUIZ_ROUND_OFFSET=1로 재시작하면 2번째 모드가
    # 6번째 사진부터 배분됨).
    round_offset = int(os.getenv("QUIZ_ROUND_OFFSET", "0"))
    if round_offset:
        print(f"ℹ️ QUIZ_ROUND_OFFSET={round_offset} — 모드 순서와 문제 은행을 "
              f"{round_offset}라운드만큼 건너뛰고 시작합니다(크래시 복구 모드). "
              f"실험이 끝나면 .env에서 지우세요!")
    if mode_order is None:
        mode_order = parse_mode_order(os.getenv("QUIZ_MODE_ORDER"))
    session = QuizSession(load_question_bank(), num_questions=num_questions,
                           initial_round_offset=round_offset, mode_order=mode_order)
    # asyncio.create_task()가 만든 태스크는 어딘가에서 강하게 참조하고 있지 않으면
    # 이벤트 루프가 약한 참조만 들고 있어서 도중에 가비지 컬렉션될 수 있다(공식 문서
    # 경고) — 지연된 거절 대사가 통째로 사라지는 사고를 막기 위해 여기 붙잡아둔다.
    _pending_stall = {"task": None}
    # _pending_stall과 같은 이유로(가비지 컬렉션 방지) 붙잡아둔다 — 정답 공개 화면을
    # REVEAL_HOLD_SEC 뒤 다음 문제로 넘기는 지연 태스크.
    _pending_reveal_transition = {"task": None}
    # 침묵 지시를 내린 턴이 끝나면 음소거를 자동으로 푸는 태스크(위 두 개와 같은
    # 가비지 컬렉션 방지용 참조 보관).
    _pending_unmute = {"task": None}
    # 하찮미 모드에서 로봇 자신의 추측 판정(submit_guess(speaker="robot"))이 오는지
    # 지켜보는 감시 태스크 — 안 오면 퀴즈가 그 문제에서 영영 멈춘다(아래 참고).
    _pending_robot_guess_watch = {"task": None}
    # 라운드 시작/전환 흐름(안내 발화 -> 사진 push -> 첫 문제 질문)을 도는 태스크.
    # 위 태스크들과 같은 이유로 참조를 붙잡아둔다(가비지 컬렉션 방지).
    _pending_round_start = {"task": None}
    # 지금 화면에 실제로 떠 있는 문제의 id — 상태 기계의 current_question은 이미 다음
    # 문제로 넘어가 있는데 화면은 아직 안 바뀐 구간이 존재하므로(정답 공개 유지, 모드
    # 전환 안내), "참가자가 보고 있는 문제"는 별도로 추적해야 한다(_QUESTION_NOT_ON_SCREEN_GUARD).
    _shown_question = {"id": None}

    async def _drain_audio():
        if drain_playback is not None:
            await drain_playback()
        else:
            # 모듈 전역을 호출 시점에 읽는다 — 오프라인 테스트가 이 값을 짧게 패치한다.
            await asyncio.sleep(POST_SPEECH_DRAIN_SEC)

    def _set_mute(on: bool):
        if mute_speech is not None:
            mute_speech[0] = on

    async def _unmute_after_turn(baseline: int, timeout: float = 20.0):
        try:
            # 턴 종료 신호가 영영 안 오는 상황(세션 사망 등)에서도 로봇이 그 뒤로 계속
            # 벙어리로 남지 않도록 상한을 둔다 — 음소거는 어디까지나 한 턴짜리 안전장치다.
            await asyncio.wait_for(_wait_for_turn_after(baseline), timeout)
        except asyncio.TimeoutError:
            print("⚠️ 침묵 지시 턴이 제시간에 끝나지 않아 음소거를 강제로 해제합니다.")
        finally:
            # 취소되든 정상 종료되든 반드시 음소거를 푼다 — 여기서 새면 로봇이 그 뒤로
            # 계속 벙어리가 되는 최악의 실패 모드라, 실패 경로를 반드시 덮는다.
            _set_mute(False)
            _pending_unmute["task"] = None

    def _mute_until_turn_ends():
        """"이 턴에서는 침묵하세요"라는 지시를 내린 직후에 호출한다 — 그 지시를 어기고
        나오는 오디오를 재생하지 않도록 launcher.py의 게이트를 닫고, 그 턴이 실제로
        끝나면 다시 연다. 같은 턴에서 두 번 호출되면(하찮미의 user->robot 연속 판정)
        이미 걸어둔 태스크를 그대로 둔다."""
        if mute_speech is None:
            return
        _set_mute(True)
        task = _pending_unmute["task"]
        if task is not None and not task.done():
            return
        _pending_unmute["task"] = loop.create_task(_unmute_after_turn(turn_seq[0]))

    def _cancel_robot_guess_watch():
        task = _pending_robot_guess_watch["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_robot_guess_watch["task"] = None

    async def _watch_for_robot_guess():
        """하찮미 모드는 로봇이 자기 추측을 말한 뒤 submit_guess(speaker="robot")를
        불러줘야만 채점/전진한다 — 모델이 그 호출을 빼먹으면 그 문제에서 영영 멈춘다
        (2026-08-10 실물: "치즈!"라고 말해놓고 툴을 안 불러 퀴즈가 정지, 참가자가
        "정답 보여줘야지"라고 해도 복구 불가). 모델이 툴을 부르는 걸 100% 보장할 수는
        없으니, 한 번 재촉하고 그래도 안 오면 사용자 답만으로 채점해 전진시킨다."""
        try:
            for attempt in range(2):
                baseline = turn_seq[0]
                await _wait_for_turn_after(baseline)
                await asyncio.sleep(ROBOT_GUESS_GRACE_SEC)
                if session.pending_user_guess is None:
                    return  # 정상적으로 호출됨
                if attempt == 0:
                    print("⚠️ 하찮미: 로봇 추측 판정 호출이 오지 않아 한 번 재촉합니다.")
                    await inject_turn(
                        "당신이 방금 말한 추측이 아직 기록되지 않았습니다 — 지금 즉시 "
                        "submit_guess(speaker=\"robot\", guess_text=<방금 말한 사물 이름>)을 "
                        "호출하세요. 이 턴에서는 아무 말도 하지 마세요."
                    )
                    _mute_until_turn_ends()
            if session.pending_user_guess is None:
                return
            print("⚠️ 하찮미: 재촉에도 로봇 추측이 오지 않아 사용자 답만으로 채점하고 넘어갑니다.")
            question_before = session.current_question
            count_before = len(session.results)
            session.resolve_robot_missing_guess()
            if len(session.results) > count_before:
                reveal_speech = session.pending_reveal_speech
                session.pending_reveal_speech = None
                _schedule_reveal(question_before, reveal_speech)
        finally:
            _pending_robot_guess_watch["task"] = None

    def _watch_robot_guess_if_staged():
        """resolve_user_guess/request_hint가 하찮미 이벤트로 분기해 로봇 추측을 기다리는
        상태(pending_user_guess가 채워진 상태)가 됐으면 감시를 건다."""
        if session.mode != "imperfect" or session.pending_user_guess is None:
            return
        _cancel_robot_guess_watch()
        _pending_robot_guess_watch["task"] = loop.create_task(_watch_for_robot_guess())

    def _force_unmute():
        task = _pending_unmute["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_unmute["task"] = None
        _set_mute(False)

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
            # 새 사진이 뜨기 **전에** 한 말은 이 문제의 답이 될 수 없다 — 신호를 여기서
            # 버린다. 2026-08-10 실물 로그에서 참가자가 로봇이 조용하니 이전 문제의 답을
            # 다시 말했는데, 그게 다음 문항의 답으로 채점돼 그 문항을 통째로 잃었다
            # (참가자는 그 사진을 보지도 못했다). user_spoke가 없으면(오프라인 테스트)
            # 아무 일도 안 한다.
            if user_spoke is not None:
                user_spoke[0] = False
            _shown_question["id"] = q.id
        else:
            quiz_ui_q.put({"type": "hide"})
            _shown_question["id"] = None

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

    async def _wait_for_turn_after(baseline: int, poll: float = 0.05):
        """turn_seq[0]이 baseline보다 커질 때까지(=baseline을 기록한 시점 이후로 로봇의
        턴이 적어도 한 번 끝날 때까지) 기다린다. speaking_done 같은 단일 Event를 매번
        "지금부터 새로 기다리기" 용도로 재사용하면, 정확히 언제 clear해야 하는지가
        호출부마다 미묘하게 달라 경합이 반복적으로 생겼다(2026-08-08, 아래 참고) — 대신
        baseline을 "이 시점 기준"으로 캡처해두고 그 이후의 증가만 보므로 호출 시점과
        무관하게 항상 정확하다."""
        while turn_seq[0] <= baseline:
            await asyncio.sleep(poll)

    def _cancel_round_start():
        task = _pending_round_start["task"]
        if task is not None and not task.done():
            task.cancel()
        _pending_round_start["task"] = None

    async def _begin_round_flow(wait_baseline: int | None):
        """다음 라운드(모드)를 시작하는 단일 경로 — start_quiz 직후의 첫 라운드와,
        한 라운드가 끝난 뒤의 모드 전환이 **같은 코드**를 탄다.

        순서를 Python이 직접 통제하는 이유는 39단계와 같다(모델이 언제 무엇을 말할지에
        기대면 이미지-발화 순서가 매번 어긋난다):
          (1) 직전 발화(전체 안내 또는 마지막 정답 반응)가 실제로 끝날 때까지 기다리고
          (2) 모드 전환 안내를 히든 턴으로 주입해 "지금부터 O번 XX 모드"를 말하게 하고
          (3) 그 안내가 끝난 뒤에야 첫 문제 사진을 띄우고
          (4) 그제서야 "이 물건은 무엇일까요?"를 묻게 한다.

        wait_baseline이 None이면 (1)을 건너뛴다 — 이미 발화 종료를 기다린 뒤 호출하는
        경우(_delayed_reveal_and_advance 안에서 이어서 부를 때).
        """
        try:
            if wait_baseline is not None:
                await _wait_for_turn_after(wait_baseline)
                await _drain_audio()

            text = session.begin_next_round()
            if text is None:
                # 배정된 모든 라운드를 마쳤다 — 화면을 정리하고 마무리 인사만 시킨다.
                _push_question_or_hide()
                await inject_turn(session.final_wrapup_prompt())
                return

            label = mode_label(session.mode)
            print(f"▶️ 퀴즈 라운드 {session.current_round_number()}/{session.total_rounds} 시작: {label}")
            quiz_ui_q.put({"type": "rules", "text": f"{label} 모드"})

            intro_baseline = turn_seq[0]
            # 이제부터는 로봇이 **반드시 말해야 하는** 턴이다 — 직전 판정 턴에 걸어둔
            # 음소거가 아직 안 풀렸을 수 있으므로(해제는 턴 종료를 0.05초 간격으로
            # 폴링하는 별도 태스크가 한다) 여기서 확실히 연다. 안 그러면 모드 전환
            # 안내가 통째로 삼켜져 참가자는 모드가 바뀐 걸 모른 채 다음 문제를 본다.
            _force_unmute()
            await inject_turn(text)
            await _wait_for_turn_after(intro_baseline)
            await _drain_audio()

            _push_question_or_hide()
            await inject_turn(session.next_question_prompt())
        finally:
            # 내가 이 홀더에 등록된 태스크일 때만 비운다 — 취소된 옛 태스크의 finally가
            # 새 태스크가 등록된 **뒤에** 실행될 수 있어서(취소는 다음 await 지점에
            # 전달된다), 무조건 비우면 살아있는 새 태스크의 참조를 지워버린다.
            if _pending_round_start["task"] is asyncio.current_task():
                _pending_round_start["task"] = None

    async def _delayed_reveal_and_advance(question, reveal_speech: str | None, baseline: int):
        # 2026-08-08 구조 변경 — 예전엔 판정 툴 호출의 응답 자체가 정답/반응 텍스트라
        # 모델이 곧장 그걸 말했는데, Live API는 여러 툴 호출을 오디오 한 마디 없이 연달아
        # 처리한 뒤에야 한꺼번에 말할 수 있어서(하찮미 모드의 사용자 채점 툴 -> 로봇 채점
        # 툴이 실제로 이렇게 배치되는 게 실물 로그로 확인됨), "이 턴이 끝나면 이미지를
        # 띄운다"는 예전 동기화가 무의미해졌다 — 그 "이 턴"에 이미 반응 텍스트와 "다음
        # 문제로 가볼까요" 필러까지 다 말해버린 뒤였기 때문. 이제 판정 시점의 툴 응답은
        # core/quiz_state.py의 _HOLD_FOR_REVEAL(침묵 지시)뿐이고, 실제 반응 텍스트
        # (reveal_speech)는 이미지가 뜬 뒤에 우리가 직접 새 히든 턴으로 주입한다 — 모델이
        # 툴 호출을 어떻게 몰아서 처리하든, 이미지->반응 순서는 항상 Python이 보장한다.
        try:
            # baseline은 이 코루틴이 처음 실행되는 시점이 아니라 **예약된 시점**(판정 툴이
            # 반환되던 순간)에 잡아서 넘겨받는다 — 여기서 직접 읽으면, 예약과 첫 실행
            # 사이에 그 판정 턴이 끝나버린 경우 baseline이 이미 끝난 값으로 잡혀 정답
            # 공개가 "다음 턴"까지 밀린다(_mute_until_turn_ends가 baseline을 동기적으로
            # 잡는 것과 같은 이유).
            await _wait_for_turn_after(baseline)  # 침묵 지시를 받은 턴이 끝나길 기다림
            await _drain_audio()
            _push_reveal(question)
            if reveal_speech:
                reveal_baseline = turn_seq[0]
                # 판정 턴에 걸어둔 음소거가 아직 안 풀렸을 수 있다 — 정답 반응은 반드시
                # 들려야 하므로 여기서 확실히 연다(_begin_round_flow와 같은 이유).
                _force_unmute()
                # _REVEAL_SPEECH_SUFFIX: 반응만 말하고 다음 문제를 미리 묻지 말라는 제약을
                # 항상 함께 보낸다(위 상수 주석 — "이 물건은 무엇일까요?" 중복 질문의 원인).
                await inject_turn(reveal_speech + _REVEAL_SPEECH_SUFFIX)
                # 반응 발화가 실제로 끝날 때까지 기다린 뒤에야 REVEAL_HOLD_SEC(사진을 유지하는
                # 시간)을 세기 시작한다 — 안 그러면 로봇이 아직 반응을 말하는 도중에 사진이
                # 넘어가버린다.
                await _wait_for_turn_after(reveal_baseline)
            await asyncio.sleep(REVEAL_HOLD_SEC)
            if session.round_finished:
                # 이번 라운드의 마지막 문제였다 — 다음 모드로 넘어가거나(전환 안내 후 첫
                # 문제) 배정된 라운드를 다 마쳤으면 마무리한다. 이미 정답 반응 발화가 끝나길
                # 기다린 뒤라 여기서는 추가 대기 없이(None) 곧장 이어간다.
                #
                # 일부러 이 태스크 **안에서** 이어서 돌린다 — 별도 태스크로 떼어내면
                # _pending_reveal_transition/_pending_round_start 어느 홀더에도 안 잡혀
                # end_quiz_early()가 취소하지 못하고, 퀴즈가 끝난 뒤에 다음 라운드 사진이
                # 뜨는 사고가 난다.
                await _begin_round_flow(None)
                return
            _push_question_or_hide()
            # 화면이 실제로 다음 문제로 바뀐 이 순간에만 모델에게 물어보라고 알린다 —
            # session.resolve_*_guess()가 곧장 돌려주면 아직 이전 문제 reveal 화면인 채로
            # 모델이 먼저 물어봐서 말/화면이 어긋나는 문제(2026-07-30)가 있었다.
            if session.active and session.current_question is not None:
                await inject_turn(session.next_question_prompt())
            else:
                # end_quiz_early 등으로 세션이 이미 꺼진 비정상 경로 — 막다른 길이 되지
                # 않도록 마무리 인사만 시킨다.
                await inject_turn(session.final_wrapup_prompt())
        finally:
            # _begin_round_flow와 같은 이유로 "내가 등록된 태스크일 때만" 비운다.
            if _pending_reveal_transition["task"] is asyncio.current_task():
                _pending_reveal_transition["task"] = None

    def _schedule_reveal(question, reveal_speech: str | None):
        _cancel_pending_reveal_transition()
        _pending_reveal_transition["task"] = loop.create_task(
            _delayed_reveal_and_advance(question, reveal_speech, turn_seq[0])
        )

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
        # 표정 시퀀스(2026-08-07 사용자 요청): 뜸들이는 동안만 THINKING이고, 거절 대사를
        # 말할 때는 NEUTRAL로 돌아와 있어야 한다("고민해봤지만 결국 기계적으로 거절"이라는
        # 서사) — 거절 주입 직후에 되돌려 발화 시작 시점엔 무표정이 되게 한다.
        if emotion_queue:
            emotion_queue.put("NEUTRAL")

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

        The whole quiz (three modes in the order the experimenter configured,
        five questions each) then runs automatically — you never choose a mode
        and never call this again mid-quiz.
        """
        if session.active:
            # 거절만 하면 모델이 빠져나갈 방법을 몰라 막다른 길이 된다 — 무엇을 해야
            # 하는지 같이 알려준다(2026-08-10, 44단계의 교훈).
            print("⚠️ 퀴즈 진행 중 start_quiz 재호출 — 거절했습니다.")
            return ("이미 퀴즈가 진행 중입니다 — 다시 시작하지 마세요(모드 전환은 제가 "
                    "자동으로 안내합니다). 사용자가 정말 처음부터 새로 시작하길 원하면 "
                    "먼저 end_quiz_early()를 호출해 이번 퀴즈를 끝내세요.")
        if session.round_index >= session.total_rounds:
            # 배정된 3라운드를 이미 다 마친 참가자 — 다시 시작하면 앞 라운드에서 정답이
            # 공개된 사진이 재사용되고 결과 로그에 4번째 라운드가 섞인다(연구 데이터
            # 무결성). 막되, 실험자가 정말 재실험을 원하면 어떻게 하는지 알려준다.
            print(f"⚠️ 배정된 {session.total_rounds}라운드를 모두 마친 뒤 start_quiz 호출 — 거절했습니다.")
            return ("이번 참가자는 배정된 모든 모드를 이미 마쳤습니다 — 퀴즈를 다시 시작할 "
                    "수 없습니다. 사용자에게 퀴즈가 모두 끝났다고 알리고 평소 대화로 "
                    "돌아가세요(재실험이 필요하면 실험자가 로봇을 다시 시작해야 합니다).")
        _cancel_pending_stall()
        _cancel_pending_reveal_transition()
        _cancel_robot_guess_watch()
        _cancel_round_start()
        _force_unmute()
        text = session.start()
        quiz_ui_q.put({"type": "rules", "text": "부분 확대 사진 퀴즈를 시작합니다!"})
        # 전체 안내 발화가 실제로 끝나면 첫 라운드를 시작한다 — 지금 이 시점의 turn_seq를
        # baseline으로 잡아둬야 "곧 시작될 안내 턴"이 끝나는 것을 정확히 기다린다.
        _pending_round_start["task"] = loop.create_task(_begin_round_flow(turn_seq[0]))
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

        if speaker == "user" and session.current_question is not None \
                and session.current_question.id != _shown_question["id"]:
            # 상태 기계는 이미 다음 문제를 가리키는데 화면은 아직 안 바뀐 구간 —
            # 참가자가 보지도 못한 문항이 소모되는 걸 막는다(위 가드 주석 참고).
            print("⚠️ 화면에 아직 안 뜬 문제에 대한 판정 요청 — 거절했습니다.")
            return _QUESTION_NOT_ON_SCREEN_GUARD

        if speaker == "user" and user_spoke is not None:
            # 사용자가 실제로 말한 적이 없으면 판정 자체를 하지 않는다 — 안 그러면 그
            # 문항이 유령 오답으로 소모된다(위 _NO_USER_SPEECH_GUARD 주석).
            if not user_spoke[0]:
                return _NO_USER_SPEECH_GUARD
            # 이번 발화는 여기서 소비한다 — 같은 발화로 두 번 판정되지 않게(read-once,
            # session.pending_user_guess와 같은 패턴).
            user_spoke[0] = False

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
                    # 뜸들이던 THINKING 표정이 남아있을 수 있다 — 정답 공개를 말할 때는
                    # 무표정으로(거절 대사 때와 같은 시퀀스, 2026-08-07).
                    if emotion_queue:
                        emotion_queue.put("NEUTRAL")
                else:
                    # 실제 답 시도(맞았든 틀렸든) — 매번 거절만 한다. 이미 스톨이 대기
                    # 중이면 재예약하지 않는다(계속 말할 때마다 타이머가 리셋되는 사고 방지).
                    # 뜸들이는 동안은 THINKING 표정(2026-08-07 확정 시퀀스: neutral ->
                    # 요청받으면 thinking -> 거절/공개 발화 때 neutral 복귀). 모션 쪽
                    # play_thinking_stall도 THINKING을 넣지만 busy면 통째로 스킵되므로,
                    # 표정만큼은 여기서 무조건 보장한다(같은 값 중복 put은 무해).
                    if emotion_queue:
                        emotion_queue.put("THINKING")
                    _run_guarded(play_thinking_stall, port, pkt, lock, shared_state, emotion_queue)
                    _schedule_stall_refusal()
                    # 뜸들이는 동안은 완전히 침묵해야 한다("음...", "어디 보자" 같은
                    # 추임새도 금지 — core/quiz_state.py의 반환문 참고). 지시만 믿지 않고
                    # 이 턴의 오디오를 실제로 막는다.
                    _mute_until_turn_ends()
            else:
                # 하찮미: 로봇이 자기 추측을 말하고 submit_guess(speaker="robot")를
                # 불러줘야 전진한다 — 그 호출이 실제로 오는지 지켜본다(위 감시 함수 주석).
                _watch_robot_guess_if_staged()

        advanced = len(session.results) > results_count_before
        if advanced:
            # 판정이 끝났다 — 이 턴에서 모델이 뭘 말하든(정답을 먼저 말해버리든, 다음
            # 문제를 미리 묻든) 재생하지 않는다. 정답 반응은 정답 사진이 화면에 뜬 뒤
            # _delayed_reveal_and_advance가 히든 턴으로 따로 시킨다.
            _mute_until_turn_ends()
            # 실제로 채점/전진했을 때만 — 이전 문제에서 걸어둔 지연된 거절 대사/정답 공개
            # 전환이 남아있으면 지금 취소하고, 방금 답한 문제의 정답 공개 화면을 띄운다.
            # resolve_*_guess가 상황이 안 맞으면(퀴즈 비활성, pending_user_guess 없음, 하찮미
            # 포기 신호로 staging만 한 경우 등) 아무것도 기록/전진하지 않을 수 있는데, 그런
            # 무효/staging 호출에 reveal을 띄우면 아직 안 풀린 문제의 "정답 공개" 화면이
            # 잘못 뜨는 사고가 난다.
            _cancel_pending_stall()
            # session.resolve_*_guess()가 방금 채워둔 반응 지시문 — 판정 툴의 응답 자체는
            # 침묵 지시(_HOLD_FOR_REVEAL)뿐이고, 이 텍스트는 이미지가 뜬 뒤에 별도의 히든
            # 턴으로 주입된다(core/quiz_state.py 상단 주석 참고). 다음 판정에 잘못 재사용되지
            # 않도록 여기서 즉시 꺼내고 지운다(pending_user_guess와 같은 read-once 패턴).
            reveal_speech = session.pending_reveal_speech
            session.pending_reveal_speech = None
            _schedule_reveal(question_before_call, reveal_speech)

        return text

    def request_hint() -> str:
        """Call this when the user explicitly asks for a hint, or asks you to
        guess the answer for them, during an active quiz question. Takes no arguments.
        """
        mode_before = session.mode
        text = session.request_hint()
        if mode_before == "annoying":
            # submit_guess의 실제 답 시도 분기와 같은 표정 시퀀스(위 주석 참고).
            if emotion_queue:
                emotion_queue.put("THINKING")
            _run_guarded(play_thinking_stall, port, pkt, lock, shared_state, emotion_queue)
            _schedule_stall_refusal()
            # submit_guess의 실제 답 시도 분기와 같은 이유 — 뜸들이는 동안의 침묵을
            # 지시만으로 믿지 않고 실제로 막는다.
            _mute_until_turn_ends()
        else:
            # 하찮미에서 힌트/대신 풀어달라는 요청도 "저도 맞춰볼게요" 이벤트로 가므로
            # submit_guess 쪽과 똑같이 로봇 추측 호출을 지켜봐야 한다(안 그러면 그 경로로
            # 들어간 문제에서만 여전히 멈춘다 — 실물 로그의 1번 문제가 이 경로였다).
            _watch_robot_guess_if_staged()
        return text

    def end_quiz_early() -> str:
        """End the quiz session early — call only if the experimenter or
        participant explicitly asks to stop before all questions are done.
        Takes no arguments.
        """
        _cancel_pending_stall()
        _cancel_pending_reveal_transition()
        _cancel_robot_guess_watch()
        _cancel_round_start()
        _force_unmute()
        text = session.end_early()
        quiz_ui_q.put({"type": "hide"})
        return text

    return start_quiz, submit_guess, request_hint, end_quiz_early, session
