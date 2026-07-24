"""레이어에 속하지 않는 최상위 유틸리티. hardware/든 core/든 어디서나 가져다 써도
계층 구조(§02, hardware↔core 상호 미참조)를 어기지 않는다.
"""
import os
import sys

from dotenv import load_dotenv

# hardware/config.py, core/report_manager.py 등은 모듈 임포트 시점에 os.getenv(...)로
# 설정값을 읽는다. 각 스크립트가 알아서 load_dotenv()를 호출하되 그 시점이 이미 저
# import 뒤라면(실제로 그래왔다), .env의 값이 조용히 무시된다 — 이 파일은 항상
# 다른 무엇보다도 먼저 import되므로, 여기서 한 번만 로드하면 그 문제가 통째로 사라진다.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def ensure_utf8_console():
    """Windows 기본 콘솔(cp949)은 이모지를 인코딩하지 못해 print()가 그대로
    크래시한다. 프로세스 시작 시 한 번만 호출하면 된다."""
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "encoding", "").lower() != "utf-8":
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
