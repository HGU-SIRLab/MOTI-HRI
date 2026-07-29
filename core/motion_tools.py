"""play_gesture/express_gesture — docs/architecture.md §05의 Layer 1/2 진입점
(hardware.motion.play_manual_motion/play_express_gesture)을 Gemini 툴로 노출한다.
memory_tools.py/emotion_tools.py와 같은 클로저 패턴.

두 함수 다 물리 동작이 끝날 때까지 블로킹한다(포옹 10초+ 등). Live 세션의 tool_call
처리는 동기적이라(docs/architecture.md §09, docs/progress.md 4단계 핵심 발견 2번),
그대로 블로킹하면 그동안 오디오 스트림도 멈춘다 — 그래서 실제 실행은 항상 백그라운드
스레드로 넘기고, 툴 자체는 "시작했다"는 응답만 즉시 돌려준다.

2026-07-27 실물 통합 검토에서 발견: hardware/motion.py의 Layer 1 매크로(hug/greeting/
wave/shy)는 재진입 방지가 전혀 없다(dance만 자체적으로 _dance_running으로 막혀있음) —
test_motions.py는 메뉴 입력이 사람 페이스라 문제가 드러날 수 없었지만, 실시간 대화에서
모델이 연속된 턴에 제스처를 두 번 부르면 두 매크로가 동시에 같은 팔/손 모터에 다른 목표
위치를 써서 예측 불가능한 움직임이 나올 수 있다. play_gesture와 express_gesture가 같은
`busy` 가드를 공유해서, Layer 1/Layer 2 동작끼리도 서로 끼어들지 못하게 막는다.
"""
import threading
import time

from hardware.motion import EXPRESS_JOINTS, is_dancing, play_express_gesture, play_manual_motion

VALID_GESTURES = ("greeting", "wave", "hug", "shy", "dance")


def make_motion_tools(port, pkt, lock, shared_state, home_pan=2081, home_tilt=2071, emotion_queue=None,
                       busy: threading.Event | None = None):
    """play_gesture, express_gesture 두 툴을 함께 만들어 반환한다(하나의 busy 가드 공유).

    busy를 외부에서 넘기면(예: core/quiz_tools.py의 퀴즈 리액션 모션과 공유) 그 이벤트를
    그대로 쓴다 — LLM이 부르는 제스처와 퀴즈 리액션 모션이 서로 다른 관절이라도 동시에
    실행되며 충돌하지 않게 하기 위함. 안 넘기면 이 함수 안에서 새로 만든다(기존 호출부 호환)."""
    if busy is None:
        busy = threading.Event()

    def _run_macro(name):
        try:
            play_manual_motion(name, port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue)
            # dance는 play_manual_motion이 즉시 리턴하고 실제 안무는 별도 스레드에서
            # 계속 진행된다(hardware/motion.py의 play_dance) — 진짜 끝날 때까지 busy를
            # 유지해야 그 사이 다른 제스처가 같은 팔 모터에 끼어들지 않는다.
            while is_dancing():
                time.sleep(0.2)
        finally:
            busy.clear()

    def _run_express(joint, intensity, speed, repeat):
        try:
            play_express_gesture(joint, intensity, speed, repeat, port, pkt, lock, shared_state)
        finally:
            busy.clear()

    def play_gesture(name: str) -> str:
        """Play a physical gesture on the robot's body.

        Call this when a gesture would naturally accompany what you're
        saying (e.g. "greeting"/"wave" when meeting someone, "hug" when
        comforting someone, "shy" for a bashful reaction, "dance" when
        celebrating). Don't call this every turn — only when it clearly
        fits the moment.

        Args:
            name: one of "greeting", "wave", "hug", "shy", "dance".
        """
        if name not in VALID_GESTURES:
            print(f"⚠️ 알 수 없는 제스처 '{name}' — 무시합니다.")
            return f"ignored unknown gesture {name}"

        if busy.is_set() or is_dancing():
            print(f"⚠️ 이미 다른 제스처가 실행 중이라 '{name}' 요청을 무시합니다.")
            return "ignored — another gesture is already playing"

        busy.set()
        threading.Thread(target=_run_macro, args=(name,), daemon=True).start()
        return f"playing gesture: {name}"

    def express_gesture(joint: str, intensity: float, speed: str = "normal", repeat: int = 1) -> str:
        """Play a small, tunable body movement on one joint — lighter-weight than play_gesture.

        Use this for subtle reactions that don't warrant a full named gesture:
        a small head tilt/nod while listening, a slight shoulder shrug, a small
        arm raise. Keep intensity low (0.1-0.4) for subtle reactions and only go
        high (0.7-1.0) for genuinely big moments.

        Args:
            joint: one of "right_arm", "left_arm", "shoulder", "head_nod".
            intensity: how big the movement is, from 0.0 (barely noticeable) to 1.0 (full range).
            speed: "slow", "normal", or "fast".
            repeat: how many times to repeat the movement, 1-3.
        """
        if joint not in EXPRESS_JOINTS:
            print(f"⚠️ 알 수 없는 관절 '{joint}' — 무시합니다.")
            return f"ignored unknown joint {joint}"

        if busy.is_set() or is_dancing():
            print(f"⚠️ 이미 다른 제스처가 실행 중이라 '{joint}' 요청을 무시합니다.")
            return "ignored — another gesture is already playing"

        busy.set()
        threading.Thread(target=_run_express, args=(joint, intensity, speed, repeat), daemon=True).start()
        return f"expressing gesture: {joint} (intensity={intensity})"

    return play_gesture, express_gesture
