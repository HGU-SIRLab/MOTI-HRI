"""마이크/스피커 오디오 I/O + 에코캔슬레이션(AEC).

launcher.py에서 인라인으로 관리하던 MicStreamer/Speaker를 여기로 뽑아냄(2026-07-27,
AEC 연결 시점 — docs/integration-points.md에 "AEC 연결할 때 media/로 뽑아내는 것도
같이 고려"라고 미리 적어뒀던 계획). AEC는 `aec-audio-processing`(WebRTC AEC3 파이썬
바인딩)을 쓴다 — 합성 신호로 30~36dB 에코 감쇠 확인됨(docs/progress.md 6단계 참고).
"""
from __future__ import annotations
import asyncio
import os
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

INPUT_RATE = 16000
OUTPUT_RATE = 24000

ENABLE_AEC = os.getenv("ENABLE_AEC", "true").lower() not in ("0", "false", "no")
# aec-audio-processing(WebRTC AEC3 바인딩)은 aarch64에 미리 빌드된 휠이 없을 수 있다.
# 예전에는 EchoCanceller() 생성 시점에 ImportError가 터져 로봇이 기동 중에 죽었다 —
# 에코캔슬은 있으면 좋은 기능이지 없으면 못 도는 기능이 아니므로, 여기서 한 번 확인하고
# 없으면 끈 채로 계속 간다(2026-08-24 Jetson 이식).
if ENABLE_AEC:
    try:
        import aec_audio_processing as _aec_probe  # noqa: F401
    except ImportError:
        print("⚠️ aec-audio-processing이 없어 에코캔슬(AEC)을 끕니다.")
        print("   스피커 소리가 마이크로 되돌아가 barge-in 오탐이 늘 수 있으니, 가능하면")
        print("   스피커 볼륨을 낮추거나 마이크를 스피커에서 떨어뜨려 두세요.")
        ENABLE_AEC = False
# 스피커→마이크 왕복 지연 추정치(ms). 실측 안 된 값 — 에코가 잘 안 잡히면 이 값부터
# 조정해볼 것(오디오 버퍼 크기가 크면 지연도 커진다: 지금 블록사이즈 기준 대략 100ms대).
AEC_STREAM_DELAY_MS = int(os.getenv("AEC_STREAM_DELAY_MS", "100"))

# 플레이아웃 지터 버퍼(2026-08-10, 40단계) — 재생을 시작하기 전에 최소한 이만큼을 쌓아둔다.
#
# 왜 필요한가: VoiceShifter는 buffer_ms만큼 오디오가 "다 모인 뒤에" 한꺼번에 변조해서
# 한 블록을 통째로 밀어넣고, 스피커는 그걸 실시간으로 빼간다. 그래서 서버가 오디오를
# 실시간 속도로 보내주는 구간에서는 블록 N의 변조가 끝나는 시각과 스피커가 블록 N-1을
# 다 재생하는 시각이 **정확히 같다** — 여유가 0이라, 네트워크나 스케줄링이 조금만
# 흔들려도 그 자리에서 무음이 끼어든다("말하다 중간에 먹히는" 증상의 실제 원인).
# 중요한 건 이 여유가 buffer_ms와 **무관하게** 항상 0이라는 점이다 — 그래서 예전에
# 500 -> 700 -> 1200ms로 버퍼를 키웠는데도 증상이 그대로였다(오히려 발화 시작 지연만
# 늘었다). 진짜 해법은 버퍼 크기가 아니라, 재생 시작 자체를 늦춰서 쿠션을 만드는 것.
PLAYOUT_PRIME_MS = int(os.getenv("PLAYOUT_PRIME_MS", "600"))
# 아주 짧은 대답("네!")은 PLAYOUT_PRIME_MS를 영영 못 채울 수 있으니, 첫 오디오가 들어온
# 뒤 이 시간이 지나면 덜 찼어도 그냥 재생을 시작한다.
PLAYOUT_PRIME_TIMEOUT_SEC = float(os.getenv("PLAYOUT_PRIME_TIMEOUT_SEC", "0.5"))


# ---------------------------------------------------------------------------
# 입출력 장치 선택 (2026-08-24, Jetson 이식)
#
# 지금까지는 sounddevice의 기본 장치를 그냥 썼다. Windows에서는 그게 대체로 맞았지만
# 리눅스/Jetson에서는 ALSA 기본 장치가 HDMI 출력이나 엉뚱한 캡처 장치로 잡히는 일이
# 흔하다("소리는 안 나는데 에러도 안 남"의 전형적 원인). 그래서 .env로 장치를 못 박을 수
# 있게 한다 — 인덱스(예: 11)로도, 이름 일부(예: "USB Audio")로도 지정할 수 있다.
# 사용 가능한 장치 목록은 `python scripts/jetson_doctor.py --audio` 로 확인.
# ---------------------------------------------------------------------------
MIC_DEVICE = os.getenv("MIC_DEVICE", "").strip()
SPEAKER_DEVICE = os.getenv("SPEAKER_DEVICE", "").strip()


def resolve_device(spec: str, kind: str):
    """.env 값(인덱스 또는 이름 일부)을 sounddevice 장치 인덱스로 바꾼다.
    빈 값이면 None(=기본 장치)을 돌려준다. kind는 'input' 또는 'output'."""
    if not spec:
        return None
    if spec.lstrip("-").isdigit():
        return int(spec)

    want = spec.lower()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    matches = [(i, d) for i, d in enumerate(sd.query_devices())
               if d[key] > 0 and want in d["name"].lower()]
    if not matches:
        raise RuntimeError(
            f"{kind} 장치 중 이름에 '{spec}'가 들어가는 것을 찾지 못했습니다. "
            f"`python scripts/jetson_doctor.py --audio`로 목록을 확인하세요."
        )
    idx, dev = matches[0]
    if len(matches) > 1:
        print(f"ℹ️ '{spec}'에 맞는 {kind} 장치가 여럿입니다 — 첫 번째({dev['name']})를 씁니다.")
    print(f"🎚️ {kind} 장치: [{idx}] {dev['name']}")
    return idx


def describe_devices() -> str:
    """현재 잡힌 입출력 장치를 사람이 읽는 한 줄로. 세션 시작 로그용."""
    try:
        din = sd.query_devices(resolve_device(MIC_DEVICE, "input"), "input")
        dout = sd.query_devices(resolve_device(SPEAKER_DEVICE, "output"), "output")
        return f"🎤 입력: {din['name']}   🔊 출력: {dout['name']}"
    except Exception as e:
        return f"⚠️ 오디오 장치 확인 실패: {e}"


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
            device=resolve_device(MIC_DEVICE, "input"),
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
    """재생 대기 오디오를 하나의 bytearray로 들고 있다가 출력 콜백에 실어 보낸다.

    플레이아웃 지터 버퍼(2026-08-10): 버퍼가 빈 상태에서 새 발화가 시작되면 곧장
    재생하지 않고 PLAYOUT_PRIME_MS만큼(또는 PLAYOUT_PRIME_TIMEOUT_SEC까지) 먼저
    쌓아둔 다음 재생을 시작한다 — 위 PLAYOUT_PRIME_MS 주석 참고. 재생 도중 버퍼가
    마르면 다시 이 대기 상태로 돌아가, 같은 자리에서 잘게 반복해서 끊기지 않게 한다.
    """

    def __init__(self, echo_canceller: "EchoCanceller | None" = None):
        self._aec = echo_canceller
        # 콜백(오디오 스레드)과 play()/stop_immediately()(recv_loop 스레드)가 같이
        # 건드리므로 락으로 보호한다. 콜백이 잡는 구간은 슬라이스 하나뿐이라 짧다.
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._prime_bytes = int(OUTPUT_RATE * PLAYOUT_PRIME_MS / 1000) * 2
        self._priming = True
        self._prime_since: float | None = None
        # 발화 도중 재생이 끊겼던 횟수/총 길이 — 실시간 콜백 안에서는 print 같은 블로킹
        # I/O를 하면 안 되므로(그 자체가 다음 콜백을 더 지연시켜 상황을 악화시킬 수 있음)
        # 그냥 카운터만 늘리고, 세션이 끝난 뒤 launcher.py가 요약해서 한 번만 출력한다.
        #
        # 세는 방식(2026-08-10에 재작성): "버퍼가 빈 순간부터 재생이 실제로 재개되기까지"를
        # 하나의 끊김으로 보고 그 길이를 통째로 잰다. 예전에는 콜백 한 번의 모자란 바이트만
        # 셌는데, 버퍼가 마르면 쿠션을 다시 채우느라 그 뒤로도 계속 무음이 나가고 그 구간은
        # priming이라 세지 않아서 **실제 무음의 1/5밖에 보고하지 않았다**(실측: 진짜 520ms
        # 무음을 "2회/100ms"로 보고). 실물 로그의 "16회/638ms"도 그래서 실제보다 한참 작은
        # 숫자였다. 이제는 끊긴 구간 전체를 잰다.
        #
        # 발화 사이의 정상적인 침묵(모티가 할 말이 없는 시간, 사용자가 말하는 시간)까지
        # 세면 숫자가 무의미해지므로(예전에 1764회/175초로 부풀려진 적 있음), 재생이 다시
        # 시작될 때까지의 공백이 MAX_MIDSPEECH_GAP_SEC 이하일 때만 "발화 도중 끊김"으로
        # 본다 — 그보다 길면 원래 조용한 구간이었다고 보고 세지 않는다.
        self.underrun_count = 0
        self.underrun_ms_total = 0.0
        self._dry_since: float | None = None
        self.MAX_MIDSPEECH_GAP_SEC = 1.5
        self._stream = sd.OutputStream(
            samplerate=OUTPUT_RATE, channels=1, dtype="int16",
            blocksize=2400, callback=self._callback,
            device=resolve_device(SPEAKER_DEVICE, "output"),
        )

    @property
    def pending_sec(self) -> float:
        """아직 재생되지 않고 남아있는 오디오 길이(초). launcher.py가 "로봇이 실제로
        말을 다 끝냈는지"를 고정 상수로 어림잡지 않고 물어보는 데 쓴다."""
        with self._lock:
            return len(self._buf) / 2 / OUTPUT_RATE

    def _callback(self, outdata, frames, time_info, status):
        need = frames * 2
        now = time.monotonic()
        resumed_after = None
        with self._lock:
            if self._priming and self._buf:
                filled = len(self._buf) >= self._prime_bytes
                timed_out = (self._prime_since is not None
                             and now - self._prime_since >= PLAYOUT_PRIME_TIMEOUT_SEC)
                if filled or timed_out:
                    self._priming = False
            if self._priming:
                chunk = b""
            else:
                chunk = bytes(self._buf[:need])
                del self._buf[:need]
                if chunk and self._dry_since is not None:
                    # 재생이 실제로 재개된 순간에야 "얼마나 비어 있었는지"가 확정된다.
                    resumed_after = now - self._dry_since
                    self._dry_since = None
                if not self._buf:
                    # 다 비었다 — 다음 발화는 쿠션을 다시 채우고 시작해야 같은 자리에서
                    # 또 끊기지 않는다(정상적인 발화 종료도 여기로 온다. 그 경우엔 다음
                    # 재생까지의 간격이 길어서 아래 판정에서 끊김으로 안 세어진다).
                    self._priming = True
                    self._prime_since = None
                    if self._dry_since is None:
                        self._dry_since = now
        if resumed_after is not None and resumed_after <= self.MAX_MIDSPEECH_GAP_SEC:
            self.underrun_count += 1
            self.underrun_ms_total += resumed_after * 1000
        chunk = chunk + b"\x00" * (need - len(chunk))
        if self._aec is not None:
            self._aec.push_far(chunk)
        outdata[:] = np.frombuffer(chunk, dtype="int16").reshape(-1, 1)

    def play(self, pcm_bytes: bytes):
        with self._lock:
            self._buf += pcm_bytes
            if self._priming and self._prime_since is None:
                self._prime_since = time.monotonic()

    def stop_immediately(self):
        with self._lock:
            self._buf.clear()
            self._priming = True
            self._prime_since = None
            # barge-in으로 일부러 끊은 것이라 "발화 도중 끊김"이 아니다.
            self._dry_since = None

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()
