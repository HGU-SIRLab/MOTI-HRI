import os

from bootstrap import ensure_utf8_console

ensure_utf8_console()

try:
    import serial.tools.list_ports
except ImportError:
    print("⚠️ 'pyserial' 라이브러리가 필요합니다. 'pip install pyserial' 명령어로 설치해주세요.")
    serial = None

# ---- DXL Control Table ----
ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132
ADDR_GOAL_VELOCITY = 104
ADDR_PROFILE_ACCELERATION = 108


def find_dxl_port() -> str | None:
    """U2D2/FTDI 계열 시리얼 포트를 스캔해 자동으로 찾는다."""
    if serial is None:
        return None

    print("▶️  사용 가능한 시리얼 포트 검색 중...")
    ports = serial.tools.list_ports.comports()
    dxl_port = None

    for port in ports:
        print(f"  - 포트: {port.device}, 설명: {port.description}")
        if 'U2D2' in port.description or \
           'USB Serial Port' in port.description or \
           'FTDI' in port.description:
            dxl_port = port.device
            print(f"✅ 다이나믹셀 포트를 찾았습니다: {dxl_port}")
            break

    if dxl_port is None:
        print("⚠️  자동으로 다이나믹셀 포트를 찾지 못했습니다.")

    return dxl_port


_DEFAULT_PORT = "COM3"

MANUAL_PORT = os.getenv("DXL_PORT")
if MANUAL_PORT:
    print(f"ℹ️  .env에 지정된 포트({MANUAL_PORT})를 사용합니다.")
    DEVICENAME = MANUAL_PORT
else:
    DEVICENAME = find_dxl_port() or _DEFAULT_PORT

BAUDRATE = int(os.getenv("DXL_BAUD", "57600"))
PROTOCOL_VERSION = float(os.getenv("DXL_PROTO", "2.0"))

# ---- 팬/틸트 (얼굴 추적 전용) ----
PAN_ID, TILT_ID = 2, 9
SERVO_MIN, SERVO_MAX = 0, 4095
TILT_POS_MAX = 2040
PAN_SIGN = 1
TILT_SIGN = -1
KP_PAN, KP_TILT = 0.1, 0.1
KI_PAN, KI_TILT = 0.0, 0.0
KD_PAN, KD_TILT = 0.0, 0.0
DEAD_ZONE = 100
MAX_PIXEL_OFF = 200
PROFILE_VELOCITY = 100
MIN_MOVE_DELTA = 5

# ---- 바퀴 (속도 제어) ----
LEFT_ID, RIGHT_ID = 4, 3
LEFT_DIR, RIGHT_DIR = -1, +1
RPM_PER_UNIT = 0.229
BASE_RPM = float(os.getenv("BASE_RPM", "25.0"))
TURN_RPM = float(os.getenv("TURN_RPM", "25.0"))
VEL_MIN, VEL_MAX = -1023, +1023


def rpm_to_unit(rpm: float) -> int:
    return int(round(rpm / RPM_PER_UNIT))


BASE_SPEED_UNITS = rpm_to_unit(BASE_RPM)
TURN_SPEED_UNITS = rpm_to_unit(TURN_RPM)

# 춤 시퀀스 전용 회전 속도 (평상시 TURN_RPM의 2배 — TURN_RPM을 .env로 바꿔도 비율 유지)
DANCE_TURN_RPM = TURN_RPM * 2
DANCE_TURN_SPEED_UNITS = rpm_to_unit(DANCE_TURN_RPM)

# ---- 고개 끄덕임 ----
HEAD_NOD_ID = 1
HEAD_NOD_HOME_POS = 4000
HEAD_NOD_DOWN_POS = 3800
HEAD_NOD_MAX_POS = 4030

# ---- Layer 1 매크로용 관절 ----
# 물리 관절은 이 이름들이 정식 명칭이다. v1 코드에는 같은 관절에
# RPS_ARM_ID(=LEFT_ARM_ID)·DANCE_ID(=SHOULDER_ID) 같은 중복 별칭이 남아있었는데,
# 실측값 대조 결과 좌표 스케일이 동일한 하나의 물리 모터였다(docs/architecture.md §05).
SHOULDER_ID = 5
AUX_ID = 6
RIGHT_ARM_ID = 7
RIGHT_HAND_ID = 8
LEFT_ARM_ID = 11
LEFT_HAND_ID = 12

# 팬/틸트·바퀴 외 나머지 관절 — 안전 종료 시 토크 OFF 대상
EXTRA_POS_IDS = (HEAD_NOD_ID, SHOULDER_ID, AUX_ID, RIGHT_ARM_ID, RIGHT_HAND_ID, LEFT_ARM_ID, LEFT_HAND_ID)

# ---- 준비 자세 (HOME) ----
RIGHT_ARM_READY_POS = 3685
LEFT_ARM_READY_POS = 1402
RIGHT_HAND_READY_POS = 1823
LEFT_HAND_READY_POS = 1864
SHOULDER_CENTER_POS = 2073

# ---- 팔/손 동작 범위 (docs/architecture.md §05 Layer 2 안전범위와 동일 출처) ----
LEFT_ARM_UP_POS = 1000
SHOULDER_LEFT_POS = 2200
SHOULDER_RIGHT_POS = 1846
RIGHT_ARM_ACTION_POS = 3400
LEFT_ARM_ACTION_POS = 1700
RIGHT_HAND_ACTION_POS = 1500
LEFT_HAND_ACTION_POS = 1500
RIGHT_ARM_TOP_POS = 4050
LEFT_ARM_TOP_POS = 900
RIGHT_ARM_MIDDLE_POS = 3850
LEFT_ARM_MIDDLE_POS = 1100
RIGHT_ARM_DOWN_POS = 3644
LEFT_ARM_DOWN_POS = 1452
LEFT_HAND_WAVE_OUT_POS = 2350
RIGHT_HAND_WAVE_OUT_POS = 2275

# 춤 5·7단계에서 고개(=팬 모터)를 좌우로 돌리는 폭
HEAD_PAN_OFFSET = 400
