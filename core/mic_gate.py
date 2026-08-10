"""퀴즈 중 barge-in(끼어들기) 차단 판단 — asyncio/오디오 장치/세션을 몰라야 launcher.py
없이 유닛 테스트할 수 있다(core/idle_watcher.py와 같은 이유로 순수 로직만 분리).

배경(2026-08-10, 42~43단계): 퀴즈 중에는 참가자가 로봇 말을 끊지 못하게 해달라는 요청이
있었다. barge-in은 Live API 서버의 자동 VAD가 처리하고 그 설정은 연결 시점에 고정돼 세션
도중에는 못 바꾸므로(system_instruction/tools와 같은 제약), 서버 설정을 끄는 대신 **로봇이
말하는 동안 마이크 오디오를 서버로 보내지 않는** 방식으로 구현한다.

이 판단이 틀리면 대가가 비대칭적이다:
  - 너무 안 막으면: 참가자가 로봇 말을 끊는다(원래 있던 동작, 불편한 정도).
  - 너무 막으면: 사용자가 아무리 말해도 서버가 못 듣는다 = **대화 자체가 불가능**.
후자가 실제로 발생했다(43단계). GoAway로 세션이 발화 도중 끊기면 turn_complete가 영영
안 와서 VoiceShifter가 크로스페이드 꼬리(120ms)를 계속 붙잡고, 그 탓에 "재생 잔량이
있다 = 아직 말하는 중"이라는 판정이 영구히 참이 됐다. 재연결 시 잔량을 정리하는 근본
수정은 launcher.py에서 따로 했지만, 여기서도 **시간 상한**을 둬서 어떤 신호가 잘못
남아있든 게이트가 반드시 풀리게 한다 — 이 실패 모드는 다시는 나면 안 된다.
"""

# 마지막 오디오 청크가 도착한 뒤 이 시간이 지나면 무조건 마이크를 연다. 재생은 생성보다
# 최대 1.5초 정도 뒤처지므로(플레이아웃 쿠션 + 변조 버퍼), 3초면 "아직 말하는 중"일
# 수 없는 여유 있는 상한이다.
MAX_MIC_WITHHOLD_SEC = 3.0

# SLEEPY 상태에서 조용한 마이크 오디오를 서버로 안 보내기 위한 문턱값(int16 RMS).
# 배경(2026-08-10): 로봇은 잠들어 있는 동안에도 마이크를 초당 계속 서버로 올리고 있었고,
# Live API는 그 오디오를 전부 입력 토큰으로 과금한다 — 참가자 사이사이 로봇을 켜둔 시간이
# 그대로 비용이 된다. 다만 **깨우기 경로가 바로 이 오디오**라는 점이 핵심 제약이다:
# 서버가 사용자 발화를 전사(input_transcription)해줘야 launcher가 깨어나므로, 무조건
# 막으면 로봇이 영영 안 깨어난다. 그래서 "조용한 구간만" 버리고 사람 목소리 크기면
# 그대로 올린다. int16 풀스케일이 32768이므로 500은 약 1.5% — 조용한 실내 소음(보통
# 1% 미만)보다는 위이고 근거리 발화(보통 5~30%)보다는 훨씬 아래라, 말을 걸면 반드시
# 통과한다. 의심스러우면 .env의 SLEEP_MIC_RMS_THRESHOLD=0으로 이 기능만 끌 수 있다.
SLEEP_MIC_RMS_THRESHOLD = 500.0


def should_send_while_sleeping(is_sleeping: bool, chunk_rms: float,
                               threshold: float = SLEEP_MIC_RMS_THRESHOLD) -> bool:
    """SLEEPY 상태에서 이 마이크 청크를 서버로 보내야 하면 True.

    깨어있을 때는 항상 True(기존 동작 그대로) — 이 게이트는 오직 잠든 동안에만 작동한다.
    threshold가 0 이하면 기능 자체가 꺼진 것으로 보고 항상 보낸다.
    """
    if not is_sleeping or threshold <= 0:
        return True
    return chunk_rms >= threshold


def decide_withhold_mic(quiz_active: bool, robot_speaking: bool,
                        sec_since_last_audio: float,
                        enabled: bool = True,
                        max_withhold_sec: float = MAX_MIC_WITHHOLD_SEC) -> bool:
    """마이크 오디오를 서버로 보내지 **않아야** 하면 True.

    quiz_active: 퀴즈가 진행 중인가(퀴즈 밖 일반 대화의 barge-in은 v3의 핵심 기능이라
        절대 막지 않는다 — AEC를 도입한 이유 자체가 barge-in을 살리기 위해서였다).
    robot_speaking: 로봇이 생성 중이거나 아직 스피커로 흘러나오는 중인가.
    sec_since_last_audio: 로봇 오디오 청크가 마지막으로 도착한 뒤 지난 시간(초).
    enabled: .env의 QUIZ_DISABLE_BARGE_IN — 문제가 생기면 통째로 끌 수 있게.
    """
    if not enabled or not quiz_active:
        return False
    if sec_since_last_audio > max_withhold_sec:
        # 신호가 뭐라고 하든 로봇이 아직 말하는 중일 수 없다 — 위 주석의 실패 모드 방지.
        return False
    return robot_speaking
