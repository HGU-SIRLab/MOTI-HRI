"""재생 파이프라인(VoiceShifter -> Speaker)이 실시간 오디오 도착을 견디는지 측정한다.
실제 오디오 장치도 로봇도 API 키도 필요 없다 — sounddevice.OutputStream을 타이머
스레드 기반 가짜 장치로 바꿔치기해서 콜백 주기를 그대로 흉내낸다.

배경(2026-08-10, 40단계): "모티가 말하다 중간에 끊긴다"는 제보가 반복됐고 그때마다
VOICE_SHIFT_BUFFER_MS를 키워서 대응했지만(500 -> 700 -> 1200) 증상이 그대로였다.
원인은 CPU 부족이 아니라 구조였다 — 워커는 버퍼가 다 차야 변조를 시작하므로, 서버가
오디오를 실시간 속도로 보내는 구간에서는 "블록 N의 변조 완료"와 "블록 N의 재생 필요"
시각이 정확히 같아 여유가 0이 된다(버퍼 크기와 무관). 이 테스트는 그 사실을 숫자로
고정해서, 나중에 누가 다시 버퍼만 키우는 잘못된 처방으로 돌아가지 않게 한다.

사용: python scripts/test_playout_margin.py
"""
import os
import sys
import threading
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

import sounddevice as sd

import media.audio_manager as am
from media.voice_shift import VoiceShifter

SR = am.OUTPUT_RATE
BLOCK_FRAMES = 2400  # media/audio_manager.py의 Speaker가 쓰는 blocksize와 같아야 의미가 있다
SPEECH_SEC = 6.0
ARRIVAL_CHUNK_MS = 100  # Live API가 오디오를 흘려보내는 대략적인 단위

# 가짜 장치가 "스피커로 실제 나간" 샘플을 여기에 모은다 — 무음이 어디에 생겼는지
# 파형으로 직접 확인하려면 카운터가 아니라 이게 필요하다.
captured: list = []


class FakeOutputStream:
    """콜백을 blocksize/samplerate 주기로 계속 부르는 가짜 출력 장치."""

    def __init__(self, samplerate, channels, dtype, blocksize, callback):
        self._period = blocksize / samplerate
        self._frames = blocksize
        self._callback = callback
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        buf = np.zeros((self._frames, 1), dtype=np.int16)
        next_at = time.perf_counter()
        while not self._halt.is_set():
            next_at += self._period
            delay = next_at - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            self._callback(buf, self._frames, None, None)
            captured.append(buf.copy().reshape(-1))

    def start(self):
        self._thread.start()

    def stop(self):
        self._halt.set()
        self._thread.join(timeout=2)

    def close(self):
        pass


def make_speech(duration_sec: float, envelope_floor: float = 0.5) -> bytes:
    """말소리 비슷한 유성음 신호.

    envelope_floor는 포락선의 최저값 — 파형에서 무음 구간을 찾아내는 테스트
    (run_short_utterance)에서는 신호 자체가 0에 가까워지면 그걸 "끊김"으로 오검출하므로
    바닥을 높여서 쓴다. 언더런 카운터만 보는 쪽은 기본값 그대로도 무방하다.
    """
    n = int(SR * duration_sec)
    t = np.arange(n) / SR
    f0 = 150 + 30 * np.sin(2 * np.pi * 2.0 * t)
    phase = np.cumsum(2 * np.pi * f0 / SR)
    sig = sum((1.0 / k) * np.sin(k * phase) for k in range(1, 12))
    span = 1.0 - envelope_floor
    sig *= envelope_floor + span * (0.5 + 0.5 * np.sin(2 * np.pi * 1.7 * t))
    sig = sig / np.abs(sig).max() * 0.6
    return (sig * 32767).astype(np.int16).tobytes()


class BackgroundLoad:
    """실물 로봇에서 같이 도는 것들(얼굴추적 카메라 루프, 모터 통신, 표정 UI)이
    CPU를 나눠 쓰는 상황을 흉내낸다 — 개발 PC 단독 벤치마크에서는 안 보이던 지터가
    바로 여기서 나온다(실사용 제보와 벤치마크가 계속 어긋났던 이유)."""

    def __init__(self, workers=4):
        self._halt = threading.Event()
        self._threads = [threading.Thread(target=self._spin, daemon=True) for _ in range(workers)]

    def _spin(self):
        a = np.random.rand(256, 256)
        while not self._halt.is_set():
            np.fft.fft2(a)

    def __enter__(self):
        for t in self._threads:
            t.start()
        return self

    def __exit__(self, *exc):
        self._halt.set()
        for t in self._threads:
            t.join(timeout=2)


def run_short_utterance(dur_sec: float, flush_delay_sec: float):
    """짧은 발화 하나를 실시간으로 흘려보내고, 재생된 파형에서 발화 **도중** 무음을 찾는다.

    flush_delay_sec은 "마지막 오디오 청크가 도착한 뒤 turn_complete가 오기까지"의 지연 —
    Live API는 서버가 출력 전사 등을 마무리한 뒤에 turn_complete를 보내므로 항상 0이 아니다.
    예전에는 VoiceShifter가 마지막 자투리를 이 turn_complete까지 붙잡고 있어서, 지연이
    재생 쿠션보다 길면 **정확히 마지막 블록 경계**에서 무음이 들어갔다 — 발화가 짧을수록
    그 경계가 문장 한복판이라, "이 물건은 무엇일까요?"는 매번 같은 음절에서 끊겼다
    (2026-08-10 실물 제보, 41단계). 이제 유휴 방출이 있으므로 지연과 무관해야 한다.
    """
    captured.clear()
    speaker = am.Speaker(echo_canceller=None)
    shifter = VoiceShifter(speaker.play, sample_rate=SR)
    shifter.start()
    pcm = make_speech(dur_sec, envelope_floor=0.85)
    chunk_bytes = int(SR * ARRIVAL_CHUNK_MS / 1000) * 2
    started = time.perf_counter()
    with speaker:
        for i in range(0, len(pcm), chunk_bytes):
            due = started + (i / chunk_bytes) * (ARRIVAL_CHUNK_MS / 1000)
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            shifter.feed(pcm[i:i + chunk_bytes])
        time.sleep(flush_delay_sec)
        shifter.flush()
        deadline = time.perf_counter() + 12
        while time.perf_counter() < deadline:
            if speaker.pending_sec + shifter.pending_sec <= 0.01:
                break
            time.sleep(0.05)
        time.sleep(0.4)
    shifter.close()

    sig = np.concatenate(captured) if captured else np.zeros(1)
    win = 240  # 10ms
    nb = len(sig) // win
    loud = np.array([np.abs(sig[i * win:(i + 1) * win]).max() for i in range(nb)]) > 200
    if not loud.any():
        print("    (재생된 오디오가 없음)")
        return 1e9
    first, last = int(np.argmax(loud)), nb - 1 - int(np.argmax(loud[::-1]))
    silent_ms = int((last - first + 1 - loud[first:last + 1].sum()) * win / SR * 1000)
    print(f"    turn_complete 지연 {int(flush_delay_sec*1000):4d}ms → 발화 중 무음 {silent_ms:4d}ms "
          f"(언더런 카운터 보고: {speaker.underrun_count}회 / {speaker.underrun_ms_total:.0f}ms)")
    return silent_ms


def run_scenario(label: str, buffer_ms: int, prime_ms: int, speech: bytes):
    """오디오가 정확히 실시간 속도로 도착하는 상황을 재현하고 언더런을 센다."""
    am.PLAYOUT_PRIME_MS = prime_ms
    speaker = am.Speaker(echo_canceller=None)
    shifter = VoiceShifter(speaker.play, sample_rate=SR, buffer_ms=buffer_ms)
    shifter.start()

    chunk_bytes = int(SR * ARRIVAL_CHUNK_MS / 1000) * 2
    started = time.perf_counter()
    with speaker:
        for i in range(0, len(speech), chunk_bytes):
            # 서버가 실시간 속도로 보내주는 상황 — 이 페이싱이 이 테스트의 핵심이다.
            due = started + (i / chunk_bytes) * (ARRIVAL_CHUNK_MS / 1000)
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            shifter.feed(speech[i:i + chunk_bytes])
        shifter.flush()
        # 남은 재생분이 다 빠질 때까지 기다린다(launcher.py의 drain_playback과 같은 판정).
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline:
            if speaker.pending_sec + shifter.pending_sec <= 0.05:
                break
            time.sleep(0.05)
        time.sleep(0.3)
    shifter.close()

    gap_ms = speaker.underrun_ms_total
    print(f"  {label}")
    print(f"    버퍼 {buffer_ms}ms / 재생쿠션 {prime_ms}ms "
          f"→ 발화 중 무음 {gap_ms:.0f}ms ({speaker.underrun_count}회)")
    return gap_ms


def main():
    sd.OutputStream = FakeOutputStream  # 실제 장치 대신 가짜 장치를 쓴다
    am.sd = sd

    speech = make_speech(SPEECH_SEC)
    print(f"실시간 속도로 도착하는 {SPEECH_SEC:.0f}초 발화를 재생 파이프라인에 통과시킵니다.\n")

    prime_ms = int(os.getenv("PLAYOUT_PRIME_MS", "600"))

    print("── 개발 PC처럼 한가한 상태 ──")
    old_idle = run_scenario("이전(버퍼 1200 / 쿠션 없음)", 1200, 0, speech)
    new_idle = run_scenario("현재(버퍼 500 / 쿠션 있음)", 500, prime_ms, speech)

    print("\n── 실물 로봇처럼 CPU를 나눠 쓰는 상태 ──")
    with BackgroundLoad():
        old_busy = run_scenario("이전(버퍼 1200 / 쿠션 없음)", 1200, 0, speech)
        new_busy = run_scenario("현재(버퍼 500 / 쿠션 있음)", 500, prime_ms, speech)

    print("\n── 짧은 발화 + turn_complete 지연 (실물에서 실제로 났던 조건) ──")
    print('   "이 물건은 무엇일까요?" 정도(1.4초)를 서버 지연을 바꿔가며 재생')
    am.PLAYOUT_PRIME_MS = prime_ms
    short_gaps = [run_short_utterance(1.4, d) for d in (0.0, 0.3, 0.5, 0.8)]

    print()
    ok = True
    # 발화 6초 중 무음 100ms 이하면 사람 귀에 "끊겼다"고 들리지 않는 수준으로 본다.
    ok &= _check("한가한 상태에서 발화 중 무음이 거의 없다", new_idle <= 100)
    ok &= _check("CPU 경합 중에도 발화 중 무음이 거의 없다", new_busy <= 100)
    ok &= _check("CPU 경합에서 쿠션 없는 이전 설정보다 확실히 낫다", new_busy < old_busy)
    ok &= _check("turn_complete가 얼마나 늦게 오든 문장 끝이 끊기지 않는다",
                 max(short_gaps) <= 50)
    _ = old_idle

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


def _check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


if __name__ == "__main__":
    main()
