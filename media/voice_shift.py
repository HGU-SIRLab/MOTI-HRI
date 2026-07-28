"""실시간 오디오 피치+포먼트 시프트 — Gemini Live 목소리를 더 앳되게 들리도록 후처리한다.

배경(docs/progress.md 참고): Gemini Live 프리셋 30종은 전부 성인 성우 녹음이 원본이라
프롬프트로 톤/속도를 아무리 밀어붙여도 "성인이 애교 부리는" 느낌을 못 벗어난다. WORLD
보코더(pyworld)로 피치와 포먼트(성도 길이감)를 동시에 조작하면 실제로 "몸집이 작은
화자"에 가까운 인상을 줄 수 있다 — 단순 피치 시프트만으로는 속도까지 빨라진 "다람쥐
소리"가 되므로 반드시 포먼트도 같이 다뤄야 함.

F0 추출은 `pw.dio`(빠름, ~47ms/500ms청크)가 아니라 `pw.harvest`(느림, ~156ms/500ms청크)를
쓴다 — 실측으로 dio가 짧고 모호한 유성음 구간에서 피치를 잘못 잡아(예: "특별한"의 파열음
근처) 지글거리는 잡음을 냈고, harvest로 바꾸니 해결됐다. 500ms 청크 기준 harvest도 여전히
실시간 예산(최악 56%) 안에 들어온다.

재합성 결과물의 피크 진폭이 원본보다 커지는 경향이 있어(원본 대비 최대 +36%까지 관찰됨)
정규화 없이 그대로 재생하면 파열음 근처에서 하드 클리핑이 난다 — 반드시 피크를 0.98
이하로 스케일해야 한다.
"""
import os
import queue
import threading

import numpy as np
import pyworld as pw

ENABLE_VOICE_SHIFT = os.getenv("ENABLE_VOICE_SHIFT", "true").lower() not in ("0", "false", "no")
# +4st/x1.15로 시작했으나 프로덕션(청크) 방식에서 살짝 기계음이 섞여 들린다는 피드백으로
# 강도를 조금 낮춤 — "귀여움"은 거의 유지하면서 기계음이 줄어드는 지점으로 확정(2026-07-28).
VOICE_PITCH_SEMITONES = float(os.getenv("VOICE_PITCH_SEMITONES", "3.5"))
VOICE_FORMANT_RATIO = float(os.getenv("VOICE_FORMANT_RATIO", "1.12"))
# 이 이하로 버퍼가 쌓이면 harvest 분석이 불안정해질 수 있어(맥락이 너무 짧음) 그냥
# 원본을 통과시킨다 — 세션 종료 직전 아주 짧은 꼬리 조각 등에서만 발생.
MIN_SHIFT_SAMPLES = 2400  # 24kHz 기준 100ms

# 로봇 실사용(얼굴추적/모터/표정 UI가 전부 같이 도는 상황)에서 "지직거림"·"대화가 먹힘"
# 제보 발생(2026-07-28) — 개발 PC 단독 벤치마크(500ms 청크 평균 156ms)로는 여유 있었지만,
# 실제 로봇은 다른 스레드들과 CPU를 나눠 써서 harvest 처리가 500ms를 넘기면 Speaker의
# 재생 큐가 말라 무음으로 메꿔지는 언더런이 생길 수 있다(Speaker.underrun_count 참고).
# 버퍼를 넉넉하게 잡아 여유를 늘린다 — 그만큼 발화 시작 지연도 조금 늘어남(트레이드오프).
VOICE_SHIFT_BUFFER_MS = int(os.getenv("VOICE_SHIFT_BUFFER_MS", "700"))


def shift_pcm(pcm_bytes: bytes, sample_rate: int,
              pitch_semitones: float = VOICE_PITCH_SEMITONES,
              formant_ratio: float = VOICE_FORMANT_RATIO) -> bytes:
    """int16 PCM 바이트를 받아 피치+포먼트를 시프트한 int16 PCM 바이트를 돌려준다.
    너무 짧거나 무음이면(분석 불가) 원본을 그대로 돌려준다."""
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64) / 32768.0
    if len(audio) < MIN_SHIFT_SAMPLES or not np.any(audio):
        return pcm_bytes

    f0, t = pw.harvest(audio, sample_rate)
    f0 = pw.stonemask(audio, f0, t, sample_rate)
    sp = pw.cheaptrick(audio, f0, t, sample_rate)
    ap = pw.d4c(audio, f0, t, sample_rate)

    f0_shifted = f0 * (2.0 ** (pitch_semitones / 12.0))
    freq_axis = np.linspace(0, sample_rate / 2, sp.shape[1])
    warped_axis = freq_axis * formant_ratio
    sp_shifted = np.empty_like(sp)
    for i in range(sp.shape[0]):
        sp_shifted[i] = np.interp(freq_axis, warped_axis, sp[i], left=0, right=sp[i, -1])

    y = pw.synthesize(f0_shifted, sp_shifted, ap, sample_rate)
    peak = np.abs(y).max()
    if peak > 0.98:
        y = y / peak * 0.98
    return np.clip(y * 32768.0, -32768, 32767).astype(np.int16).tobytes()


class VoiceShifter:
    """스트리밍 PCM 청크를 버퍼링했다가 `buffer_ms`만큼 모이면 pyworld로 시프트해
    `on_shifted` 콜백(보통 Speaker.play)으로 넘기는 전용 스레드.

    pyworld 처리는 CPU 바운드 블로킹 작업이라 asyncio 이벤트 루프에서 직접 돌리면 안
    된다(recv_loop의 다른 이벤트 처리가 수백 ms씩 밀림) — MicStreamer/Speaker와 같은
    패턴으로 별도 스레드+큐를 쓴다.
    """

    def __init__(self, on_shifted, sample_rate: int, buffer_ms: int = VOICE_SHIFT_BUFFER_MS):
        self._on_shifted = on_shifted
        self._sr = sample_rate
        self._buffer_bytes = int(sample_rate * buffer_ms / 1000) * 2  # int16 = 2 bytes/sample
        self._buf = bytearray()
        self._in_q: "queue.Queue[tuple[str, bytes | None]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="voice-shift", daemon=True)

    def feed(self, pcm_bytes: bytes):
        self._in_q.put(("data", pcm_bytes))

    def flush(self):
        """턴이 끝났을 때 버퍼 임계값 미만으로 남은 꼬리를 그대로 흘려보낸다."""
        self._in_q.put(("flush", None))

    def reset(self):
        """barge-in 등으로 재생을 즉시 끊을 때 — 버퍼링 중이던 미처리 오디오는
        재생하지 않고 폐기한다(Speaker.stop_immediately()와 함께 호출할 것)."""
        self._in_q.put(("reset", None))

    def _run(self):
        while not self._stop.is_set():
            try:
                kind, payload = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue

            if kind == "reset":
                self._buf.clear()
            elif kind == "data":
                self._buf.extend(payload)
                while len(self._buf) >= self._buffer_bytes:
                    chunk = bytes(self._buf[:self._buffer_bytes])
                    del self._buf[:self._buffer_bytes]
                    self._process_and_emit(chunk)
            elif kind == "flush":
                if self._buf:
                    chunk = bytes(self._buf)
                    self._buf.clear()
                    self._process_and_emit(chunk)

    def _process_and_emit(self, pcm_bytes: bytes):
        try:
            shifted = shift_pcm(pcm_bytes, self._sr)
        except Exception as e:
            print(f"⚠️ 음성 변조 실패, 원본 그대로 재생: {e}")
            shifted = pcm_bytes
        self._on_shifted(shifted)

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
