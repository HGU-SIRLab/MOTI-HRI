"""세션 산출물(대화록 + 퀴즈 연구 데이터)이 저장될 폴더를 한 곳에서 정한다.

**왜 별도 모듈인가**: 대화록은 core/report_manager.py가, 퀴즈 JSON은 core/quiz_export.py가
저장하는데, 예전에는 둘이 각자 `datetime.now()`를 찍어 각자 파일명을 지었다. 그래서 같은
세션인데도 몇 초 어긋난 이름으로 흩어졌고(실제로 `..._130537_조형민_대화.txt`와
`..._130541_조형민_quiz/`처럼 4초 차이가 났다), N명 분량이 쌓이면 어떤 대화록이 어떤 퀴즈
결과와 같은 세션인지 눈으로 맞춰야 했다. 이제 launcher.py가 세션 시작 시점에 폴더 하나를
정해서 두 저장 함수에 똑같이 넘겨준다.

구조:
    user_result/{참가자ID}/{날짜_시각}/
        대화.txt
        1_척척박사.json  2_하찮미.json  3_짜증유발.json  _전체.json
        session_meta.json

참가자ID는 .env의 PARTICIPANT_ID로 진행자가 지정한다(QUIZ_MODE_ORDER와 같은 운영 방식).
로봇이 대화 중에 알아낸 이름 대신 이 값을 폴더 키로 쓰는 이유:
  - 참가자가 끝내 이름을 말하지 않아도 산출물이 항상 올바른 폴더에 남는다(예전에는
    이름을 모르면 대화록이 **아예 저장되지 않았다** — report_manager.py 참고).
  - 논문 데이터가 실명 없이 익명화된다.
  - 동명이인이 섞이지 않는다.
"""
import json
import os
from datetime import datetime

# 폴더명으로 쓸 수 없거나(윈도우 예약문자) 상위 폴더로 새어나갈 수 있는 문자.
_FORBIDDEN_CHARS = set('\\/:*?"<>|')

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_ROOT = os.path.join(_BASE_DIR, "user_result")


def parse_participant_id(raw: str | None) -> str:
    """.env의 PARTICIPANT_ID를 검증해 폴더명으로 쓸 문자열을 돌려준다.

    **비어 있거나 잘못된 값이면 조용히 기본값으로 넘어가지 않고 ValueError를 던진다** —
    QUIZ_MODE_ORDER와 같은 이유다(launcher.py 참고). 참가자 표식이 없거나 잘못된 채로
    실험이 진행되면 산출물이 엉뚱한 폴더에 섞이는데, 세션이 끝난 뒤에는 어느 참가자
    것이었는지 되찾을 방법이 없다.

    실험이 아닌 평소 대화로 로봇을 켤 때는 PARTICIPANT_ID=test 처럼 아무 값이나 넣으면 된다.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError(
            "PARTICIPANT_ID가 비어 있습니다 — .env에 이번 참가자 번호를 넣어주세요"
            "(예: PARTICIPANT_ID=P07). 실험이 아니면 PARTICIPANT_ID=test 로 두면 됩니다."
        )
    bad = sorted(_FORBIDDEN_CHARS & set(value))
    if bad:
        raise ValueError(
            f"PARTICIPANT_ID에 폴더명으로 쓸 수 없는 문자가 있습니다: {''.join(bad)} "
            f"(지금: {value!r}) — 영문/숫자/한글과 _ - 정도만 써주세요(예: P07)."
        )
    if set(value) <= {"."}:
        raise ValueError(f"PARTICIPANT_ID가 폴더명으로 쓸 수 없는 값입니다: {value!r} (예: P07)")
    return value


def make_session_dir(participant_id: str, started_at: datetime | None = None) -> str:
    """이번 세션의 산출물 폴더 경로를 만든다(폴더를 실제로 만들지는 않는다).

    폴더명에 날짜뿐 아니라 시각(HHMMSS)까지 넣는 이유: 같은 참가자를 같은 날 다시
    진행해야 하는 경우(기술적 문제로 재실험 등) 이전 시도를 덮어쓰지 않기 위함이다.

    실제 생성은 저장 함수들이 쓰기 직전에 한다 — 아무것도 안 남긴 세션(로봇만 켜뒀다
    끈 경우)이 빈 폴더로 쌓이지 않게.
    """
    now = started_at or datetime.now()
    return os.path.join(RESULT_ROOT, participant_id, now.strftime("%Y-%m-%d_%H%M%S"))


def write_session_meta(session_dir: str, **fields) -> str | None:
    """참가자ID·이름·배정된 모드 순서 등 세션 단위 정보를 session_meta.json으로 남긴다.

    특히 **모드 순서(카운터밸런싱 그룹)** 는 지금까지 .env에만 있고 산출물에는 어디에도
    안 남아서, 분석할 때 "이 참가자가 몇 번 순서 그룹이었나"를 손으로 맞춰야 했다
    (docs/experiment_design.md의 순서효과 부가 분석에 필요한 값이다).

    이 세션이 아무것도 안 남겼으면(대화록도 퀴즈 결과도 없음) 폴더를 만들지 않고
    None을 돌려준다 — 빈 폴더에 meta만 덩그러니 남기지 않기 위함.
    """
    if not os.path.isdir(session_dir):
        return None
    path = os.path.join(session_dir, "session_meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fields, f, ensure_ascii=False, indent=2)
    return path
