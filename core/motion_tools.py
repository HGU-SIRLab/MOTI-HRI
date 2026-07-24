"""play_gesture — docs/architecture.md §05의 Layer 1 진입점(hardware.motion.play_manual_motion)을
Gemini 툴로 노출한다. memory_tools.py/emotion_tools.py와 같은 클로저 패턴.

play_manual_motion은 "dance"를 제외한 나머지 매크로(hug/greeting/wave/shy)를 블로킹으로
실행한다(포옹 10초+ 등). Live 세션의 tool_call 처리는 동기적이라(docs/architecture.md §09,
docs/progress.md 4단계 핵심 발견 2번), 그대로 블로킹하면 그동안 오디오 스트림도 멈춘다 —
그래서 실제 실행은 항상 백그라운드 스레드로 넘기고, 툴 자체는 "시작했다"는 응답만 즉시
돌려준다.
"""
import threading

from hardware.motion import play_manual_motion

VALID_GESTURES = ("greeting", "wave", "hug", "shy", "dance")


def make_play_motion_tool(port, pkt, lock, shared_state, home_pan=2081, home_tilt=2071, emotion_queue=None):
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

        threading.Thread(
            target=play_manual_motion,
            args=(name, port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue),
            daemon=True,
        ).start()
        return f"playing gesture: {name}"

    return play_gesture
