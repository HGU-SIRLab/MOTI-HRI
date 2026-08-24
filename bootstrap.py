"""레이어에 속하지 않는 최상위 유틸리티. hardware/든 core/든 어디서나 가져다 써도
계층 구조(§02, hardware↔core 상호 미참조)를 어기지 않는다.
"""
import os
import sys

from dotenv import load_dotenv

# hardware/config.py, core/report_manager.py 등은 모듈 임포트 시점에 os.getenv(...)로
# 설정값을 읽는다. 각 스크립트가 알아서 load_dotenv()를 호출하되 그 시점이 이미 저
# import 뒤라면(실제로 그래왔다), .env의 값이 조용히 무시된다 — 이 파일은 항상
# 다른 무엇보다도 먼저 import되므로, 여기서 한 번만 로드하면 그 문제가 통째로 사라진다.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def ensure_utf8_console():
    """Windows 기본 콘솔(cp949)은 이모지를 인코딩하지 못해 print()가 그대로
    크래시한다. 프로세스 시작 시 한 번만 호출하면 된다."""
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "encoding", "").lower() != "utf-8":
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 플랫폼 판별 / 리눅스(Jetson) 이식 지원 (2026-08-24, jetson-moti 브랜치)
#
# 이 코드베이스는 Windows에서만 돌아왔다. Jetson Orin Nano로 옮기면서 갈리는 지점이
# 네 군데 있다 — 카메라 백엔드(vision/camera.py), 시리얼 포트 이름(hardware/config.py),
# ONNX 실행 프로바이더(vision/vision_brain.py), multiprocessing 시작 방식(아래).
# 각 모듈이 제각각 os.name을 보지 않도록 판별을 여기 한 곳에 모은다.
# ---------------------------------------------------------------------------
import multiprocessing
import platform as _platform

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_ARM64 = _platform.machine().lower() in ("aarch64", "arm64")


def device_tree_model() -> str:
    """리눅스 SBC의 보드 이름을 그대로 돌려준다(Jetson이면 'NVIDIA Jetson Orin Nano
    Developer Kit' 같은 문자열). 일반 PC나 Windows에서는 빈 문자열.
    판정보다는 로그용 — scripts/jetson_doctor.py가 이 값을 그대로 출력해서
    "지금 이게 무슨 보드인지"를 실물에서 눈으로 확인하게 한다."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode("utf-8", "replace").replace("\x00", "").strip()
    except OSError:
        return ""


def is_jetson() -> bool:
    """NVIDIA Jetson(L4T) 위에서 돌고 있는가.

    /etc/nv_tegra_release는 L4T(JetPack) 이미지에만 있는 파일이라 이것 하나로 거의
    확정된다. 커스텀 루트파일시스템 등으로 그 파일이 없을 때를 대비해 보드 이름도 본다."""
    if not IS_LINUX:
        return False
    if os.path.exists("/etc/nv_tegra_release"):
        return True
    return "jetson" in device_tree_model().lower() or "tegra" in device_tree_model().lower()


def ensure_spawn_start_method():
    """multiprocessing 시작 방식을 spawn으로 고정한다. 자식 프로세스를 만들기 전에,
    프로세스당 한 번만 부르면 된다.

    왜 필요한가: 이 코드는 Windows(=spawn 고정)에서만 검증됐는데 리눅스 기본값은 fork다.
    launcher.py는 CUDA/ONNX 세션(RobotBrain), pygame 얼굴 창, 열린 시리얼 포트,
    돌아가는 스레드 여러 개를 **다 띄운 뒤에** 퀴즈 사진 창 프로세스를 만든다(launcher.py
    main() 참고). fork는 그 시점의 메모리를 통째로 복제하면서 다른 스레드가 쥐고 있던 락은
    잠긴 채로 가져가고, CUDA 컨텍스트는 자식에서 못 쓰는 상태가 된다 — 자식이 그 자리에서
    멈추거나(퀴즈 사진이 안 뜸) 죽는다.

    즉 이건 리눅스용 새 동작이 아니라, Windows에서 이미 검증된 동작을 리눅스에서도
    그대로 유지하기 위한 고정이다."""
    if multiprocessing.get_start_method(allow_none=True) == "spawn":
        return
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError as e:
        # 이미 다른 방식으로 컨텍스트가 굳은 뒤에 불린 경우 — 조용히 넘어가면
        # 위에 적은 fork 사고를 그대로 당하므로 눈에 띄게 알린다.
        print(f"⚠️ multiprocessing 시작 방식을 spawn으로 고정하지 못했습니다: {e}")


def env_flag(name: str, default: bool = False) -> bool:
    """.env의 불리언 값을 읽는다. 코드베이스 곳곳에 흩어져 있던
    `os.getenv(...).lower() not in ("0","false","no")` 관용구와 같은 규칙."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# 파이썬 버전 호환 (2026-08-24, Ubuntu 22.04 / Python 3.10 대응)
#
# 개발 PC는 Python 3.11이고 Ubuntu 22.04(JetPack 6 계열)의 기본 파이썬은 3.10이다.
# 전 소스를 3.10 문법으로 파싱해 본 결과 위반은 0건이었고, 걸리는 건 API 하나뿐이다:
# `asyncio.timeout()`은 3.11에 추가됐다(scripts/test_quiz_live.py에서 사용).
# 젯슨에서 그 테스트를 돌리면 AttributeError로 죽으므로 대체 구현을 둔다.
# ---------------------------------------------------------------------------
class _Timeout310:
    """3.10용 `asyncio.timeout()` 대체. 타이머가 만료되면 현재 태스크를 취소하고,
    그 취소를 TimeoutError로 바꿔서 내보낸다.

    한계: 3.11 본판이 하는 취소 부기(uncancel) 처리는 없다. 중첩해서 쓰거나 바깥에서
    들어온 취소와 정교하게 구분해야 하는 상황에는 부족하다 — 지금 쓰이는 곳은
    테스트 스크립트의 단순한 대기 루프뿐이라 이 정도면 충분하다."""

    def __init__(self, delay: float):
        self._delay = delay
        self._handle = None
        self._task = None
        self._expired = False

    async def __aenter__(self):
        import asyncio
        self._task = asyncio.current_task()
        self._handle = asyncio.get_running_loop().call_later(self._delay, self._on_timeout)
        return self

    def _on_timeout(self):
        self._expired = True
        if self._task is not None:
            self._task.cancel()

    async def __aexit__(self, exc_type, exc, tb):
        import asyncio
        if self._handle is not None:
            self._handle.cancel()
        # 우리 타이머가 건 취소일 때만 TimeoutError로 바꾼다. 바깥에서 들어온 취소는
        # 그대로 통과시켜야 Ctrl+C나 상위 태스크 종료가 정상 동작한다.
        if self._expired and exc_type is asyncio.CancelledError:
            raise TimeoutError from None
        return False


def async_timeout(delay: float):
    """`async with async_timeout(3): ...` — asyncio.timeout(3)과 같은 뜻이되
    Python 3.10에서도 동작한다. 3.11 이상에서는 표준 구현을 그대로 쓴다."""
    import asyncio
    if hasattr(asyncio, "timeout"):
        return asyncio.timeout(delay)
    return _Timeout310(delay)
