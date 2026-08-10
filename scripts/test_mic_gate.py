"""core/mic_gate.py 검증 — 퀴즈 중 barge-in 차단 판단. 로봇/API/오디오 장치 불필요.

이 게이트는 틀렸을 때 대가가 비대칭적이다: 안 막으면 참가자가 로봇 말을 끊는 정도지만,
잘못 막으면 사용자가 아무리 말해도 서버가 못 들어 **대화 자체가 불가능**해진다.
2026-08-10에 실제로 그 사고가 났으므로(GoAway 이후 재생 잔량이 안 내려가 게이트가 영구히
닫힘), 특히 "반드시 풀린다"는 쪽을 집중적으로 검증한다.

사용: python scripts/test_mic_gate.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core.mic_gate import (MAX_MIC_WITHHOLD_SEC, SLEEP_MIC_RMS_THRESHOLD,
                            decide_withhold_mic, should_send_while_sleeping)


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def main():
    ok = True

    # 기본 동작: 퀴즈 중 로봇이 말하는 동안만 막는다.
    ok &= check("퀴즈 중 로봇이 말하는 동안은 마이크를 막는다",
                decide_withhold_mic(quiz_active=True, robot_speaking=True,
                                    sec_since_last_audio=0.2) is True)
    ok &= check("퀴즈 중이라도 로봇이 말하지 않으면 막지 않는다",
                decide_withhold_mic(quiz_active=True, robot_speaking=False,
                                    sec_since_last_audio=0.2) is False)

    # 퀴즈 밖 일반 대화의 barge-in은 v3의 핵심 기능 — 절대 막으면 안 된다.
    ok &= check("퀴즈가 아니면 로봇이 말하는 중이어도 절대 막지 않는다",
                decide_withhold_mic(quiz_active=False, robot_speaking=True,
                                    sec_since_last_audio=0.1) is False)

    # 끌 수 있어야 한다(.env의 QUIZ_DISABLE_BARGE_IN=false).
    ok &= check("enabled=False면 어떤 상태에서도 막지 않는다",
                decide_withhold_mic(quiz_active=True, robot_speaking=True,
                                    sec_since_last_audio=0.1, enabled=False) is False)

    # 회귀 방지의 핵심(2026-08-10 실제 사고): 재생 잔량 신호가 잘못 남아 robot_speaking이
    # 영원히 True가 되더라도, 마지막 오디오가 온 지 오래됐으면 반드시 풀려야 한다.
    ok &= check("오디오가 끊긴 지 오래면 robot_speaking이 True로 박혀 있어도 마이크를 연다",
                decide_withhold_mic(quiz_active=True, robot_speaking=True,
                                    sec_since_last_audio=MAX_MIC_WITHHOLD_SEC + 0.1) is False)
    ok &= check("상한 직전까지는 정상적으로 막는다",
                decide_withhold_mic(quiz_active=True, robot_speaking=True,
                                    sec_since_last_audio=MAX_MIC_WITHHOLD_SEC - 0.1) is True)
    ok &= check("세션이 한 번도 말한 적 없는 초기 상태(아주 큰 경과시간)에서도 열려 있다",
                decide_withhold_mic(quiz_active=True, robot_speaking=True,
                                    sec_since_last_audio=1e9) is False)

    # --- SLEEPY 상태의 마이크 업로드 게이트(2026-08-10, 토큰 절감) ---
    # 여기서도 실패 대가가 비대칭적이다: 너무 안 막으면 조용한 시간이 토큰으로 새고,
    # 너무 막으면 **로봇이 영영 안 깨어난다**(깨우기 신호가 바로 이 오디오의 전사다).
    # 그래서 "깨어 있을 때는 무조건 통과"와 "발화 크기는 무조건 통과"가 핵심 불변식.
    import numpy as np

    def rms(arr):
        a = arr.astype(np.float32)
        return float(np.sqrt(np.mean(a * a)))

    silence = np.zeros(1600, dtype=np.int16)
    room_noise = np.random.normal(0, 150, 1600).astype(np.int16)   # 조용한 실내
    speech = np.random.normal(0, 3000, 1600).astype(np.int16)      # 근거리 발화

    ok &= check("깨어 있을 때는 무음이든 뭐든 항상 서버로 보낸다(기존 동작 보존)",
                should_send_while_sleeping(False, rms(silence)) is True
                and should_send_while_sleeping(False, rms(room_noise)) is True)
    ok &= check("잠든 동안 무음/실내소음은 올리지 않는다",
                should_send_while_sleeping(True, rms(silence)) is False
                and should_send_while_sleeping(True, rms(room_noise)) is False)
    ok &= check("잠든 동안에도 사람 목소리 크기면 반드시 올린다(깨우기 경로 보존)",
                should_send_while_sleeping(True, rms(speech)) is True)
    ok &= check("문턱값을 0으로 두면 기능이 꺼져 예전처럼 전부 올린다",
                should_send_while_sleeping(True, 0.0, threshold=0.0) is True)
    ok &= check("문턱값 경계에서는 같거나 크면 통과한다",
                should_send_while_sleeping(True, SLEEP_MIC_RMS_THRESHOLD) is True
                and should_send_while_sleeping(True, SLEEP_MIC_RMS_THRESHOLD - 1) is False)

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
