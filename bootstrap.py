"""레이어에 속하지 않는 최상위 유틸리티. hardware/든 core/든 어디서나 가져다 써도
계층 구조(§02, hardware↔core 상호 미참조)를 어기지 않는다.
"""
import sys


def ensure_utf8_console():
    """Windows 기본 콘솔(cp949)은 이모지를 인코딩하지 못해 print()가 그대로
    크래시한다. 프로세스 시작 시 한 번만 호출하면 된다."""
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "encoding", "").lower() != "utf-8":
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
