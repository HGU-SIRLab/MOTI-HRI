"""Jetson 이식 사전점검 — 이 기기에서 모티가 돌 수 있는지 항목별로 확인한다.

사용:
    python scripts/jetson_doctor.py            # 전체 점검
    python scripts/jetson_doctor.py --audio    # 오디오 장치 목록까지 (.env의 MIC_DEVICE/SPEAKER_DEVICE 채울 때)
    python scripts/jetson_doctor.py --camera   # 카메라에서 실제로 프레임을 한 장 받아본다
    python scripts/jetson_doctor.py --serial   # 시리얼 포트를 실제로 열어본다 (모터는 안 움직임)

왜 이 스크립트가 있나: 이식 작업은 Windows에서 하고 실행은 Jetson에서 한다. 개발 PC에서
"될 것"이라고 적어둔 것과 실물에서 실제로 되는 것은 다르므로, 추측을 실측으로 바꾸는
체크리스트를 코드로 남긴다. launcher.py를 띄우기 전에 이걸 먼저 돌려서 ❌ 를 없애면 된다.

Windows에서 돌려도 동작한다(Jetson 전용 항목은 건너뜀) — 옮기기 전 기준선 확인용.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import (IS_ARM64, IS_LINUX, device_tree_model,
                       ensure_utf8_console, is_jetson)

ensure_utf8_console()

_RESULTS: list[tuple[str, str]] = []


def _record(level: str, title: str, detail: str = ""):
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "info": "  ·"}[level]
    print(f"{icon} {title}")
    if detail:
        for line in detail.splitlines():
            print(f"     {line}")
    if level in ("warn", "fail"):
        _RESULTS.append((level, title))


def _run(cmd: list[str]) -> "str | None":
    """명령을 돌려 출력을 돌려준다. 명령이 없거나 실패하면 None."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr).strip()
    except Exception:
        return None


def _section(name: str):
    print(f"\n--- {name} " + "-" * max(0, 52 - len(name)))


# ---------------------------------------------------------------------------
def check_platform():
    _section("플랫폼")
    print(f"  python  : {sys.version.split()[0]}  ({sys.executable})")
    # Ubuntu 22.04의 기본 파이썬은 3.10이다. 소스 전체를 3.10 문법으로 파싱해 위반 0건을
    # 확인했고, 3.11 전용 API(asyncio.timeout)는 bootstrap.async_timeout으로 대체해 뒀다.
    if sys.version_info < (3, 10):
        _record("fail", f"파이썬 {sys.version_info.major}.{sys.version_info.minor} — 3.10 이상이 필요합니다",
                "Ubuntu 22.04 기본 파이썬이 3.10이므로 그대로 쓰면 됩니다.")
    else:
        _record("ok", f"파이썬 {sys.version_info.major}.{sys.version_info.minor} (3.10 이상 요구 충족)")
    print(f"  플랫폼  : {sys.platform}   arch={'aarch64' if IS_ARM64 else 'x86_64/기타'}")
    model = device_tree_model()
    if model:
        print(f"  보드    : {model}")

    if not IS_LINUX:
        _record("info", "리눅스가 아님 — Jetson 전용 항목은 건너뜁니다(개발 PC 기준선 점검 모드).")
        return
    if not is_jetson():
        _record("warn", "Jetson(L4T)으로 보이지 않습니다",
                "일반 리눅스라면 GPU/전원모드 항목은 무시하세요.")
    else:
        try:
            with open("/etc/nv_tegra_release") as f:
                _record("ok", "L4T(JetPack) 확인", f.read().strip())
        except OSError:
            _record("warn", "/etc/nv_tegra_release 를 읽지 못했습니다")

        # Orin Nano는 기본 전원 모드가 최대가 아닐 수 있고, 그러면 Live API 대화 지연이 늘어난다.
        out = _run(["nvpmodel", "-q"])
        if out:
            _record("ok", "전원 모드(nvpmodel)", out)
            # Xavier NX는 MAXN(=6코어 모드)이 무조건 좋지 않다 — 2026-08-26 실물 확인:
            # 6코어 모드는 코어당 최대 클럭이 1.9GHz -> 1.42GHz로 낮아져 media/voice_shift.py의
            # 실시간 오디오 처리가 오히려 못 버틴다(docs/jetson.md 8번 항목 참고). 기본값
            # MODE_10W_DESKTOP(4코어, 1.9GHz)이 이 저장소 기준 검증된 값이니 그냥 두는 게
            # 맞다 — 여기서는 정보만 보여주고 모드를 바꾸라고 권하지 않는다.
            if "10W_DESKTOP" not in out.upper():
                _record("info", "기본값(MODE_10W_DESKTOP)이 아닙니다",
                        "이 저장소는 4코어 @ 1.9GHz(MODE_10W_DESKTOP)에서 오디오 실시간\n"
                        "처리가 검증됐습니다. 6코어 모드는 코어당 클럭이 낮아져 오히려\n"
                        "끊김이 심해질 수 있으니(docs/jetson.md 8번), 성능이 아쉬워도\n"
                        "바로 MAXN으로 바꾸지 말고 docs/jetson.md를 먼저 확인하세요.")
        else:
            _record("info", "nvpmodel 명령을 찾지 못했습니다.")

    # 8GB 모델은 스왑이 없으면 모델 로딩 중 메모리가 빠듯하다.
    try:
        with open("/proc/meminfo") as f:
            mem = dict(l.split(":", 1) for l in f.read().splitlines() if ":" in l)
        total_gb = int(mem["MemTotal"].split()[0]) / 1024 / 1024
        swap_gb = int(mem["SwapTotal"].split()[0]) / 1024 / 1024
        _record("ok", f"메모리 {total_gb:.1f} GB / 스왑 {swap_gb:.1f} GB")
        if swap_gb < 4:
            _record("warn", "스왑이 4GB 미만입니다",
                    "8GB Orin Nano에서 mediapipe+insightface+pygame+Tk를 동시에 띄우면 빠듯합니다.\n"
                    "SSD에 스왑 파일을 만들어 두는 편이 안전합니다(docs/jetson.md).")
    except Exception:
        pass

    free = shutil.disk_usage(_REPO_ROOT).free / (1024 ** 3)
    _record("ok" if free > 10 else "warn", f"저장공간 여유 {free:.1f} GB",
            "" if free > 10 else "모델 캐시와 세션 산출물 저장에 부족할 수 있습니다.")


def check_packages():
    _section("파이썬 패키지")
    # (import 이름, pip 이름, 없으면 실행 불가인가)
    required = [
        ("google.genai", "google-genai", True),
        ("google.generativeai", "google-generativeai", True),
        ("cv2", "opencv-python", True),
        ("mediapipe", "mediapipe", True),
        ("insightface", "insightface", True),
        ("onnxruntime", "onnxruntime(-gpu)", True),
        ("dynamixel_sdk", "dynamixel-sdk", True),
        ("serial", "pyserial", True),
        ("sounddevice", "sounddevice", True),
        ("numpy", "numpy", True),
        ("scipy", "scipy", True),
        ("pygame", "pygame", True),
        ("PIL", "pillow", True),
        ("dotenv", "python-dotenv", True),
        ("websockets", "websockets", True),
        ("aec_audio_processing", "aec-audio-processing", False),
        ("pyworld", "pyworld", False),
        ("pynput", "pynput", False),
        ("screeninfo", "screeninfo", False),
    ]
    soft_note = {
        "aec_audio_processing": "AEC 불가 → .env에 ENABLE_AEC=false 로 두세요(에코로 barge-in 오탐이 늘어납니다).",
        "pyworld": "목소리 시프트 불가 → .env에 ENABLE_VOICE_SHIFT=false 로 두세요(성인 음색 그대로 재생).",
        "pynput": "키보드 단축키 기능만 영향.",
        "screeninfo": "퀴즈 창 모니터 지정만 영향(기본 화면에 뜹니다).",
    }
    for module, pkg, hard in required:
        try:
            m = __import__(module)
            ver = getattr(m, "__version__", "") or ""
            _record("ok", f"{pkg} {ver}".strip())
        except Exception as e:
            if hard:
                _record("fail", f"{pkg} 를 불러올 수 없습니다", f"{type(e).__name__}: {e}")
            else:
                _record("warn", f"{pkg} 없음 — 해당 기능이 꺼집니다", soft_note.get(module, ""))

    # Tkinter는 pip이 아니라 apt로 깔아야 한다 — 퀴즈 사진 창(display/quiz_window.py)이 여기에 의존.
    try:
        import tkinter  # noqa: F401
        _record("ok", "tkinter (퀴즈 사진 창)")
    except Exception:
        _record("fail", "tkinter 없음 — 퀴즈 사진 창이 안 뜹니다",
                "리눅스에서는 pip이 아니라 apt입니다: `sudo apt install python3-tk`")


def check_onnx():
    _section("ONNX Runtime (얼굴인식 가속)")
    try:
        import onnxruntime as ort
    except Exception as e:
        _record("fail", "onnxruntime 임포트 실패", str(e))
        return
    providers = ort.get_available_providers()
    _record("ok", f"onnxruntime {ort.__version__}", "가용 프로바이더: " + ", ".join(providers))
    if is_jetson() and providers == ["CPUExecutionProvider"]:
        _record("warn", "GPU 프로바이더가 없습니다 — 얼굴인식이 CPU로 돕니다",
                "설치된 JetPack 버전에 맞는 onnxruntime-gpu 휠이 필요합니다(docs/jetson.md).\n"
                "CPU로 버틸 거라면 .env에 FACE_DET_SIZE=320 을 넣어 검출 해상도를 낮추세요.")


def check_opencv():
    _section("OpenCV 빌드 옵션")
    try:
        import cv2
    except Exception as e:
        _record("fail", "cv2 임포트 실패", str(e))
        return
    info = cv2.getBuildInformation()

    def has(key: str) -> bool:
        for line in info.splitlines():
            if line.strip().lower().startswith(key.lower()):
                return "YES" in line.upper()
        return False

    _record("ok", f"OpenCV {cv2.__version__}", f"실제 로드된 경로: {getattr(cv2, '__file__', '?')}")

    # ⚠️ mediapipe는 opencv-contrib-python을 의존성으로 끌고 온다(pip show mediapipe로 확인).
    # 그래서 CSI 카메라를 쓰려고 apt의 python3-opencv(GStreamer 포함)를 깔아놔도, pip이
    # 설치한 opencv가 앞서 잡혀 GStreamer 없는 쪽이 로드되는 일이 생긴다. 위의 "실제 로드된
    # 경로"가 dist-packages(apt)가 아니라 site-packages(pip)면 그 상황이다.
    if IS_LINUX:
        path = getattr(cv2, "__file__", "") or ""
        if "dist-packages" in path:
            _record("info", "apt(python3-opencv) 쪽 OpenCV가 로드되었습니다.")
        elif "site-packages" in path:
            _record("info", "pip 쪽 OpenCV가 로드되었습니다"
                            " — CSI 카메라를 쓸 계획이면 아래 GStreamer 줄을 반드시 확인하세요.")

    gst = has("GStreamer")
    _record("ok" if gst else "warn", f"GStreamer 지원: {'YES' if gst else 'NO'}",
            "" if gst else
            "CSI 카메라(nvarguscamerasrc) 경로를 쓸 수 없습니다. USB 웹캠만 쓸 거면 무시해도 됩니다.\n"
            "필요하면 pip의 opencv-python 대신 JetPack이 제공하는 python3-opencv를 쓰세요.")
    if IS_LINUX:
        v4l = has("V4L/V4L2")
        _record("ok" if v4l else "warn", f"V4L/V4L2 지원: {'YES' if v4l else 'NO'}",
                "" if v4l else "USB 웹캠을 열 수 없습니다.")


def check_camera(grab: bool = False):
    _section("카메라")
    if IS_LINUX:
        try:
            devs = sorted(p for p in os.listdir("/dev") if p.startswith("video"))
        except OSError:
            devs = []
        _record("ok" if devs else "warn",
                f"/dev/video* : {', '.join(devs) if devs else '없음'}")
        out = _run(["v4l2-ctl", "--list-devices"])
        if out:
            print("     " + out.replace("\n", "\n     "))
        else:
            _record("info", "v4l2-ctl 없음 — `sudo apt install v4l-utils` 하면 더 자세히 볼 수 있습니다.")

    if not grab:
        _record("info", "실제 프레임 획득 테스트는 `--camera` 옵션으로 실행하세요.")
        return

    from vision.camera import open_capture
    cap = open_capture(int(os.getenv("CAMERA_INDEX", "0")))
    if not cap.isOpened():
        _record("fail", "카메라를 열지 못했습니다",
                ".env의 CAMERA_BACKEND / CAMERA_INDEX / CAMERA_GST_PIPELINE 을 조정해 보세요.")
        return
    ok, frame = cap.read()
    cap.release()
    if ok and frame is not None:
        _record("ok", f"프레임 획득 성공: {frame.shape[1]}x{frame.shape[0]}")
    else:
        _record("fail", "카메라는 열렸는데 프레임을 못 읽었습니다",
                "USB 웹캠이면 FOURCC/해상도 조합을 지원하지 않을 수 있습니다 —\n"
                ".env에 CAMERA_FOURCC= (빈 값)으로 두거나 640x480으로 낮춰 다시 시도해 보세요.")


def check_serial(try_open: bool = False):
    _section("시리얼 / 다이나믹셀")
    try:
        import serial
        import serial.tools.list_ports
    except Exception as e:
        _record("fail", "pyserial 임포트 실패", str(e))
        return

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        _record("fail", "시리얼 포트가 하나도 열거되지 않았습니다", "U2D2가 꽂혀 있는지 확인하세요.")
    for p in ports:
        vid_pid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "----:----"
        print(f"  · {p.device}  [{vid_pid}]  {p.description}")

    from hardware import config as C
    _record("ok", f"모티가 쓸 포트: {C.DEVICENAME} @ {C.BAUDRATE} (프로토콜 {C.PROTOCOL_VERSION})")

    if IS_LINUX:
        import getpass
        import grp
        try:
            in_dialout = getpass.getuser() in grp.getgrnam("dialout").gr_mem
        except (KeyError, ImportError):
            in_dialout = False
        _record("ok" if in_dialout else "warn",
                f"dialout 그룹 소속: {'예' if in_dialout else '아니오'}",
                "" if in_dialout else "`sudo usermod -aG dialout $USER` 후 **재로그인**해야 반영됩니다.")

    if not try_open:
        _record("info", "포트 열기 테스트는 `--serial` 옵션으로 실행하세요(모터는 안 움직입니다).")
        return
    try:
        with serial.Serial(C.DEVICENAME, C.BAUDRATE, timeout=0.2):
            _record("ok", f"{C.DEVICENAME} 열기 성공")
    except Exception as e:
        _record("fail", f"{C.DEVICENAME} 열기 실패", f"{type(e).__name__}: {e}")


def check_audio(verbose: bool = False):
    _section("오디오")
    try:
        import sounddevice as sd
    except Exception as e:
        _record("fail", "sounddevice 임포트 실패",
                f"{e}\n리눅스에서는 PortAudio가 따로 필요합니다: `sudo apt install libportaudio2`")
        return

    try:
        devices = sd.query_devices()
    except Exception as e:
        _record("fail", "오디오 장치 목록을 못 읽었습니다", str(e))
        return

    if verbose:
        for i, d in enumerate(devices):
            tags = []
            if d["max_input_channels"]:
                tags.append("in")
            if d["max_output_channels"]:
                tags.append("out")
            print(f"  [{i:2d}] {d['name']}  ({'/'.join(tags) or '-'}, {int(d['default_samplerate'])}Hz)")
    else:
        _record("info", "전체 장치 목록은 `--audio` 옵션으로 볼 수 있습니다.")

    from media.audio_manager import INPUT_RATE, OUTPUT_RATE, describe_devices
    print("  " + describe_devices())

    # 모티가 실제로 쓰는 레이트/채널로 열리는지 — ALSA는 여기서 자주 걸린다.
    checks = (("input", INPUT_RATE, "MIC_DEVICE", sd.check_input_settings),
              ("output", OUTPUT_RATE, "SPEAKER_DEVICE", sd.check_output_settings))
    for kind, rate, key, fn in checks:
        try:
            fn(samplerate=rate, channels=1, dtype="int16")
            _record("ok", f"{kind} {rate}Hz mono int16 지원")
        except Exception as e:
            _record("fail", f"{kind} {rate}Hz mono int16 를 열 수 없습니다",
                    f"{e}\n.env의 {key}에 장치 인덱스나 이름 일부를 지정하세요"
                    f"(`python scripts/jetson_doctor.py --audio`로 목록 확인).")


def check_display():
    _section("디스플레이 (얼굴 UI / 퀴즈 사진 창)")
    if IS_LINUX:
        disp = os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")
        _record("ok" if disp else "fail",
                f"DISPLAY={os.getenv('DISPLAY') or '(없음)'}",
                "" if disp else
                "SSH로만 접속한 상태면 화면이 안 뜹니다. 젯슨에 모니터를 연결한 뒤\n"
                "데스크톱 세션에서 실행하거나, `export DISPLAY=:0` 후 실행하세요.")
    try:
        import pygame
        pygame.display.init()
        sizes = pygame.display.get_desktop_sizes()
        pygame.display.quit()
        _record("ok", f"감지된 모니터 {len(sizes)}개: {sizes}")
        if len(sizes) > 1:
            _record("info", "모니터가 2개 이상이면 얼굴 UI는 두 번째(#1)에 뜹니다(display/main.py).")
    except Exception as e:
        _record("fail", "pygame 디스플레이 초기화 실패", str(e))


def check_assets():
    _section("모델 / 설정 파일")
    landmarker = os.path.join(_REPO_ROOT, "models", "face_landmarker.task")
    exists = os.path.exists(landmarker)
    _record("ok" if exists else "fail",
            f"models/face_landmarker.task {'있음' if exists else '없음'}",
            "" if exists else
            "이 파일은 .gitignore 대상이라 git clone만으로는 오지 않습니다. 개발 PC의 models/ 에서 복사하세요.")

    ins = os.path.join(os.path.expanduser("~"), ".insightface", "models", "buffalo_l")
    _record("ok" if os.path.isdir(ins) else "warn",
            f"insightface buffalo_l {'있음' if os.path.isdir(ins) else '없음'}",
            "" if os.path.isdir(ins) else
            "첫 실행 시 자동 다운로드됩니다(네트워크 필요).\n"
            "오프라인 시연이라면 미리 한 번 돌려 캐시를 만들어 두세요.")

    brain = os.path.join(_REPO_ROOT, "art_brain.pkl")
    _record("ok" if os.path.exists(brain) else "warn",
            f"art_brain.pkl {'있음' if os.path.exists(brain) else '없음 (얼굴 기억이 비어서 시작)'}",
            "" if os.path.exists(brain) else
            "기존에 등록해둔 얼굴을 그대로 쓰려면 user_profiles.json과 함께 개발 PC에서 복사하세요.")

    env = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(env):
        _record("fail", ".env 없음", "`cp .env.example .env` 후 GOOGLE_API_KEY를 채우세요.")
    else:
        _record("ok", ".env 있음")
        _record("ok" if os.getenv("GOOGLE_API_KEY") else "fail",
                "GOOGLE_API_KEY " + ("설정됨" if os.getenv("GOOGLE_API_KEY") else "비어 있음"),
                "" if os.getenv("GOOGLE_API_KEY") else "Live API 대화가 시작되지 않습니다.")

    assets = os.path.join(_REPO_ROOT, "assets")
    _record("ok" if os.path.isdir(assets) else "fail",
            f"assets/ {'있음' if os.path.isdir(assets) else '없음'}",
            "" if os.path.isdir(assets) else "퀴즈 사진이 여기 들어있습니다 — 없으면 퀴즈가 안 돕니다.")

    for d in ("user_result", "logs"):
        os.makedirs(os.path.join(_REPO_ROOT, d), exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="모티 Jetson 이식 사전점검")
    ap.add_argument("--audio", action="store_true", help="오디오 장치 목록을 전부 출력")
    ap.add_argument("--camera", action="store_true", help="실제로 프레임을 한 장 받아본다")
    ap.add_argument("--serial", action="store_true", help="시리얼 포트를 실제로 열어본다")
    args = ap.parse_args()

    print("=" * 60)
    print("  모티 Jetson 사전점검 (scripts/jetson_doctor.py)")
    print("=" * 60)

    for fn in (check_platform, check_packages, check_onnx, check_opencv):
        try:
            fn()
        except Exception as e:
            _record("fail", f"{fn.__name__} 점검 중 예외", f"{type(e).__name__}: {e}")
    for fn, kw in ((check_camera, {"grab": args.camera}),
                   (check_serial, {"try_open": args.serial}),
                   (check_audio, {"verbose": args.audio}),
                   (check_display, {}),
                   (check_assets, {})):
        try:
            fn(**kw)
        except Exception as e:
            _record("fail", f"{fn.__name__} 점검 중 예외", f"{type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    fails = [t for lv, t in _RESULTS if lv == "fail"]
    warns = [t for lv, t in _RESULTS if lv == "warn"]
    if not fails and not warns:
        print("  ✅ 전부 통과 — `python launcher.py` 를 실행해도 됩니다.")
    else:
        if fails:
            print(f"  ❌ 반드시 고쳐야 할 것 {len(fails)}건:")
            for t in fails:
                print(f"     - {t}")
        if warns:
            print(f"  ⚠️  확인 권장 {len(warns)}건:")
            for t in warns:
                print(f"     - {t}")
    print("=" * 60)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
