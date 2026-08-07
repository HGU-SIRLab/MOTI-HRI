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
# 실제 로봇은 다른 스레드들과 CPU를 나눠 써서 harvest 처리가 버퍼 크기를 넘기면 Speaker의
# 재생 큐가 말라 무음으로 메꿔지는 언더런이 생길 수 있다(Speaker.underrun_count 참고).
# 500ms → 700ms로 한 번 올렸는데도 실측(Speaker.underrun_count의 "말하는 중" 오탐 버그를
# 고친 뒤 재측정)으로 80회/7.8초(실제 발화 시간의 25~35%)가 나와 여전히 부족함을 확인 →
# 700 → 1200으로 재상향. 그만큼 발화 시작 지연도 늘어남(트레이드오프) — 안 좋아지면
# 700으로 롤백.
VOICE_SHIFT_BUFFER_MS = int(os.getenv("VOICE_SHIFT_BUFFER_MS", "1200"))

# 청크를 서로 독립적으로 pyworld 처리하면 경계에서 F0/스펙트럼 추정이 어긋나 톤이 뚝
# 끊기는 불연속이 생긴다 — "편하게 말씀해주세요"가 "말...씀"처럼 들리던 실사용 제보
# (docs/progress.md 14단계, 당시 오버랩-크로스페이드가 필요하다고 가설만 세우고 보류).
# 이제 각 청크 앞에 직전 청크의 입력 꼬리를 이 길이만큼 이어붙여 분석하고, 겹치는 구간의
# 출력은 선형 크로스페이드로 섞는다(2026-08-07, VoiceShifter 참고) — 청크 경계가 항상
# 분석 문맥의 한가운데 놓여 경계 아티팩트가 사라진다. 그만큼(120ms) 재생이 더 지연되고
# pyworld 처리량도 ~10% 늘어나는 트레이드오프.
VOICE_SHIFT_OVERLAP_MS = int(os.getenv("VOICE_SHIFT_OVERLAP_MS", "120"))

# turn_complete 이후에도 이 버퍼만큼 오디오가 아직 재생 중일 수 있어, 그 뒤에야 다음 턴을
# 안전하게 보내거나(launcher.py의 inject_turn) 정답 공개 화면을 띄울 수 있는(core/quiz_tools.py)
# 곳이 여러 군데라 여기서 한 번만 계산해 공유한다(2배 여유 — 오버랩 홀드백 120ms까지
# 포함하고도 남는 마진).
POST_SPEECH_DRAIN_SEC = (VOICE_SHIFT_BUFFER_MS * 2) / 1000


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

    청크 경계 처리(2026-08-07): 각 청크를 직전 청크의 입력 꼬리(`overlap_ms`)와
    이어붙여 분석하고, 그 꼬리에 해당하는 출력은 직전에 붙잡아둔 출력 꼬리와 선형
    크로스페이드로 섞어 내보낸다 — 청크를 독립 처리하던 시절의 경계 톤 불연속
    ("말...씀" 끊김)과, 턴 마지막 짧은 꼬리가 MIN_SHIFT_SAMPLES 미만이라 변조 없이
    원본 톤으로 재생되던 문제(원본/변환본 톤 차이)를 함께 해결한다. 항상 출력 꼬리
    `overlap_ms`만큼을 다음 청크와 섞기 위해 들고 있으므로 재생이 그만큼 더 지연된다.

    barge-in 처리(2026-08-07): reset()이 세대 번호를 올리고, feed()가 넣은 데이터엔
    그 시점 세대가 태깅된다. 워커는 (1) 큐에서 꺼낼 때 (2) 오래 걸리는 pyworld 처리를
    마치고 재생 직전, 두 번 세대를 검사한다 — 이전엔 reset 마커가 큐 뒤에 줄을 서는
    구조라, 이미 큐에 쌓였거나 처리 중이던 오디오가 stop_immediately() 이후에도 최대
    버퍼 길이만큼 뒤늦게 새어나올 수 있었다(2026-07-31 리뷰에서 발견, 당시 보류).
    """

    def __init__(self, on_shifted, sample_rate: int, buffer_ms: int = VOICE_SHIFT_BUFFER_MS,
                 overlap_ms: int = VOICE_SHIFT_OVERLAP_MS):
        self._on_shifted = on_shifted
        self._sr = sample_rate
        self._buffer_bytes = int(sample_rate * buffer_ms / 1000) * 2  # int16 = 2 bytes/sample
        self._overlap_bytes = int(sample_rate * overlap_ms / 1000) * 2
        self._buf = bytearray()
        self._in_q: "queue.Queue[tuple[str, bytes | None, int]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="voice-shift", daemon=True)
        # 오버랩-크로스페이드 상태(워커 스레드 전용): 직전 청크의 입력 꼬리와, 그 꼬리에
        # 해당하는 변환된 출력(아직 재생 안 됨 — 다음 청크의 머리와 섞어서 내보낸다).
        self._prev_in_tail = b""
        self._prev_out_tail = b""
        # reset() 세대 번호. feed/reset은 같은 스레드(recv_loop)에서만 불리고 워커는
        # 읽기만 하므로 락 없이 int 갱신으로 충분하다(GIL).
        self._gen = 0

    def feed(self, pcm_bytes: bytes):
        self._in_q.put(("data", pcm_bytes, self._gen))

    def flush(self):
        """턴이 끝났을 때 버퍼 임계값 미만으로 남은 꼬리 + 붙잡아둔 출력 꼬리를 흘려보낸다."""
        self._in_q.put(("flush", None, self._gen))

    def reset(self):
        """barge-in 등으로 재생을 즉시 끊을 때 — 큐에 쌓였거나 처리 중이던 미재생 오디오를
        전부 폐기한다(Speaker.stop_immediately()와 함께 호출할 것)."""
        self._gen += 1
        self._in_q.put(("reset", None, self._gen))

    def _run(self):
        while not self._stop.is_set():
            try:
                kind, payload, gen = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue

            if kind == "reset":
                self._buf.clear()
                self._prev_in_tail = b""
                self._prev_out_tail = b""
                continue
            if gen != self._gen:
                # reset() 이전에 feed()된 스테일 오디오 — 처리도 재생도 하지 않는다.
                continue

            if kind == "data":
                self._buf.extend(payload)
                while len(self._buf) >= self._buffer_bytes:
                    chunk = bytes(self._buf[:self._buffer_bytes])
                    del self._buf[:self._buffer_bytes]
                    self._emit_block(chunk, gen, is_flush=False)
            elif kind == "flush":
                chunk = bytes(self._buf)
                self._buf.clear()
                self._emit_block(chunk, gen, is_flush=True)

    def _shift_same_length(self, pcm_bytes: bytes):
        """shift_pcm을 돌리되 결과를 입력과 정확히 같은 샘플 수로 맞춰 돌려준다(int16 배열).
        pyworld 재합성은 프레임 경계 때문에 입력과 몇 ms 어긋날 수 있는데, 그대로 두면
        크로스페이드/꼬리 계산의 위치가 청크마다 조금씩 밀린다 — 모자라면 마지막 샘플을
        반복해 채운다(어차피 다음 청크와 크로스페이드되는 구간이라 티가 안 남)."""
        try:
            shifted = shift_pcm(pcm_bytes, self._sr)
        except Exception as e:
            print(f"⚠️ 음성 변조 실패, 원본 그대로 재생: {e}")
            shifted = pcm_bytes
        target = len(pcm_bytes) // 2
        arr = np.frombuffer(shifted, dtype=np.int16)
        if len(arr) > target:
            arr = arr[:target]
        elif len(arr) < target:
            arr = np.pad(arr, (0, target - len(arr)), mode="edge")
        return arr

    @staticmethod
    def _crossfade(prev_tail_bytes: bytes, cur_head: "np.ndarray"):
        prev = np.frombuffer(prev_tail_bytes, dtype=np.int16).astype(np.float32)
        cur = cur_head.astype(np.float32)
        fade = np.linspace(0.0, 1.0, len(prev), dtype=np.float32)
        # rint 없이 astype만 하면 버림(truncate)이라, 같은 값끼리 섞어도 부동소수점 오차로
        # ±1씩 어긋난다 — 반올림해야 "항등 변환이면 출력도 항등"이 정확히 성립한다.
        return np.rint(np.clip(prev * (1.0 - fade) + cur * fade, -32768, 32767)).astype(np.int16)

    def _emit_block(self, chunk: bytes, gen: int, is_flush: bool):
        overlap_samples = self._overlap_bytes // 2

        if not chunk:
            if is_flush and self._prev_out_tail:
                # 턴이 정확히 버퍼 경계에서 끝난 경우 — 붙잡아뒀던 출력 꼬리를 그대로 내보낸다.
                tail = self._prev_out_tail
                self._prev_in_tail = b""
                self._prev_out_tail = b""
                if gen == self._gen:
                    self._on_shifted(tail)
            return

        inp = self._prev_in_tail + chunk
        out = self._shift_same_length(inp)

        if self._prev_out_tail:
            head = self._crossfade(self._prev_out_tail, out[:overlap_samples])
            out = np.concatenate((head, out[overlap_samples:]))

        if is_flush:
            emit = out
            self._prev_in_tail = b""
            self._prev_out_tail = b""
        else:
            emit = out[:-overlap_samples]
            self._prev_out_tail = out[-overlap_samples:].tobytes()
            self._prev_in_tail = inp[-self._overlap_bytes:]

        if gen != self._gen:
            # pyworld 처리 도중 reset()이 들어왔다 — 이미 stop_immediately()로 끊긴
            # 발화의 꼬리를 뒤늦게 재생하지 않는다.
            self._prev_in_tail = b""
            self._prev_out_tail = b""
            return
        self._on_shifted(emit.tobytes())

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
