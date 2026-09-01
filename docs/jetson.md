# Jetson Orin Nano 이식 (jetson-moti 브랜치)

대상 하드웨어: **Jetson Orin Nano Super Developer Kit 8GB + NVMe SSD 256GB**
대상 OS: **Ubuntu 22.04 LTS (aarch64)** — Orin 계열에서 22.04는 JetPack 6.x(Jetson Linux 36.x) 계열이다.
정확한 버전은 실물에서 확인할 것(onnxruntime-gpu 휠을 고를 때 이 값이 기준이 된다):

```bash
lsb_release -a                 # Ubuntu 22.04.x 인지
cat /etc/nv_tegra_release      # L4T(Jetson Linux) 버전
apt show nvidia-jetpack 2>/dev/null | head -3
```

목표: 지금까지 Windows에서 돌던 모티 기능 전부(얼굴추적·인식 → Live API 대화 → 표정 UI →
제스처 → 퀴즈 3라운드 → 결과지 저장)를 젯슨에서 그대로 재현.

이 문서는 **절차서**다. "이렇게 하면 된다"가 아니라 "이 순서로 하고, 각 단계를
`scripts/jetson_doctor.py`로 확인하라"는 뜻이다 — 실제 검증은 젯슨 실물에서만 가능하다.

---

## 0. 이 브랜치가 Windows 브랜치와 다른 점

`main`은 Windows에서만 돌아가는 전제로 쓰여 있었다. 갈리는 지점이 일곱 군데 있었고,
전부 **Windows 동작을 바꾸지 않는 방식**(플랫폼 분기 + `.env` 스위치)으로 고쳤다.
즉 이 브랜치는 개발 PC에서도 예전과 똑같이 돌아간다.

| # | 문제 | 증상(고치기 전) | 고친 곳 |
|---|------|----------------|---------|
| 1 | `cv2.CAP_DSHOW`는 Windows 전용 백엔드 | 젯슨에서 카메라가 안 열림 | `vision/camera.py` 신규, `vision/face.py` |
| 2 | 시리얼 포트 기본값 `COM3`, 탐색이 Windows 드라이버 문자열 매칭 | U2D2를 못 찾음 | `hardware/config.py` (FTDI **VID**로 판정) |
| 3 | `multiprocessing` 기본값이 리눅스는 `fork` | 퀴즈 사진 창이 뜨다 멈춤/죽음 | `bootstrap.ensure_spawn_start_method()` |
| 4 | ONNX 프로바이더를 검증 없이 요청 | 얼굴인식이 **조용히** CPU로 떨어짐 | `vision/vision_brain.py` |
| 5 | 오디오가 시스템 기본 장치 고정 | ALSA가 HDMI를 잡아 소리가 안 남 | `media/audio_manager.py` (`MIC_DEVICE`/`SPEAKER_DEVICE`) |
| 6 | `pyworld`/`aec` 를 최상단 import | 그 패키지 빌드가 실패하면 **로봇 자체가 안 뜸** | `media/voice_shift.py`, `media/audio_manager.py` |
| 7 | `asyncio.timeout()` 은 파이썬 3.11 전용 | Ubuntu 22.04 기본 파이썬 3.10에서 `AttributeError` | `bootstrap.async_timeout()`, `scripts/test_quiz_live.py` |

**파이썬 버전(7번)**: 개발 PC는 3.11인데 Ubuntu 22.04의 기본 파이썬은 **3.10**이다. 전 소스를
3.10 문법으로 파싱해 확인한 결과 **문법 위반은 0건**이었고, 걸리는 건 API 하나뿐이었다 —
`asyncio.timeout()`(3.11 추가, `scripts/test_quiz_live.py`에서 사용). 3.10에서도 도는 대체
구현을 `bootstrap.async_timeout()`에 두고 호출부를 바꿨다. **즉 22.04 기본 파이썬을 그대로
쓰면 되고, 3.11을 따로 깔 필요가 없다.**

6번이 특히 중요하다. 두 패키지는 aarch64 미리 빌드 휠이 없을 수 있는데, 예전 구조에서는
`ENABLE_VOICE_SHIFT=false`로 꺼둬도 `launcher.py`의 import 단계에서 죽었다. 지금은 없으면
해당 기능만 끄고 경고를 찍은 뒤 계속 간다.

---

## 1. 하드웨어 연결

| 장치 | 연결 | 젯슨에서 보이는 이름 |
|------|------|---------------------|
| 다이나믹셀 (U2D2) | USB | `/dev/ttyUSB0` (FTDI, VID `0403`) |
| 카메라 | USB 웹캠 | `/dev/video0` |
| 마이크 / 스피커 | USB 오디오 | `jetson_doctor.py --audio` 로 인덱스 확인 |
| 표정 화면 | HDMI/DP | 800×480 기준으로 스케일됨 |

카메라를 CSI로 바꿀 거라면 `.env`에 `CAMERA_BACKEND=gstreamer`를 넣는다. 단 그 경로는
**OpenCV가 GStreamer 지원으로 빌드되어 있어야** 동작한다(아래 3단계).

---

## 2. OS / 시스템 준비

```bash
# 전원 모드를 최대로 — 기본 저전력 모드면 대화 응답 지연이 눈에 띄게 늘어난다.
# 모드 번호는 보드/JetPack 버전마다 다르다(Orin Nano Super는 MAXN SUPER 모드가 따로 있다).
# 번호를 외워 넣지 말고 먼저 열거해서 최대 성능 모드를 고를 것.
sudo nvpmodel -p --verbose     # 이 보드가 지원하는 모드 목록
nvpmodel -q                    # 지금 걸린 모드
sudo nvpmodel -m <최대성능_모드번호>
sudo jetson_clocks             # 재부팅하면 풀린다

# 시리얼 포트 권한. 재로그인해야 반영된다
sudo usermod -aG dialout $USER

# 8GB 모델이라 스왑을 잡아두는 편이 안전하다(SSD에 만들 것)
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

메모리 압박이 실제로 있다: mediapipe FaceLandmarker + insightface(buffalo_l) + pygame +
Tkinter 창 + Live API 스트림이 동시에 뜬다. 스왑이 없으면 모델 로딩 중 죽을 수 있다.

---

## 3. 파이썬 환경

`requirements-jetson.txt` 의 주석이 단계별 명령을 그대로 담고 있다. 요약하면:

```bash
sudo apt install -y python3-pip python3-dev python3-venv python3-tk \
                    libportaudio2 portaudio19-dev v4l-utils build-essential cmake

python3 -m venv ~/moti-venv --system-site-packages   # apt로 깐 opencv를 쓰려면 이 옵션 필요
source ~/moti-venv/bin/activate
python -V                                            # 3.10.x 면 정상
pip install -r requirements-jetson.txt
```

**따로 판단해야 하는 두 가지:**

- **OpenCV** — USB 웹캠만 쓸 거면 `pip install opencv-python`으로 충분하다. CSI 카메라를
  쓰려면 GStreamer 지원이 필요하고, 그건 `sudo apt install python3-opencv` 쪽이다.

  ⚠️ **함정**: `mediapipe`는 `opencv-contrib-python`을 **의존성으로 끌고 온다**
  (`pip show mediapipe`로 확인됨). 그래서 apt로 GStreamer 포함 OpenCV를 깔아놔도
  `pip install mediapipe` 한 방에 pip쪽 OpenCV가 venv에 들어와 그쪽이 먼저 로드된다.
  CSI 카메라를 쓸 계획이라면 설치 후 반드시 확인할 것:
  ```bash
  python -c "import cv2; print(cv2.__file__)"   # dist-packages(apt)인지 site-packages(pip)인지
  ```
  `jetson_doctor.py`가 이 경로와 GStreamer 지원 여부를 같이 찍어준다.
  USB 웹캠만 쓸 거라면 이 충돌은 신경 쓸 필요 없다.
- **onnxruntime** — `pip install onnxruntime`은 **CPU 전용**이다. GPU 가속을 쓰려면
  설치된 JetPack 버전에 맞는 `onnxruntime-gpu` 휠을 받아야 한다. 먼저
  `cat /etc/nv_tegra_release`로 L4T 버전을 확인하고 그에 맞는 휠을 설치할 것.
  확인:
  ```bash
  python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
  ```
  `CUDAExecutionProvider`가 보이면 성공. 안 보이면 CPU로 도는 것이고, 그 상태로 버티려면
  `.env`에 `FACE_DET_SIZE=320`을 넣어 검출 해상도를 낮춘다.

---

## 4. git으로 코드 옮기기 — 안 따라오는 파일들

`.gitignore` 때문에 `git clone`만으로는 **오지 않는** 것들이 있다. 개발 PC에서 직접 복사해야 한다.

| 파일/폴더 | 없으면 | 어떻게 |
|-----------|--------|--------|
| `models/face_landmarker.task` | 얼굴**추적**이 아예 안 됨 | 개발 PC `C:\moti\models\`에서 복사 |
| `.env` | API 키 없어 대화 시작 안 됨 | `cp .env.example .env` 후 채우기 |
| `art_brain.pkl` | 등록된 얼굴 기억이 빈 상태로 시작 | 기존 기억을 유지하려면 복사 |
| `user_profiles.json` | 위와 같음 | 위와 같음 |
| insightface `buffalo_l` | 얼굴**인식**이 안 됨 | 첫 실행 시 자동 다운로드(네트워크 필요) |

`assets/`(퀴즈 사진 30MB)는 git에 들어있으니 clone으로 따라온다.

오프라인 시연 예정이라면 **네트워크가 되는 동안 한 번 실행해서** insightface 캐시
(`~/.insightface/models/buffalo_l`)를 미리 만들어 둘 것. 단, Live API 대화 자체는
네트워크가 필수라 완전 오프라인 시연은 불가능하다.

---

## 5. 점검 → 실행

```bash
python scripts/jetson_doctor.py            # 전체
python scripts/jetson_doctor.py --audio    # 오디오 장치 인덱스 확인 → .env에 기입
python scripts/jetson_doctor.py --camera   # 실제로 프레임을 한 장 받아본다
python scripts/jetson_doctor.py --serial   # 포트 열기까지 (모터는 안 움직임)
```

❌ 가 없어지면 실행:

```bash
export DISPLAY=:0        # SSH로 접속했다면 반드시 (안 하면 표정 창이 안 뜬다)
python launcher.py
```

> ⚠️ **보드마다 다르다.** Orin Nano Super 실물(JetPack 6.2.3)에서는 GDM이 사용자 X
> 세션을 `:0`이 아니라 **`:1`** 에 띄웠다. `DISPLAY=:0`으로는 `xcb_connection_has_error`
> 만 난다. `ls /tmp/.X11-unix/` 로 실제 디스플레이 번호를 확인하고, SSH 세션이면
> `XAUTHORITY` 도 같이 줘야 한다. 상세는 아래 **§9**.

---

## 6. 실물에서 처음 켤 때 확인할 순서

한꺼번에 켜서 안 되면 원인을 못 찾는다. 아래 순서로 하나씩 올린다.

1. `python scripts/jetson_doctor.py --camera --serial --audio` → ❌ 0건
2. `python scripts/test_motions.py` → 모터가 실제로 움직이는가 (관절 홈 위치가 Windows에서
   보정한 값 그대로인지 눈으로 확인 — `hardware/init.py`의 `MOTOR_HOME_POSITIONS`)
3. `python scripts/test_display.py` → 표정 창이 HDMI 화면에 뜨는가
4. `python scripts/test_quiz_window.py` → 퀴즈 사진 창(별도 프로세스)이 뜨는가
   **← 3번 fork 문제가 있었다면 여기서 걸린다**
5. `python scripts/test_vision_brain.py` → 얼굴이 인식되는가, 몇 fps인가
6. `python scripts/test_live_audio.py` → 마이크로 말하면 대답이 들리는가
7. `python launcher.py` → 전체

---

## 7. 젯슨에서 새로 생길 법한 문제와 대처

| 증상 | 먼저 볼 것 |
|------|-----------|
| 카메라 fps가 5~10으로 낮다 | `.env` `CAMERA_FOURCC=MJPG` 확인. 그래도 낮으면 `CAMERA_WIDTH/HEIGHT=640/480` |
| 얼굴인식이 느리다 | doctor의 "얼굴인식 실행 프로바이더" 줄 — CPU면 onnxruntime-gpu 재설치, 아니면 `FACE_DET_SIZE=320` |
| 소리가 안 난다 | `--audio`로 인덱스 확인 후 `SPEAKER_DEVICE` 지정. ALSA 기본이 HDMI인 경우가 흔함 |
| 말이 중간에 끊긴다 | 세션 종료 로그의 "스피커 언더런" 수치를 보고 `PLAYOUT_PRIME_MS`를 올린다(`.env.example` 주석 참고). **`VOICE_SHIFT_BUFFER_MS`는 올리지 말 것** — 2026-08-10에 효과 없음이 실측으로 확정됨 |
| 퀴즈 사진 창이 안 뜬다 | `python3-tk` 설치 여부, `DISPLAY` 설정, 그리고 콘솔에 "퀴즈 사진 창 프로세스가 죽었습니다" 경고가 떴는지 |
| 모터를 못 연다 | `dialout` 그룹 + **재로그인**. `ls -l /dev/ttyUSB0`로 권한 확인 |
| 대화 응답이 전반적으로 굼뜨다 | `nvpmodel -q`로 전원 모드 확인 → 최대 성능 모드 + `sudo jetson_clocks` (재부팅하면 풀린다) |

---

## 8. 아직 안 한 것 / 남은 판단

- **실물 검증 전부.** 이 브랜치의 변경은 Windows에서 회귀가 없다는 것만 확인했다
  (doctor 실행 결과 ❌ 0건, 카메라·시리얼·오디오 경로 모두 기존 동작 유지).
- **성능 목표치 미정.** 얼굴추적이 몇 fps 나와야 실사용에 무리가 없는지는 젯슨에서
  재본 뒤 정해야 한다. Windows에서는 웹캠 30fps 기준으로 맞춰져 있었다.
- **자동 시작(systemd) 미구성.** 전원만 넣으면 모티가 뜨게 하려면 서비스 유닛이 필요한데,
  `DISPLAY`가 필요한 GUI 프로세스라 사용자 세션에 붙여야 한다. 실행이 안정된 뒤에 할 일.
- **CSI 카메라 경로 미검증.** 코드 경로는 만들어 뒀지만(`vision/camera.py`의
  `csi_pipeline()`) USB 웹캠을 계속 쓸 거면 손댈 필요 없다.

---

## 9. Orin Nano Super 실물 이식 메모 (2026-09-01)

Jetson Orin Nano Super Developer Kit 실물에 이식하면서 실측한 것. 위 절차의 보완/정정.
보드/OS: **JetPack 6.2.3 (L4T R36.5.2) / Ubuntu 22.04.5 / Python 3.10.12 / MAXN_SUPER / NVMe 부팅.**
`jetson-moti` 브랜치 전제와 일치 → 재플래싱 불필요. (JetPack 7/Ubuntu 24.04면 6.2.x로 다시 깔 것.)

### 파이썬 의존성 — 핀 없이 설치하면 세 군데서 깨진다

`requirements-jetson.txt` 상단 주석의 "실측 검증된 버전" 블록 참고. 요약:
- `numpy==1.26.4` (pip 기본 2.x → `--system-site-packages` matplotlib과 ABI 충돌로 mediapipe import 크래시. dotprod SIGILL 아님 — Orin A78AE엔 `asimddp` 있음).
- `opencv-contrib-python==4.11.0.86` 단독 (opencv-python과 동시설치 시 cv2 깨짐).
- `onnx==1.17.0` (1.22는 ml_dtypes>=0.5.4 → numpy>=2 악순환).
- `onnxruntime-gpu==1.23.0` from `https://pypi.jetson-ai-lab.io/jp6/cu126` (CPU판 먼저 제거).
  검증: `get_available_providers()`에 `CUDAExecutionProvider`. recognize_face 중앙 75ms @640.
- `pyworld==0.3.5` 소스빌드 OK (cython+build-essential 있으면).

### 디스플레이 — 로봇 화면은 `:0`이 아니라 `:1` (이 보드 기준)

GDM이 사용자 X 세션을 `:1`에 띄운다(`/tmp/.X11-unix/`에 `X1`만). 연결 패널은 **DP-1, 800×480 native**.
SSH 세션에서 얼굴 UI를 로봇 화면에 띄우려면:
```bash
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority
```
얼굴 UI(`:1`)와 퀴즈 창(참가자 노트북, `ssh -X` 포워딩 디스플레이)을 **동시에** 쓰려면 두
디스플레이 쿠키를 한 파일에 합쳐야 한다(안 하면 퀴즈 자식이 `couldn't connect to display`):
```bash
XAUTHORITY=/run/user/1000/gdm/Xauthority xauth nextract - :1 | xauth nmerge -   # :1 쿠키를 ~/.Xauthority에
# 이후 launcher 실행: DISPLAY=:1  XAUTHORITY=$HOME/.Xauthority
# .env: QUIZ_WINDOW_DISPLAY=localhost:10.0  (참가자 노트북 ssh -X 디스플레이)
```
`display/main.py`는 `pygame.NOFRAME | pygame.FULLSCREEN` (커밋 09c496f) — NOFRAME만으론
GNOME 상단바/독이 얼굴 위에 남는다.

### 오디오 — USB 스피커가 48kHz 전용이라 PulseAudio 경유 필수

- 스피커 = USB "UACDemoV1.0" (48kHz 전용), 마이크 = C922 웹캠 내장 (32kHz).
- 코드는 스피커를 Gemini 출력 그대로 24kHz로 여는데 raw ALSA `hw:`로는 `paInvalidSampleRate`.
  → **PulseAudio 경유**로 리샘플. `libasound2-plugins` + `/usr/share/alsa/alsa.conf.d/pulse.conf`
  덕에 ALSA `default` PCM이 PulseAudio로 라우팅됨. sounddevice 목록엔 `pulse`가 안 뜨고
  `default`만 뜬다 → **`.env`: `MIC_DEVICE=default` / `SPEAKER_DEVICE=default`**.
- `pactl` CLI는 `PULSE_SERVER=unix:/run/user/1000/pulse/native` 를 줘야 붙는다(앱이 쓰는
  ALSA pulse 플러그인은 이 env 없이도 붙음).

### AEC — `aec-audio-processing` 없음, PulseAudio module-echo-cancel 로 (`.env` ENABLE_AEC=false)

`~/.config/pulse/default.pa` (Xavier에서 확정, `extended_filter=1` 단독):
```
.include /etc/pulse/default.pa
set-default-source alsa_input.usb-046d_C922_Pro_Stream_Webcam_<SERIAL>-02.analog-stereo
set-default-sink   alsa_output.usb-Jieli_Technology_UACDemoV1.0_<SERIAL>-00.analog-stereo
load-module module-echo-cancel aec_method=webrtc aec_args="extended_filter=1" source_name=echocancel_source sink_name=echocancel_sink
set-default-source echocancel_source
set-default-sink   echocancel_sink
```
`systemctl --user restart pulseaudio.service` 로 적용/검증. (장치명 시리얼은 `pactl list short sinks/sources`.)

### 시리얼 (U2D2) 권한 — udev 규칙

`usermod -aG dialout` 후에도 재로그인 전 셸은 반영 안 됨. 영구:
`/etc/udev/rules.d/99-moti-u2d2.rules`:
```
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6014", OWNER="jetson-moti", MODE="0660"
```
`sudo udevadm control --reload-rules && sudo udevadm trigger`.

### launcher.py 실행 (검증됨 2026-09-01)

```bash
cd ~/moti
export DISPLAY=:1 XAUTHORITY=$HOME/.Xauthority PULSE_SERVER=unix:/run/user/1000/pulse/native
~/moti-venv/bin/python -u launcher.py
```
확인됨: 모터 홈, insightface CUDA, mediapipe 트래킹 로딩, Live API 대화, 얼굴 재인식,
프라이버시 안내, 제스처, 퀴즈 창 기동(xauth 합친 뒤), `[대화종료]` → 대화록/메타 저장.
**아직 안 본 것**: 퀴즈 3모드 실제 진행, barge-in, 완전 재부팅 후 자동복구, 장시간 keepalive.
