"""현재 모터 위치값을 읽어 MOTOR_HOME_POSITIONS 갱신용으로 출력하는 도구.

읽기 전용(쓰기 없음) — 토크가 꺼진 상태에서 사람이 손으로 자세를 잡은 뒤
그 위치를 새 홈 포지션으로 기록하고 싶을 때 사용한다.

사용:
    python scripts/read_positions.py
"""
import os
import sys

from dynamixel_sdk import PortHandler, PacketHandler

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from hardware import config as C
from hardware import dxl_io as io
from hardware import init as I


def main():
    port = PortHandler(C.DEVICENAME)
    pkt = PacketHandler(C.PROTOCOL_VERSION)
    if not port.openPort():
        raise RuntimeError(f"포트를 열 수 없습니다: {C.DEVICENAME}")
    if not port.setBaudRate(C.BAUDRATE):
        raise RuntimeError(f"보드레이트 설정 실패: {C.BAUDRATE}")
    print(f"✅ 포트 연결됨: {C.DEVICENAME} @ {C.BAUDRATE}")

    lock = __import__("threading").Lock()

    id_to_name = {
        C.HEAD_NOD_ID: "HEAD_NOD_ID",
        C.PAN_ID: "PAN_ID",
        C.SHOULDER_ID: "SHOULDER_ID",
        C.AUX_ID: "AUX_ID",
        C.RIGHT_ARM_ID: "RIGHT_ARM_ID",
        C.RIGHT_HAND_ID: "RIGHT_HAND_ID",
        C.TILT_ID: "TILT_ID",
        10: "10 (용도 불명)",
        C.LEFT_ARM_ID: "LEFT_ARM_ID",
        C.LEFT_HAND_ID: "LEFT_HAND_ID",
    }

    print("\n현재 위치값:\n")
    results = {}
    for motor_id in MOTOR_HOME_POSITIONS_KEYS():
        pos = io.read_present_position(pkt, port, lock, motor_id)
        results[motor_id] = pos
        name = id_to_name.get(motor_id, str(motor_id))
        print(f"  ID #{motor_id:02d} ({name}): {pos}")

    print("\nMOTOR_HOME_POSITIONS 갱신용 코드:\n")
    print("MOTOR_HOME_POSITIONS = {")
    for motor_id, pos in results.items():
        name = id_to_name.get(motor_id, str(motor_id))
        comment = f"  # {name}" if not name.startswith("C.") else ""
        key = f"C.{name}" if motor_id in (C.HEAD_NOD_ID, C.PAN_ID, C.SHOULDER_ID, C.AUX_ID, C.RIGHT_ARM_ID, C.RIGHT_HAND_ID, C.TILT_ID, C.LEFT_ARM_ID, C.LEFT_HAND_ID) else str(motor_id)
        print(f"    {key}: {pos},")
    print("}")

    port.closePort()


def MOTOR_HOME_POSITIONS_KEYS():
    return list(I.MOTOR_HOME_POSITIONS.keys())


if __name__ == "__main__":
    main()
