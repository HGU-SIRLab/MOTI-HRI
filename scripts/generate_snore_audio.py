"""SLEEPY 상태에서 launcher.py가 반복 재생할 코골이 소리("드르렁... 쿠우...")를
딱 한 번 생성해 assets/audio/snore.wav로 캐싱하는 오프라인 스크립트.

launcher.py는 이 파일을 읽기만 하고 런타임에 API를 다시 호출하지 않는다 — idle-sleep
배경음이라 매번 실시간으로 생성하면 낭비고, "정확히 1초 간격으로 반복" 같은 정밀한
타이밍도 캐시된 클립을 로컬에서 반복 재생하는 쪽이 훨씬 안정적이다(Live API로 매번
새로 말하게 하면 반복 간격이 API 응답 지연에 따라 들쭉날쭉해짐).

목소리는 launcher.py와 같은 LIVE_MODEL/LIVE_VOICE_NAME + media/voice_shift.py의 같은
피치/포먼트 설정을 그대로 적용해 평소 모티 목소리와 톤이 어긋나지 않게 한다.

사용:
    python scripts/generate_snore_audio.py
    (GOOGLE_API_KEY 필요, 로봇/디스플레이 불필요)
"""
import asyncio
import os
import sys
import wave

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from media.audio_manager import OUTPUT_RATE
from media.voice_shift import VOICE_FORMANT_RATIO, VOICE_PITCH_SEMITONES, shift_pcm

LIVE_MODEL = os.getenv("LIVE_MODEL_NAME", "models/gemini-3.1-flash-live-preview")
LIVE_VOICE_NAME = os.getenv("LIVE_VOICE_NAME", "Zephyr")
OUT_PATH = os.path.join(_REPO_ROOT, "assets", "audio", "snore.wav")
SNORE_LINE = "드르렁... 쿠우..."


async def main():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=os.path.join(_REPO_ROOT, ".env"))
            api_key = os.getenv("GOOGLE_API_KEY")
        except ImportError:
            pass
    if not api_key:
        print("⏭️  GOOGLE_API_KEY가 없어 생성할 수 없습니다.")
        return

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE_NAME)
            )
        ),
    )

    raw_chunks = []
    print(f"🎙️  {LIVE_VOICE_NAME} 목소리로 코골이 소리를 생성합니다...")
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=(
                    "이제 정확히 이 소리만 그대로 내세요(단어를 덧붙이거나 완전한 문장으로 "
                    f"만들지 마세요 — 코 고는 흉내입니다): \"{SNORE_LINE}\""
                ))],
            ),
            turn_complete=True,
        )
        async for message in session.receive():
            if message.data:
                raw_chunks.append(message.data)
            if message.server_content and message.server_content.turn_complete:
                break

    raw_pcm = b"".join(raw_chunks)
    if not raw_pcm:
        print("❌ 오디오를 받지 못했습니다.")
        return

    shifted = shift_pcm(raw_pcm, OUTPUT_RATE, VOICE_PITCH_SEMITONES, VOICE_FORMANT_RATIO)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with wave.open(OUT_PATH, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(OUTPUT_RATE)
        wf.writeframes(shifted)

    duration = len(shifted) / 2 / OUTPUT_RATE
    print(f"✅ 저장 완료: {OUT_PATH} ({duration:.2f}초) — 재생해서 톤/발음을 확인해보세요.")
    print("   마음에 안 들면 이 스크립트를 다시 실행하면 덮어씁니다(생성 결과가 매번 조금씩 다를 수 있음).")


if __name__ == "__main__":
    asyncio.run(main())
