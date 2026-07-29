"""launcher.py의 _load_snore_clip()이 SLEEPY 배경음 캐시 파일을 안전하게 읽는지 확인.
로봇/API 불필요 — 임시 WAV 파일로만 검증한다(launcher.py를 임포트하면 포트 스캔 로그가
찍히지만 실제로 포트를 열지는 않는다).

사용:
    python scripts/test_snore_clip.py
"""
import os
import sys
import tempfile
import wave

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

import launcher
from media.audio_manager import OUTPUT_RATE


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


def _write_wav(path, framerate, nchannels, sampwidth, n_frames):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00" * (n_frames * nchannels * sampwidth))


def main():
    ok = True
    tmpdir = tempfile.mkdtemp()
    original_path = launcher.SNORE_CLIP_PATH

    try:
        launcher.SNORE_CLIP_PATH = os.path.join(tmpdir, "missing.wav")
        pcm, dur = launcher._load_snore_clip()
        ok &= check("missing file returns (None, None)", pcm is None and dur is None)

        wrong_rate_path = os.path.join(tmpdir, "wrong_rate.wav")
        _write_wav(wrong_rate_path, framerate=16000, nchannels=1, sampwidth=2, n_frames=1000)
        launcher.SNORE_CLIP_PATH = wrong_rate_path
        pcm, dur = launcher._load_snore_clip()
        ok &= check("wrong sample rate is rejected", pcm is None and dur is None)

        stereo_path = os.path.join(tmpdir, "stereo.wav")
        _write_wav(stereo_path, framerate=OUTPUT_RATE, nchannels=2, sampwidth=2, n_frames=1000)
        launcher.SNORE_CLIP_PATH = stereo_path
        pcm, dur = launcher._load_snore_clip()
        ok &= check("stereo (non-mono) is rejected", pcm is None and dur is None)

        valid_path = os.path.join(tmpdir, "valid.wav")
        n_frames = OUTPUT_RATE * 2  # 정확히 2초
        _write_wav(valid_path, framerate=OUTPUT_RATE, nchannels=1, sampwidth=2, n_frames=n_frames)
        launcher.SNORE_CLIP_PATH = valid_path
        pcm, dur = launcher._load_snore_clip()
        ok &= check("valid mono 24kHz int16 file loads", pcm is not None and len(pcm) == n_frames * 2)
        ok &= check("duration matches frame count", abs(dur - 2.0) < 1e-6)
    finally:
        launcher.SNORE_CLIP_PATH = original_path

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
