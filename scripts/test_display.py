"""표정 UI(display/) 독립 테스트 도구.

로봇도 카메라도 필요 없다 — pygame 창이 뜨면 콘솔에 감정 이름을 입력해서
emotion_queue로 밀어넣고 화면이 바뀌는지 확인하는 용도.
창에서 ESC를 누르거나 콘솔에 q를 입력하면 종료.

사용:
    python scripts/test_display.py
"""
import os
import sys
import threading
import queue

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.emotion_tools import VALID_EMOTIONS
from display.main import RobotFaceApp

# sleepy/awakening은 set_emotion 툴(VALID_EMOTIONS)엔 없다 — launcher.py의 idle_watcher가
# 시스템 판단으로만 트리거하는 상태라 모델이 직접 부를 수 없게 의도적으로 뺐다(CLOSE와 같은
# 부류). 여기서는 눈으로 확인할 수 있게 디버그용으로만 별도 허용.
DEBUG_ONLY_EMOTIONS = ("sleepy", "awakening")

MENU = f"""
──────────────────────────────
 감정 입력: {', '.join(VALID_EMOTIONS)}
 디버그 전용(모델은 못 부름): {', '.join(DEBUG_ONLY_EMOTIONS)}
 q) 종료
──────────────────────────────
"""


def input_worker(emotion_queue: queue.Queue, stop_event: threading.Event):
    print(MENU)
    while not stop_event.is_set():
        try:
            line = input("감정 > ").strip().lower()
        except EOFError:
            break
        if line in ("q", "quit", "exit"):
            stop_event.set()
            break
        elif line in VALID_EMOTIONS or line in DEBUG_ONLY_EMOTIONS:
            emotion_queue.put(line.upper())
        elif line:
            print(f"⚠️ 알 수 없는 감정: {line}")


def main():
    emotion_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    t_input = threading.Thread(target=input_worker, args=(emotion_queue, stop_event), name="input", daemon=True)
    t_input.start()

    app = RobotFaceApp(emotion_queue=emotion_queue, stop_event=stop_event)
    app.run()


if __name__ == "__main__":
    main()
