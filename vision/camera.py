"""카메라 열기 — 플랫폼별 백엔드 차이를 여기서 흡수한다(2026-08-24, jetson-moti).

기존 코드는 `cv2.VideoCapture(index, cv2.CAP_DSHOW)`를 직접 불렀다. DSHOW(DirectShow)는
Windows 전용 백엔드라 Jetson(리눅스)에서는 그대로 두면 카메라가 열리지 않는다. 그렇다고
백엔드 인자를 빼버리면 Windows에서 지금 잘 되던 것이 느려지거나(MSMF 폴백) 첫 프레임까지
몇 초씩 걸리는 회귀가 나므로, **플랫폼별로 갈라주는 함수 하나**로 통일한다.

지원하는 경로 세 가지:
  - dshow     : Windows 기본. 지금까지의 동작 그대로.
  - v4l2      : 리눅스에서 USB 웹캠. 지금 로봇에 달린 카메라가 USB면 이쪽.
  - gstreamer : CSI 카메라(nvarguscamerasrc) 또는 직접 짠 파이프라인.

USB 웹캠을 리눅스에서 쓸 때 흔히 겪는 함정 하나를 기본값으로 처리해 둔다: V4L2는 기본
포맷이 YUYV(무압축)라 1280x720이면 USB 대역폭이 모자라 5~10fps로 떨어진다. MJPG로
바꿔야 30fps가 나온다 — 그래서 리눅스에서는 FOURCC를 MJPG로 먼저 요청한다(카메라가
MJPG를 지원 안 하면 요청이 무시될 뿐 실패하지는 않는다).
"""
from __future__ import annotations

import os

from bootstrap import IS_LINUX, IS_WINDOWS, is_jetson

# 지금까지 face.py가 하드코딩하고 있던 값 — 기본값을 바꾸지 않는다.
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", str(DEFAULT_WIDTH)))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", str(DEFAULT_HEIGHT)))
CAMERA_FPS = int(os.getenv("CAMERA_FPS", "30"))
# auto | dshow | v4l2 | gstreamer | any
CAMERA_BACKEND = os.getenv("CAMERA_BACKEND", "auto").strip().lower()
# 값이 있으면 그 문자열을 GStreamer 파이프라인으로 그대로 쓴다(백엔드 자동판정보다 우선).
CAMERA_GST_PIPELINE = os.getenv("CAMERA_GST_PIPELINE", "").strip()
# 리눅스에서 요청할 FOURCC. 빈 문자열이면 요청하지 않는다.
CAMERA_FOURCC = os.getenv("CAMERA_FOURCC", "MJPG" if IS_LINUX else "").strip().upper()
# CSI 카메라(nvarguscamerasrc)의 flip-method. 카메라를 거꾸로 달았으면 2.
CSI_FLIP_METHOD = int(os.getenv("CSI_FLIP_METHOD", "0"))


def csi_pipeline(sensor_id: int = 0, width: int = None, height: int = None,
                 fps: int = None, flip_method: int = None) -> str:
    """Jetson CSI 카메라용 GStreamer 파이프라인 문자열.

    ⚠️ 이 경로는 **OpenCV가 GStreamer 지원으로 빌드되어 있어야** 동작한다. JetPack의
    apt 패키지(python3-opencv)는 지원하지만 pip의 opencv-python 휠은 지원하지 않는다 —
    scripts/jetson_doctor.py가 실물에서 이걸 확인해 준다(cv2.getBuildInformation()의
    'GStreamer: YES').
    """
    width = width or CAMERA_WIDTH
    height = height or CAMERA_HEIGHT
    fps = fps or CAMERA_FPS
    flip_method = CSI_FLIP_METHOD if flip_method is None else flip_method
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), width=(int){width}, height=(int){height}, "
        f"framerate=(fraction){fps}/1 ! "
        f"nvvidconv flip-method={flip_method} ! "
        f"video/x-raw, width=(int){width}, height=(int){height}, format=(string)BGRx ! "
        f"videoconvert ! video/x-raw, format=(string)BGR ! "
        f"appsink drop=true max-buffers=1"
    )


def _resolve_backend() -> str:
    if CAMERA_GST_PIPELINE:
        return "gstreamer"
    if CAMERA_BACKEND != "auto":
        return CAMERA_BACKEND
    if IS_WINDOWS:
        return "dshow"
    if IS_LINUX:
        # Jetson이라도 USB 웹캠이 훨씬 흔하므로 v4l2를 기본으로 둔다. CSI 카메라를 쓰려면
        # .env에 CAMERA_BACKEND=gstreamer(또는 CAMERA_GST_PIPELINE=...)를 지정할 것.
        return "v4l2"
    return "any"


def open_capture(camera_index: int = 0, *, width: int = None, height: int = None,
                 verbose: bool = True):
    """VideoCapture를 열어서 돌려준다. 열기 실패해도 예외를 던지지 않는다 —
    호출부가 지금까지 하던 대로 `cap.isOpened()`로 판정하면 된다."""
    from core import suppress
    cv2, _ = suppress.import_cv2_mp()

    width = width or CAMERA_WIDTH
    height = height or CAMERA_HEIGHT
    backend = _resolve_backend()

    if backend == "gstreamer":
        pipeline = CAMERA_GST_PIPELINE or csi_pipeline(sensor_id=camera_index,
                                                       width=width, height=height)
        if verbose:
            print(f"▶ 카메라(GStreamer) 파이프라인으로 여는 중:\n    {pipeline}")
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened() and verbose:
            print("⚠️ GStreamer 파이프라인 열기 실패 — OpenCV가 GStreamer 지원으로 "
                  "빌드됐는지 확인하세요(scripts/jetson_doctor.py).")
        # 파이프라인 caps에 해상도가 이미 박혀 있어 cap.set()은 의미가 없다.
        return cap

    api = {
        "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
        "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
        "any": cv2.CAP_ANY,
    }.get(backend, cv2.CAP_ANY)

    if verbose:
        print(f"▶ 카메라({camera_index})를 여는 중입니다... (백엔드: {backend})")
    cap = cv2.VideoCapture(camera_index, api)
    if not cap.isOpened():
        return cap

    # FOURCC는 해상도보다 **먼저** 설정해야 한다 — 순서가 바뀌면 드라이버가 무압축
    # 포맷으로 해상도를 잡아버린 뒤라 MJPG 요청이 반영되지 않는 경우가 있다.
    if CAMERA_FOURCC:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC[:4]))
        except Exception:
            pass
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if CAMERA_FPS:
        cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

    if verbose:
        got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        got_fps = cap.get(cv2.CAP_PROP_FPS) or 0
        note = ""
        if (got_w, got_h) != (width, height):
            note = f"  ⚠️ 요청({width}x{height})과 다름 — 카메라가 지원하는 모드로 대체됨"
        print(f"✅ 카메라({camera_index}) 열림: {got_w}x{got_h} @ {got_fps:.0f}fps"
              f"{' [' + CAMERA_FOURCC + ']' if CAMERA_FOURCC else ''}{note}")
        if IS_LINUX and is_jetson() and got_fps and got_fps < 15:
            print("   ⚠️ fps가 낮습니다 — USB 웹캠이면 .env의 CAMERA_FOURCC=MJPG 확인, "
                  "그래도 낮으면 CAMERA_WIDTH/HEIGHT를 640x480으로 낮춰보세요.")
    return cap
