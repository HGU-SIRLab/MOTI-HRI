"""마이크/스피커 오디오 I/O + 에코캔슬레이션(AEC).

launcher.py에서 인라인으로 관리하던 MicStreamer/Speaker를 여기로 뽑아냄(2026-07-27,
AEC 연결 시점 — docs/integration-points.md에 "AEC 연결할 때 media/로 뽑아내는 것도
같이 고려"라고 미리 적어뒀던 계획). AEC는 `aec-audio-processing`(WebRTC AEC3 파이썬
바인딩)을 쓴다 — 합성 신호로 30~36dB 에코 감쇠 확인됨(docs/progress.md 6단계 참고).
"""
import asyncio
import os
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

INPUT_RATE = 16000
OUTPUT_RATE = 24000

ENABLE_AEC = os.getenv("ENABLE_AEC", "true").lower() not in ("0", "false", "no")
# 스피커→마이크 왕복 지연 추정치(ms). 실측 안 된 값 — 에코가 잘 안 잡히면 이 값부터
# 조정해볼 것(오디오 버퍼 크기가 크면 지연도 커진다: 지금 블록사이즈 기준 대략 100ms대).
AEC_STREAM_DELAY_MS = int(os.getenv("AEC_STREAM_DELAY_MS", "100"))


class EchoCanceller:
    """근단(마이크)/원단(스피커) 오디오를 하나의 AEC 엔진으로 처리한다.
    MicStreamer/Speaker 콜백은 서로 다른 오디오 스레드에서 동시에 호출될 수 있어
    내부 `AudioProcessor` 접근을 락으로 보호한다.

    `process_reverse_stream()`은 `set_reverse_stream_format()`으로 지정한 레이트가
    아니라 근단(마이크) 프레임 크기를 그대로 요구한다(실측으로 확인된 함정) — 그래서
    원단(스피커, 24kHz)을 근단 레이트(마이크, 16kHz)로 리샘플링해서 먹인다
    (24000/16000 = 3/2라 정확한 유리비 리샘플링 가능).
    """

    def __init__(self, stream_delay_ms: int = AEC_STREAM_DELAY_MS):
        import aec_audio_processing as aec

        self._ap = aec.AudioProcessor(enable_aec=True, enable_ns=False,
                                       enable_agc=False, enable_vad=False)
        self._ap.set_stream_format(sample_rate_in=INPUT_RATE, channel_count_in=1)
        self._ap.set_reverse_stream_format(sample_rate_in=INPUT_RATE, channel_count_in=1)
        self._ap.set_stream_delay(stream_delay_ms)
        self._frame_bytes = self._ap.get_frame_size() * 2  # int16 = 2 bytes/sample
        self._lock = threading.Lock()
        self._far_leftover = b""

    def process_near(self, pcm_bytes: bytes) -> bytes:
        """마이크로 캡처한 오디오(16kHz int16 mono)를 AEC에 통과시켜 반환한다."""
        n = (len(pcm_bytes) // self._frame_bytes) * self._frame_bytes
        out = bytearray()
        with self._lock:
            for i in range(0, n, self._frame_bytes):
                out += self._ap.process_stream(pcm_bytes[i:i + self._frame_bytes])
        return bytes(out) + pcm_bytes[n:]

    def push_far(self, pcm_24k_bytes: bytes):
        """스피커로 나가는 오디오(24kHz int16 mono)를 16kHz로 리샘플링해 AEC의
        원단(참조) 스트림에 투입한다. 재생 자체와는 무관 — 반환값 없음."""
        audio = np.frombuffer(pcm_24k_bytes, dtype=np.int16)
        resampled = resample_poly(audio, up=2, down=3).astype(np.int16).tobytes()
        buf = self._far_leftover + resampled
        n = (len(buf) // self._frame_bytes) * self._frame_bytes
        with self._lock:
            for i in range(0, n, self._frame_bytes):
                self._ap.process_reverse_stream(buf[i:i + self._frame_bytes])
        self._far_leftover = buf[n:]


class MicStreamer:
    def __init__(self, loop: asyncio.AbstractEventLoop, echo_canceller: "EchoCanceller | None" = None):
        self._loop = loop
        self._aec = echo_canceller
        self.queue: asyncio.Queue = asyncio.Queue()
        self._stream = sd.InputStream(
            samplerate=INPUT_RATE, channels=1, dtype="int16",
            blocksize=1600, callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        chunk = bytes(indata)
        if self._aec is not None:
            chunk = self._aec.process_near(chunk)
        self._loop.call_soon_threadsafe(self.queue.put_nowait, chunk)

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


class Speaker:
    def __init__(self, echo_canceller: "EchoCanceller | None" = None):
        self._aec = echo_canceller
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._leftover = b""
        # 큐가 말라서 무음으로 메꾼 횟수/총 길이 — 실시간 콜백 안에서는 print 같은 블로킹
        # I/O를 하면 안 되므로(그 자체가 다음 콜백을 더 지연시켜 언더런을 악화시킬 수 있음)
        # 그냥 카운터만 늘리고, 세션이 끝난 뒤 launcher.py가 요약해서 한 번만 출력한다.
        #
        # 콜백 자체는 스트림이 열려있는 내내 계속 돈다 — 모티가 말을 안 하고 사용자가
        # 말하는 중이거나 대화 사이 공백일 때도 큐는 당연히 비어있다. 그걸 전부 "언더런"으로
        # 세면 숫자가 크게 부풀려진다(실측: 실제 대화에서 1764회/175초로 찍혔는데 체감
        # 문제는 거의 없었음 — 대부분 그냥 "말 안 하는 시간"이었던 것). 그래서 마지막
        # play() 호출 후 SILENCE_GAP_SEC 이내에 큐가 마른 경우만 진짜 언더런으로 센다
        # (한창 재생 중인데 다음 조각이 안 와서 비는 경우) — 그보다 오래 조용했으면
        # "원래 할 말이 없는 시간"으로 보고 세지 않는다.
        self.underrun_count = 0
        self.underrun_ms_total = 0.0
        self._last_play_time = 0.0
        self.SILENCE_GAP_SEC = 2.0
        self._stream = sd.OutputStream(
            samplerate=OUTPUT_RATE, channels=1, dtype="int16",
            blocksize=2400, callback=self._callback,
        )

    def _callback(self, outdata, frames, time_info, status):
        need = frames * 2
        buf = self._leftover
        while len(buf) < need:
            try:
                buf += self._q.get_nowait()
            except queue.Empty:
                break
        chunk, self._leftover = buf[:need], buf[need:]
        shortfall = need - len(chunk)
        if shortfall > 0 and (time.monotonic() - self._last_play_time) < self.SILENCE_GAP_SEC:
            self.underrun_count += 1
            self.underrun_ms_total += (shortfall / 2) / OUTPUT_RATE * 1000
        chunk = chunk + b"\x00" * shortfall
        if self._aec is not None:
            self._aec.push_far(chunk)
        outdata[:] = np.frombuffer(chunk, dtype="int16").reshape(-1, 1)

    def play(self, pcm_bytes: bytes):
        self._last_play_time = time.monotonic()
        self._q.put(pcm_bytes)

    def stop_immediately(self):
        with self._q.mutex:
            self._q.queue.clear()
        self._leftover = b""

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()
