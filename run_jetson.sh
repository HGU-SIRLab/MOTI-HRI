#!/usr/bin/env bash
# MOTI launcher.py 실행 래퍼 (Jetson Orin Nano Super)
#   ./run_jetson.sh            SSH 세션이든 로봇 콘솔이든 알아서 맞춤
#   ./run_jetson.sh 1          카메라 인덱스 지정 (기본 0 = C922)
#   FACE_DISPLAY=:0 ./run_jetson.sh   얼굴 UI 디스플레이 수동 지정
set -e
cd "$(dirname "$0")"

# 1) SSH -X/-Y 로 들어왔으면 지금 이 셸의 $DISPLAY 가 포워딩 디스플레이(localhost:1x.0)다.
#    얼굴 UI 용으로 덮어쓰기 전에 먼저 붙잡아 둔다.
INCOMING_DISPLAY="$DISPLAY"

export XAUTHORITY="$HOME/.Xauthority"
export PULSE_SERVER="unix:/run/user/$(id -u)/pulse/native"
export PYTHONUNBUFFERED=1
GDM_XAUTH="/run/user/$(id -u)/gdm/Xauthority"

# 2) 로봇 얼굴 UI 디스플레이 자동 탐지.
#    GDM 재로그인/재부팅 때마다 콘솔 세션이 :0 <-> :1 로 바뀌므로 하드코딩하지 않는다.
if [ -n "$FACE_DISPLAY" ]; then
  export DISPLAY="$FACE_DISPLAY"
else
  FOUND=""
  for sock in /tmp/.X11-unix/X*; do
    [ -e "$sock" ] || continue
    n=":${sock##*/X}"
    [ "$n" = "$INCOMING_DISPLAY" ] && continue                 # SSH 포워딩 디스플레이는 제외
    if DISPLAY="$n" XAUTHORITY="$GDM_XAUTH" xdpyinfo >/dev/null 2>&1; then
      FOUND="$n"; break
    fi
  done
  export DISPLAY="${FOUND:-:0}"
fi

# 3) 얼굴 UI 디스플레이 쿠키를 ~/.Xauthority 에 합쳐 넣는다.
#    sshd 가 넣어준 포워딩 쿠키와 공존 → 얼굴 UI + 퀴즈 창이 같은 XAUTHORITY 로 둘 다 뜬다.
if ! xauth list 2>/dev/null | grep -q "/unix${DISPLAY} "; then
  XAUTHORITY="$GDM_XAUTH" xauth nextract - "$DISPLAY" 2>/dev/null \
    | xauth nmerge - 2>/dev/null || true
fi

# 4) 퀴즈 창 목적지: SSH 포워딩 디스플레이가 있으면 거기(참가자 노트북), 없으면 .env 값 사용
if [ -n "$SSH_CONNECTION" ] && [ -n "$INCOMING_DISPLAY" ] && [ "$INCOMING_DISPLAY" != "$DISPLAY" ]; then
  export QUIZ_WINDOW_DISPLAY="$INCOMING_DISPLAY"
  echo "🖥  퀴즈 창 → $QUIZ_WINDOW_DISPLAY (SSH 포워딩)"
else
  echo "🖥  퀴즈 창 → .env 의 QUIZ_WINDOW_DISPLAY 값"
fi

echo "🤖 얼굴 UI → $DISPLAY   |   오디오 → PulseAudio(echocancel)"
echo "   종료: 로봇에게 \"대화 종료\" 라고 말하거나 Ctrl+C"
exec ~/moti-venv/bin/python -u launcher.py "$@"
