#!/usr/bin/env bash
# MOTI launcher.py 실행 래퍼 (Jetson Orin Nano Super)
#   ./run_jetson.sh            SSH 세션이든 로봇 콘솔이든 알아서 맞춤
#   ./run_jetson.sh 1          카메라 인덱스 지정 (기본 0 = C922)
set -e
cd "$(dirname "$0")"

# 1) SSH -X 로 들어왔으면 지금 이 셸의 $DISPLAY 가 포워딩 디스플레이(localhost:1x.0)다.
#    얼굴 UI 용으로 :1 을 덮어쓰기 전에 먼저 붙잡아 둔다.
INCOMING_DISPLAY="$DISPLAY"

# 2) 로봇 얼굴 UI 는 항상 :1 (이 보드는 GDM이 사용자 세션을 :1 에 띄움)
export DISPLAY=:1
export XAUTHORITY="$HOME/.Xauthority"
export PULSE_SERVER="unix:/run/user/$(id -u)/pulse/native"
export PYTHONUNBUFFERED=1

# 3) :1 쿠키가 ~/.Xauthority 에 없으면 gdm 쪽에서 합쳐 넣는다 (한 번만 하면 됨)
if ! xauth list 2>/dev/null | grep -q "/unix:1 "; then
  XAUTHORITY="/run/user/$(id -u)/gdm/Xauthority" xauth nextract - :1 2>/dev/null \
    | xauth nmerge - 2>/dev/null || true
fi

# 4) 퀴즈 창 목적지: SSH 포워딩 디스플레이가 있으면 거기(참가자 노트북), 없으면 .env 값 사용
if [ -n "$SSH_CONNECTION" ] && [ -n "$INCOMING_DISPLAY" ] && [ "$INCOMING_DISPLAY" != ":1" ]; then
  export QUIZ_WINDOW_DISPLAY="$INCOMING_DISPLAY"
  echo "🖥  퀴즈 창 → $QUIZ_WINDOW_DISPLAY (SSH 포워딩)"
else
  echo "🖥  퀴즈 창 → .env 의 QUIZ_WINDOW_DISPLAY 값"
fi

echo "🤖 얼굴 UI → :1   |   오디오 → PulseAudio(echocancel)"
echo "   종료: 로봇에게 \"대화 종료\" 라고 말하거나 Ctrl+C"
exec ~/moti-venv/bin/python -u launcher.py "$@"
