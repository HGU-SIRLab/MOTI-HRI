from dynamixel_sdk import PortHandler, PacketHandler
from hardware import config as C
from hardware import dxl_io as io
import time

# 팬/틸트·바퀴를 제외한 관절의 초기 목표 위치. v1/v2 실측값 그대로.
# ID 10은 config.py 어디에도 이름이 없는 모터지만 v1/v2 모두 이 값(1001)으로
# 초기화해왔다 — 용도가 불명확해도 미초기화 상태로 남기지 않기 위해 그대로 유지.
MOTOR_HOME_POSITIONS = {
    C.HEAD_NOD_ID: 3862,
    C.PAN_ID: 2081,
    C.SHOULDER_ID: 2089,
    C.AUX_ID: 2216,
    C.RIGHT_ARM_ID: 3666,
    C.RIGHT_HAND_ID: 1906,
    C.TILT_ID: 2071,
    10: 1001,
    C.LEFT_ARM_ID: 1425,
    C.LEFT_HAND_ID: 1956,
}


def initialize_robot(port: PortHandler, pkt: PacketHandler, lock):
    """모든 모터를 초기화하고 지정된 위치 및 모드로 설정하는 통합 함수"""
    print("▶️  모든 모터 초기화 및 지정 위치로 이동 시작...")

    with lock:
        # 1. 관절 모터 (위치 제어 모드)
        for motor_id, home_pos in MOTOR_HOME_POSITIONS.items():
            io.write1(pkt, port, motor_id, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, motor_id, C.ADDR_OPERATING_MODE, 3)
            io.write4(pkt, port, motor_id, C.ADDR_PROFILE_VELOCITY, 100)
            io.write1(pkt, port, motor_id, C.ADDR_TORQUE_ENABLE, 1)
            io.write4(pkt, port, motor_id, C.ADDR_GOAL_POSITION, home_pos)
            print(f"  [INIT] 모터 ID #{motor_id:02d} -> 목표 위치 {home_pos}로 이동 명령")

        # 2. 바퀴 모터 (속도 제어 모드)
        print("▶️  바퀴 모터를 속도 제어(Velocity) 모드로 변경합니다...")
        for dxl_id in (C.LEFT_ID, C.RIGHT_ID):
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, dxl_id, C.ADDR_OPERATING_MODE, 1)
            io.write1(pkt, port, dxl_id, C.ADDR_TORQUE_ENABLE, 1)

    print("▶️  모터가 초기 위치로 이동 중... (3초 대기)")
    time.sleep(3)
    print("✅ 모든 모터 초기화 완료!")


def stop_all_wheels(pkt: PacketHandler, port: PortHandler, lock):
    """시스템 종료 시 바퀴 모터 정지용 함수"""
    from . import wheel
    wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
    wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)


def shutdown_all_motors(port: PortHandler, pkt: PacketHandler, lock):
    """모든 모터의 토크를 끈다 — 프로세스 종료 시 호출."""
    stop_all_wheels(pkt, port, lock)
    with lock:
        for motor_id in (*C.EXTRA_POS_IDS, C.PAN_ID, C.TILT_ID, C.LEFT_ID, C.RIGHT_ID, 10):
            io.write1(pkt, port, motor_id, C.ADDR_TORQUE_ENABLE, 0)
    print("■ 모든 모터 토크 OFF")
