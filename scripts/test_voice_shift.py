"""media/voice_shift.py의 VoiceShifter 오버랩-크로스페이드 + reset 세대 로직 검증.
API 키/로봇/오디오 장치 불필요 — 콜백으로 나온 바이트만 검사한다.

1) 항등 변환 플럼빙: shift_pcm을 항등 함수로 패치하면 크로스페이드가 "같은 값끼리 섞기"가
   되므로, 출력이 입력과 바이트 단위로 완전히 같아야 한다(오버랩 홀드백/이어붙이기/길이
   계산이 전부 정확하다는 가장 강한 증거).
2) reset 세대: barge-in으로 reset()한 뒤에는 (a) 큐에 이미 쌓여있던 오디오와 (b) 그 순간
   pyworld 처리 중이던 오디오 둘 다 재생되면 안 된다 — 느린 변환 함수로 패치해 재현.
3) 실제 pyworld 스모크: 사인파를 실제로 변조해 길이 보존과 청크 경계의 급격한 점프가
   없는지(크로스페이드 효과) 확인한다.

사용:
    python scripts/test_voice_shift.py
"""
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

import numpy as np

from media import voice_shift as vs

SR = 24000


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def run_shifter(feeds: list[bytes], buffer_ms: int, overlap_ms: int,
                reset_after_first_feed: bool = False, drain_sec: float = 1.0) -> bytes:
    """VoiceShifter에 feeds를 순서대로 넣고 flush한 뒤, 콜백으로 나온 바이트를 이어 반환."""
    out: list[bytes] = []
    shifter = vs.VoiceShifter(out.append, sample_rate=SR, buffer_ms=buffer_ms, overlap_ms=overlap_ms)
    shifter.start()
    try:
        for i, pcm in enumerate(feeds):
            shifter.feed(pcm)
            if reset_after_first_feed and i == 0:
                time.sleep(0.05)  # 워커가 첫 청크를 집어 처리(느린 패치 함수)에 들어갈 시간
                shifter.reset()
        shifter.flush()
        deadline = time.monotonic() + drain_sec
        while time.monotonic() < deadline and not shifter._in_q.empty():
            time.sleep(0.02)
        time.sleep(0.1)  # 마지막 블록 처리 마무리 여유
    finally:
        shifter.close()
    return b"".join(out)


def main():
    ok = True
    original_shift_pcm = vs.shift_pcm

    # ---- 1) 항등 변환 플럼빙: 출력 == 입력(바이트 단위) ----
    vs.shift_pcm = lambda pcm, sr, **kw: pcm
    ramp = np.arange(0, 24000, dtype=np.int16)  # 1초 분량의 단조 증가 램프(위치 어긋남에 민감)
    data = ramp.tobytes()
    # 버퍼(150ms)와 안 나눠떨어지는 크기로 잘게 나눠 feed — 경계 정렬 실수를 드러내기 위함.
    feeds = [data[i:i + 7000] for i in range(0, len(data), 7000)]
    result = run_shifter(feeds, buffer_ms=150, overlap_ms=30)
    ok &= check("identity: total output length equals input", len(result) == len(data))
    ok &= check("identity: output is byte-for-byte identical to input", result == data)

    # ---- 2) reset 세대: 처리 중이던 것 + 큐에 쌓인 것 둘 다 폐기 ----
    def slow_identity(pcm, sr, **kw):
        time.sleep(0.2)
        return pcm

    vs.shift_pcm = slow_identity
    block = int(SR * 150 / 1000) * 2  # buffer_ms=150 기준 한 블록 크기(bytes)
    a = (np.full(block // 2, 1000, dtype=np.int16)).tobytes()   # reset 이전(폐기 대상)
    a2 = (np.full(block // 2, 1500, dtype=np.int16)).tobytes()  # reset 이전, 큐 대기(폐기 대상)
    b = (np.full(block // 2, 2000, dtype=np.int16)).tobytes()   # reset 이후(살아야 함)
    result = run_shifter([a + a2, b], buffer_ms=150, overlap_ms=30,
                          reset_after_first_feed=True, drain_sec=3.0)
    values = set(np.frombuffer(result, dtype=np.int16).tolist())
    ok &= check("reset: pre-reset audio (in-flight and queued) never plays",
                1000 not in values and 1500 not in values)
    ok &= check("reset: post-reset audio still plays", 2000 in values)

    # ---- 3) 실제 pyworld 스모크: 길이 보존 + 경계 불연속 없음 ----
    vs.shift_pcm = original_shift_pcm
    t = np.arange(SR)  # 1초
    sine = (np.sin(2 * np.pi * 220 * t / SR) * 8000).astype(np.int16)
    data = sine.tobytes()
    feeds = [data[i:i + block] for i in range(0, len(data), block)]
    t0 = time.monotonic()
    result = run_shifter(feeds, buffer_ms=300, overlap_ms=120, drain_sec=15.0)
    elapsed = time.monotonic() - t0
    out_arr = np.frombuffer(result, dtype=np.int16).astype(np.int32)
    ok &= check("pyworld: total output length equals input", len(result) == len(data))
    ok &= check("pyworld: output has real energy (not silence)", np.abs(out_arr).max() > 1000)
    # 크로스페이드가 없으면 청크 경계에서 위상 불일치로 수만 단위 점프가 날 수 있다 —
    # 220Hz/8000진폭 사인파의 정상적인 인접 샘플 차이는 ~700 수준이라 4000이면 넉넉한 상한.
    max_jump = int(np.abs(np.diff(out_arr)).max()) if len(out_arr) > 1 else 0
    ok &= check(f"pyworld: no hard discontinuity at chunk boundaries (max jump {max_jump} < 4000)",
                max_jump < 4000)
    print(f"    (1초 오디오 변조에 {elapsed:.2f}초 소요 — 실시간 배수 {elapsed:.2f}x)")

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
