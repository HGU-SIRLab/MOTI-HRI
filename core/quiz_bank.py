"""퀴즈 문제 은행 + 정답 판정 — 순수 로직, Gemini/asyncio 의존 없음(core/quiz_state.py가 사용).

docs 참고: `소셜 로봇 의도적 비완전성 실험 설계.docx`의 "부분 확대 사진 퀴즈" 실험용.
모든 참가자에게 같은 문제/같은 순서를 보여줘야 하므로(조건 간 비교 오염 방지),
`load_question_bank()`는 JSON 파일에 적힌 순서를 그대로 유지한다 — 셔플하지 않음.
"""
import difflib
import json
import os
from dataclasses import dataclass, field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS_DIR = os.path.join(_REPO_ROOT, "assets", "quiz")
_DEFAULT_BANK_PATH = os.path.join(_ASSETS_DIR, "questions.json")

# STT로 들어온 발화에서 흔히 붙는 조사/어미를 떼어낸다 — 정답이 "먼지떨이"인데 사용자가
# "먼지떨이예요"/"먼지떨이인가요?"라고 말해도 같은 것으로 인식하기 위함. 긴 것부터 매칭해야
# "이에요"를 "예요"보다 먼저 시도하다 어중간하게 잘리는 일이 없다(길이 내림차순 필수).
_TRAILING_SUFFIXES = sorted([
    "이에요", "예요", "입니다", "인가요", "인 것 같아요", "인것같아요", "같아요", "이야", "야",
    "이다", "은", "는", "이", "가", "을", "를", "요", "?", "!", ".",
], key=len, reverse=True)

# "모르겠다"류뿐 아니라 "정답 알려줘"/"네가 풀어줘"처럼 로봇에게 대신 풀어달라고 넘기는
# 말도 여기 포함한다 — 하찮미(imperfect) 모드에서 이 마커에 걸려야 "저도 한번 맞춰볼게요!"
# 이벤트가 뜬다(2026-07-30, 실제 답 시도와 구분하기 위해 도입).
_DONT_KNOW_MARKERS = (
    "모르겠", "몰라", "모름", "패스", "글쎄", "잘 모르", "포기",
    "알려줘", "알려주세요", "알려줄래", "가르쳐줘", "가르쳐주세요",
    "네가 풀어", "니가 풀어", "너가 풀어", "네가 맞혀", "니가 맞혀", "너가 맞혀",
    "네가 대신", "니가 대신", "너가 대신", "정답이 뭐", "정답 뭐",
)

FUZZY_MATCH_THRESHOLD = 0.72


@dataclass
class QuizQuestion:
    id: str
    image_path: str
    answer: str
    alternates: list[str] = field(default_factory=list)
    hint: str | None = None
    # 정답 공개 때 보여줄 크롭 전 원본 사진 — 없으면(구형 문제 데이터 등) 정답 공개 때도
    # 확대 사진(image_path)을 그대로 보여준다(core/quiz_tools.py에서 fallback 처리).
    full_image_path: str | None = None

    @property
    def accepted_answers(self) -> list[str]:
        return [self.answer] + list(self.alternates)


def load_question_bank(path: str | None = None) -> list[QuizQuestion]:
    """JSON 파일에 적힌 순서 그대로 로드한다(셔플 금지 — 모든 참가자가 같은 순서로 봐야 함)."""
    path = path or _DEFAULT_BANK_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    questions = []
    seen_ids = set()
    for item in raw:
        qid = item["id"]
        if qid in seen_ids:
            raise ValueError(f"중복된 문제 id: {qid}")
        seen_ids.add(qid)
        if not item.get("answer", "").strip():
            raise ValueError(f"문제 {qid}에 정답이 비어있음")
        # image_path가 상대경로면(scripts/build_quiz_bank.py가 저장하는 형식) 이 파일
        # 기준 저장소 루트로 풀어준다 — cwd가 항상 저장소 루트라는 보장이 없다(예:
        # display/quiz_window.py를 별도 프로세스로 다른 위치에서 띄우는 경우 등).
        image_path = item["image_path"]
        if not os.path.isabs(image_path):
            image_path = os.path.join(_REPO_ROOT, image_path)
        full_image_path = item.get("full_image_path")
        if full_image_path and not os.path.isabs(full_image_path):
            full_image_path = os.path.join(_REPO_ROOT, full_image_path)
        questions.append(QuizQuestion(
            id=qid,
            image_path=image_path,
            answer=item["answer"].strip(),
            alternates=[a.strip() for a in item.get("alternates", [])],
            hint=item.get("hint"),
            full_image_path=full_image_path,
        ))
    return questions


def normalize_guess(text: str) -> str:
    """공백 제거 + 흔한 조사/어미 제거(가장 긴 접미사부터 시도)."""
    t = text.strip().replace(" ", "")
    changed = True
    while changed:
        changed = False
        for suffix in _TRAILING_SUFFIXES:
            if len(t) > len(suffix) and t.endswith(suffix):
                t = t[: -len(suffix)]
                changed = True
                break
    return t


def judge_guess(guess_text: str, question: QuizQuestion) -> tuple[bool, bool]:
    """(is_correct, is_dont_know)를 반환한다.

    "모르겠어요" 류는 오답과 별개로 취급해야 한다 — 척척박사 모드는 "오답"과 "모름"에
    대해 말투를 다르게 할 수 있고, 하찮미 모드의 흐름도 사용자가 명시적으로 "모르겠다"고
    했는지를 구분해야 자연스럽다(설계 문서 4.2절 "오답을 말하거나 모른다고 대답했을 때").
    """
    raw = guess_text.strip()
    if any(marker in raw for marker in _DONT_KNOW_MARKERS):
        return False, True

    normalized_guess = normalize_guess(raw)
    if not normalized_guess:
        return False, True

    for accepted in question.accepted_answers:
        normalized_accepted = normalize_guess(accepted)
        if normalized_guess == normalized_accepted:
            return True, False
        # STT 오타/부분 인식 흡수 — 짧은 단어일수록 문턱값이 너무 관대해지지 않도록
        # SequenceMatcher 비율 + 포함관계 둘 다 확인.
        ratio = difflib.SequenceMatcher(None, normalized_guess, normalized_accepted).ratio()
        if ratio >= FUZZY_MATCH_THRESHOLD:
            return True, False
        if len(normalized_accepted) >= 2 and normalized_accepted in normalized_guess:
            return True, False

    return False, False
