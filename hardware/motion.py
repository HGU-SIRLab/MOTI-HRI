import math
import os
import threading
import time

from dynamixel_sdk import PacketHandler, PortHandler

from hardware import config as C
from hardware import dxl_io as io
from hardware import wheel

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_MUSIC_FILE = os.path.join(_ASSETS_DIR, "SODA_POP.mp3")
_MUSIC_START_SECONDS = 55
DANCE_DURATION = 40  # play_dance()가 끝날 때까지 걸리는 대략적인 시간(초)

_mixer_ready = False


def _ensure_mixer():
    global _mixer_ready
    if _mixer_ready:
        return
    import pygame
    pygame.mixer.init()
    _mixer_ready = True


def perform_head_nod(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict, repetitions=2):
    """고개를 부드럽게 끄덕이는 독립 실행 함수. 실행 중 얼굴추적을 잠시 멈춘다."""
    print(f"🤖 고개 끄덕이기 {repetitions}회 시작... (Face Tracking 일시 정지)")

    if shared_state:
        shared_state['mode'] = 'nodding'
        time.sleep(0.1)

    home_pos = C.HEAD_NOD_HOME_POS
    down_pos = C.HEAD_NOD_DOWN_POS
    accel_value = 30
    velocity_value = 300
    default_velocity = 100
    nod_wait_time = 0.3
    nod_wait_time_up = 0.4

    try:
        with lock:
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_ACCELERATION, 20)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, home_pos)

        time.sleep(0.2)

        with lock:
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_VELOCITY, velocity_value)

        for i in range(repetitions):
            with lock:
                io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, down_pos)
            time.sleep(nod_wait_time)
            with lock:
                io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, home_pos)
            time.sleep(nod_wait_time_up)
    finally:
        with lock:
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_PROFILE_VELOCITY, default_velocity)
            io.write4(pkt, port, C.HEAD_NOD_ID, C.ADDR_GOAL_POSITION, home_pos)
        if shared_state:
            shared_state['mode'] = 'tracking'
        print("✅ 고개 끄덕이기 완료! (Face Tracking 재개)")


def play_greeting_motion(port: PortHandler, pkt: PacketHandler, lock: threading.Lock):
    """왼손을 흔들며 인사하는 동작."""
    print("🤖 [행동] 인사 동작 시작...")
    acceleration_value = 30
    arm_speed = 500
    hand_speed = 600

    try:
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, acceleration_value)

        with lock:
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)

        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_UP_POS)
        time.sleep(1.2)

        for _ in range(2):
            with lock:
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            time.sleep(0.5)
            with lock:
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)
            time.sleep(0.5)

        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
        with lock:
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        time.sleep(0.7)

        print("✅ [행동] 인사 동작 완료.")
    finally:
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_ACCELERATION, 0)


def play_hug_motion(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, hold_seconds: float = 10.0):
    """양팔을 벌려 포옹 자세를 취하고 hold_seconds 동안 유지한다."""
    print("🤖 [행동] 포옹 동작 시작...")
    accel_value = 30
    arm_speed = 100

    try:
        with lock:
            for motor_id in (C.RIGHT_ARM_ID, C.LEFT_ARM_ID, C.RIGHT_HAND_ID, C.LEFT_HAND_ID):
                io.write4(pkt, port, motor_id, C.ADDR_PROFILE_ACCELERATION, accel_value)
                io.write4(pkt, port, motor_id, C.ADDR_PROFILE_VELOCITY, arm_speed)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_WAVE_OUT_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_WAVE_OUT_POS)

        time.sleep(hold_seconds)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        time.sleep(1.5)

        print("✅ [행동] 포옹 동작 완료.")
    finally:
        with lock:
            for motor_id in (C.RIGHT_ARM_ID, C.LEFT_ARM_ID, C.RIGHT_HAND_ID, C.LEFT_HAND_ID):
                io.write4(pkt, port, motor_id, C.ADDR_PROFILE_ACCELERATION, 0)


def play_shy_motion(port: PortHandler, pkt: PacketHandler, lock: threading.Lock):
    """팔을 모으고 몸을 살짝 좌우로 트는 부끄러움 동작. 바퀴를 잠깐 움직인다."""
    print("🤖 [행동] 부끄부끄 동작 시작...")
    accel_value = 40
    arm_speed = 150
    hand_speed = 150
    wheel_speed = C.TURN_SPEED_UNITS
    shoulder_wiggle_count = 2
    shoulder_wait = 0.8

    try:
        with lock:
            for motor_id in (C.RIGHT_ARM_ID, C.LEFT_ARM_ID, C.RIGHT_HAND_ID, C.LEFT_HAND_ID):
                io.write4(pkt, port, motor_id, C.ADDR_PROFILE_ACCELERATION, accel_value)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, hand_speed)

            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_ACTION_POS)

        # 오른쪽으로 살짝 이동 (몸 비틀기)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * wheel_speed)
        time.sleep(0.5)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(1.0)

        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 100)

        for _ in range(shoulder_wiggle_count):
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
            time.sleep(shoulder_wait)
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_RIGHT_POS)
            time.sleep(shoulder_wait)

        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.5)

        # 왼쪽으로 이동해 원위치 복귀
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * wheel_speed)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * wheel_speed)
        time.sleep(0.5)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
        time.sleep(1.0)

        print("✅ [행동] 부끄부끄 동작 완료.")
    finally:
        with lock:
            for motor_id in (C.RIGHT_ARM_ID, C.LEFT_ARM_ID, C.RIGHT_HAND_ID, C.LEFT_HAND_ID):
                io.write4(pkt, port, motor_id, C.ADDR_PROFILE_ACCELERATION, 0)


def _perform_shoulder_dance(port: PortHandler, pkt: PacketHandler, lock, duration_sec: float, frequency_hz: float, title: str):
    """사인파로 어깨를 흔드는 헬퍼. play_dance()의 오프닝/피날레에서 사용."""
    print(f"🎶 {title} 시작! ({duration_sec}초, {frequency_hz}Hz)")
    amplitude = C.SHOULDER_LEFT_POS - C.SHOULDER_CENTER_POS

    with lock:
        io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 250)

    t0 = time.time()
    while time.time() - t0 <= duration_sec:
        t = time.time() - t0
        offset = amplitude * math.sin(2.0 * math.pi * frequency_hz * t)
        goal_pos = int(round(C.SHOULDER_CENTER_POS + offset))
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, goal_pos)
        time.sleep(0.02)

    with lock:
        io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
    time.sleep(0.5)
    print(f"✅ {title} 완료!")


def _dance_routine(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict,
                    home_pan: int, home_tilt: int, emotion_queue=None):
    try:
        print("🤖 [춤 준비] 얼굴 추적 중지 및 고개 정렬")
        shared_state['mode'] = 'dancing'
        with lock:
            io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, home_pan)
            io.write4(pkt, port, C.TILT_ID, C.ADDR_GOAL_POSITION, home_tilt)
        time.sleep(0.5)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 30)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 30)

        _ensure_mixer()
        import pygame
        pygame.mixer.music.load(_MUSIC_FILE)
        print(f"{_MUSIC_START_SECONDS}초부터 {DANCE_DURATION}초 동안 음악을 재생합니다.")
        pygame.mixer.music.play(start=_MUSIC_START_SECONDS)
        threading.Timer(DANCE_DURATION, pygame.mixer.music.stop).start()

        _perform_shoulder_dance(port, pkt, lock, duration_sec=8.0, frequency_hz=0.5, title="오프닝 어깨 춤")
        _perform_shoulder_dance(port, pkt, lock, duration_sec=4.5, frequency_hz=1, title="고조되는 어깨 춤")
        time.sleep(0.25)

        # [1단계] 몸 전체 왼쪽 회전
        print("🤖 [안무 1단계] 몸 전체 왼쪽 회전")
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * C.DANCE_TURN_SPEED_UNITS)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * C.DANCE_TURN_SPEED_UNITS)
        time.sleep(0.3)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.25)

        # [2단계] 왼팔 들기
        print("🤖 [안무 2단계] 왼팔 들기")
        with lock:
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 600)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_UP_POS)
        time.sleep(0.35)
        time.sleep(0.25)

        # [3단계] 왼쪽 어깨 들었다 내리기
        print("🤖 [안무 3단계] 왼쪽 어깨 들기")
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 500)
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
        time.sleep(0.25)
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.25)
        time.sleep(0.25)

        # [4단계] 회전 후 팔 모으기
        print("🤖 [안무 4단계] 회전 후 팔 모으기")
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * C.DANCE_TURN_SPEED_UNITS)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * C.DANCE_TURN_SPEED_UNITS)
        time.sleep(0.3)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.15)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 800)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 800)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_MIDDLE_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_MIDDLE_POS)
        time.sleep(0.35)

        with lock:
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_ACTION_POS)
        time.sleep(0.5)

        with lock:
            # 손 모터 부담을 덜기 위해 잠깐 토크를 뺀다
            io.write1(pkt, port, C.LEFT_HAND_ID, C.ADDR_TORQUE_ENABLE, 0)
            io.write1(pkt, port, C.RIGHT_HAND_ID, C.ADDR_TORQUE_ENABLE, 0)
        time.sleep(0.25)

        # [5단계] 스텝 & 팔 동작
        print("🤖 [안무 5단계] 스텝 및 팔 동작")
        step_speed = C.DANCE_TURN_SPEED_UNITS
        step_duration = 0.15
        for r_speed, l_speed in ((-step_speed, -step_speed), (step_speed, step_speed),
                                  (step_speed, step_speed), (-step_speed, -step_speed)):
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, r_speed)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, l_speed)
            time.sleep(step_duration)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * C.DANCE_TURN_SPEED_UNITS)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * C.DANCE_TURN_SPEED_UNITS)
        time.sleep(0.6)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.25)

        with lock:
            io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, 400)
            io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, home_pan - C.HEAD_PAN_OFFSET)
        time.sleep(0.25)

        arm_speed, arm_wait = 800, 0.3
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
        time.sleep(arm_wait)
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_MIDDLE_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_MIDDLE_POS)
        time.sleep(arm_wait)
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait)
        time.sleep(0.25)

        # [6단계] 만세 동작
        print("🤖 [안무 6단계] 만세 동작")
        arm_speed, arm_wait = 1000, 0.3
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_TOP_POS)
        time.sleep(arm_wait)
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait)
        time.sleep(0.25)

        # [7단계] 어깨 춤
        print("🤖 [안무 7단계] 어깨 춤")
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_PROFILE_VELOCITY, 400)
        for _ in range(3):
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_RIGHT_POS)
            time.sleep(0.3)
            with lock:
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_LEFT_POS)
            time.sleep(0.3)
        with lock:
            io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
        time.sleep(0.25)

        with lock:
            io.write4(pkt, port, C.PAN_ID, C.ADDR_PROFILE_VELOCITY, 400)
            io.write4(pkt, port, C.PAN_ID, C.ADDR_GOAL_POSITION, home_pan)
        time.sleep(0.25)

        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * C.DANCE_TURN_SPEED_UNITS)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * C.DANCE_TURN_SPEED_UNITS)
        time.sleep(0.6)
        wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
        wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 1000)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, 1000)
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_TOP_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(0.25)

        # [8단계] 마무리 동작
        print("🤖 [안무 8단계] 마무리 동작")
        arm_speed, arm_wait = 600, 0.25
        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_VELOCITY, arm_speed)

        for right_pos, left_pos in ((C.RIGHT_ARM_DOWN_POS, C.LEFT_ARM_TOP_POS),
                                     (C.RIGHT_ARM_TOP_POS, C.LEFT_ARM_DOWN_POS),
                                     (C.RIGHT_ARM_DOWN_POS, C.LEFT_ARM_TOP_POS)):
            with lock:
                io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, left_pos)
                io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, right_pos)
            time.sleep(arm_wait)

        with lock:
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_PROFILE_VELOCITY, 600)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_ACTION_POS)
        time.sleep(0.35)

        twist_duration, twist_speed = 0.15, C.DANCE_TURN_SPEED_UNITS
        for _ in range(2):
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, -C.RIGHT_DIR * twist_speed)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, C.LEFT_DIR * twist_speed)
            time.sleep(twist_duration)
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, C.RIGHT_DIR * twist_speed)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, -C.LEFT_DIR * twist_speed)
            time.sleep(twist_duration)
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
        time.sleep(0.5)

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_DOWN_POS)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_DOWN_POS)
        time.sleep(arm_wait)

        print("☕ [숨 고르기] 다음 동작 전 1.5초 휴식...")
        time.sleep(1.5)
        _perform_shoulder_dance(port, pkt, lock, duration_sec=11.0, frequency_hz=1, title="피날레 어깨 춤")
        time.sleep(0.25)

        print("🤖 [마무리 준비] 양손 토크 ON 및 자세 복귀")
        with lock:
            io.write1(pkt, port, C.LEFT_HAND_ID, C.ADDR_TORQUE_ENABLE, 1)
            io.write1(pkt, port, C.RIGHT_HAND_ID, C.ADDR_TORQUE_ENABLE, 1)
            io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
            io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
        time.sleep(0.5)

    finally:
        # 음악 정지 자체가 실패해도(예: 믹서 초기화 전 조기 예외) 아래의 모드 복구/자세
        # 복귀는 반드시 실행되어야 한다 — 로봇이 'dancing' 모드에 갇히는 걸 막기 위함.
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception as e:
            print(f"⚠️ 음악 정지 중 오류(무시하고 계속 진행): {e}")

        with lock:
            io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
            io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_PROFILE_ACCELERATION, 0)
        shared_state['mode'] = 'tracking'
        if emotion_queue:
            emotion_queue.put("NEUTRAL")
        print("🎉 춤 시퀀스 종료! 얼굴 추적 모드로 복귀합니다.")

        try:
            with lock:
                io.write4(pkt, port, C.RIGHT_ARM_ID, C.ADDR_GOAL_POSITION, C.RIGHT_ARM_READY_POS)
                io.write4(pkt, port, C.LEFT_ARM_ID, C.ADDR_GOAL_POSITION, C.LEFT_ARM_READY_POS)
                io.write4(pkt, port, C.RIGHT_HAND_ID, C.ADDR_GOAL_POSITION, C.RIGHT_HAND_READY_POS)
                io.write4(pkt, port, C.LEFT_HAND_ID, C.ADDR_GOAL_POSITION, C.LEFT_HAND_READY_POS)
                io.write4(pkt, port, C.SHOULDER_ID, C.ADDR_GOAL_POSITION, C.SHOULDER_CENTER_POS)
            wheel.set_wheel_speed(pkt, port, lock, C.RIGHT_ID, 0)
            wheel.set_wheel_speed(pkt, port, lock, C.LEFT_ID, 0)
            time.sleep(1.0)
            print("✅ 모든 모터 원위치 복귀 완료.")
        except Exception as e:
            print(f"⚠️ 춤 종료 후 모터 원위치 복귀 중 오류: {e}")


_dance_running = threading.Event()


def _dance_routine_guarded(*args, **kwargs):
    try:
        _dance_routine(*args, **kwargs)
    finally:
        _dance_running.clear()


def is_dancing() -> bool:
    return _dance_running.is_set()


def play_dance(port: PortHandler, pkt: PacketHandler, lock: threading.Lock, shared_state: dict,
                home_pan: int, home_tilt: int, emotion_queue=None) -> int:
    """춤 시퀀스를 백그라운드 스레드로 시작하고, 예상 소요 시간(초)을 즉시 반환한다.
    이미 춤을 추는 중이면 무시한다 — 음악 재생과 안무 스레드가 겹치는 걸 막기 위함."""
    if _dance_running.is_set():
        print("⚠️ 이미 춤을 추는 중입니다 — 새 요청을 무시합니다.")
        return 0

    _dance_running.set()
    threading.Thread(
        target=_dance_routine_guarded,
        args=(port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue),
        daemon=True,
    ).start()
    return DANCE_DURATION


# ---- Layer 1 진입점 — docs/architecture.md §05 play_manual_motion(name) ----
# "wave"는 v1에 별도 함수가 없고 play_greeting_motion과 동일한 손 흔들기 동작이라 별칭으로 둔다.
_BLOCKING_MOTIONS = {
    "hug": play_hug_motion,
    "greeting": play_greeting_motion,
    "wave": play_greeting_motion,
    "shy": play_shy_motion,
}


def play_manual_motion(name: str, port: PortHandler, pkt: PacketHandler, lock: threading.Lock,
                        shared_state: dict, home_pan: int = 2081, home_tilt: int = 2071, emotion_queue=None):
    """이름으로 매뉴얼 매크로를 실행한다. "dance"는 스레드를 띄우고 소요 시간을 반환,
    나머지는 동작이 끝날 때까지 블로킹한다. 알 수 없는 이름은 아무 동작도 하지 않는다(Layer 3 폴백)."""
    if name == "dance":
        return play_dance(port, pkt, lock, shared_state, home_pan, home_tilt, emotion_queue)

    motion_fn = _BLOCKING_MOTIONS.get(name)
    if motion_fn is None:
        print(f"⚠️ 알 수 없는 매크로 '{name}' — 아무 동작도 하지 않습니다.")
        return None

    motion_fn(port, pkt, lock)
    return None
