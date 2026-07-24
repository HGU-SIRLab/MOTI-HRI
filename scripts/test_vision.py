"""얼굴추적(vision/face.py) 독립 테스트 도구.

로봇과 카메라를 연결한 뒤 실행하면 팬/틸트 모터가 얼굴을 따라 움직인다.
대화 엔진 없이 추적 동작만 검증하는 용도 — scripts/test_motions.py와 같은 패턴.
ESC 또는 Ctrl+C로 종료.

사용:
    python scripts/test_vision.py [카메라 인덱스]
"""
import os
import sys
import threading
import queue

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from dynamixel_sdk import PortHandler, PacketHandler

from hardware import config as C
from hardware import init as I
from vision import face as F


def open_port() -> tuple[PortHandler, PacketHandler]:
    port = PortHandler(C.DEVICENAME)
    pkt = PacketHandler(C.PROTOCOL_VERSION)
    if not port.openPort():
        raise RuntimeError(f"포트를 열 수 없습니다: {C.DEVICENAME}")
    if not port.setBaudRate(C.BAUDRATE):
        raise RuntimeError(f"보드레이트 설정 실패: {C.BAUDRATE}")
    print(f"✅ 포트 연결됨: {C.DEVICENAME} @ {C.BAUDRATE}")
    return port, pkt


def main():
    camera_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    port, pkt = open_port()
    lock = threading.Lock()
    stop_event = threading.Event()
    video_frame_q = queue.Queue(maxsize=1)
    shared_state = {"mode": "tracking"}

    try:
        I.initialize_robot(port, pkt, lock)

        t_face = threading.Thread(
            target=F.face_tracker_worker,
            args=(port, pkt, lock, stop_event, video_frame_q, shared_state),
            kwargs=dict(camera_index=camera_index, draw_mesh=True, print_debug=True),
            name="face", daemon=True)
        t_face.start()
        print(f"▶ FaceTracker 시작 (camera_index={camera_index}) — ESC 또는 Ctrl+C로 종료")

        F.display_loop_main_thread(stop_event, window_name="Face Tracking Test")
    except KeyboardInterrupt:
        print("\n🛑 KeyboardInterrupt 감지 → 종료 신호 보냄")
    finally:
        stop_event.set()
        print("▶️  종료 — 모든 모터 토크 OFF")
        I.shutdown_all_motors(port, pkt, lock)
        port.closePort()


if __name__ == "__main__":
    main()
