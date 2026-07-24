"""Layer 1 매크로 독립 테스트 도구.

로봇을 시리얼 포트에 연결한 뒤 실행하면 홈 포지션으로 초기화하고,
번호를 입력해 각 매크로를 하나씩 눌러볼 수 있다. 대화 엔진 없이
hardware/motion.py의 동작만 검증하는 용도.

사용:
    python scripts/test_motions.py
"""
import os
import sys
import threading
import time

from dynamixel_sdk import PortHandler, PacketHandler

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from hardware import config as C
from hardware import init as I
from hardware import motion as M

MENU = """
──────────────────────────────
 1) greeting  (인사)
 2) hug       (포옹, 10초 유지)
 3) shy       (부끄부끄)
 4) dance     (춤, 약 {duration}초 — 백그라운드 재생)
 5) head_nod  (고개 끄덕임)
 q) 종료
──────────────────────────────
""".format(duration=M.DANCE_DURATION)


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
    port, pkt = open_port()
    lock = threading.Lock()
    shared_state = {"mode": "tracking"}

    try:
        I.initialize_robot(port, pkt, lock)
        home_pan = I.MOTOR_HOME_POSITIONS.get(C.PAN_ID, 2081)
        home_tilt = I.MOTOR_HOME_POSITIONS.get(C.TILT_ID, 2071)

        while True:
            print(MENU)
            choice = input("동작 선택 > ").strip().lower()

            if choice in ("q", "quit", "exit"):
                break
            elif choice == "1":
                M.play_manual_motion("greeting", port, pkt, lock, shared_state)
            elif choice == "2":
                M.play_manual_motion("hug", port, pkt, lock, shared_state)
            elif choice == "3":
                M.play_manual_motion("shy", port, pkt, lock, shared_state)
            elif choice == "4":
                eta = M.play_manual_motion("dance", port, pkt, lock, shared_state, home_pan, home_tilt)
                print(f"🎵 춤 시작됨 — 약 {eta}초간 재생됩니다 (백그라운드).")
            elif choice == "5":
                M.perform_head_nod(port, pkt, lock, shared_state)
            else:
                print("⚠️ 알 수 없는 입력입니다.")
    finally:
        if M.is_dancing():
            print("⏳ 춤이 끝날 때까지 대기 중... (강제 종료하면 로봇이 자세를 복구하지 못합니다)")
            deadline = time.monotonic() + M.DANCE_DURATION + 5
            while M.is_dancing() and time.monotonic() < deadline:
                time.sleep(0.5)
        print("▶️  종료 — 모든 모터 토크 OFF")
        I.shutdown_all_motors(port, pkt, lock)
        port.closePort()


if __name__ == "__main__":
    main()
