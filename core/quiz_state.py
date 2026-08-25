"""퀴즈 세션 상태 기계 — 모드별 분기는 전부 여기 있고, core/quiz_tools.py는 이 클래스의
반환 지시문을 그대로 Gemini 툴 응답으로 넘기는 얇은 래퍼다(core/profile_manager.py와
core/memory_tools.py의 관계와 동일한 분리).

핵심 설계(docs 계획 참고): Live API 세션은 system_instruction/tools를 연결 시점에 한 번만
고정하므로, 모드별로 다른 툴/프롬프트를 쓸 수 없다 — 대신 고정된 툴 하나(submit_guess 등)의
반환값을 이 클래스가 `self.mode`에 따라 런타임에 다르게 만든다. remember_fact가
name_state["name"]에 따라 분기하는 것과 같은 패턴을 그대로 확장한 것.

이 모듈은 순수 로직만 다룬다 — asyncio/하드웨어/UI 큐를 몰라야 core/quiz_tools.py 없이도
독립적으로 유닛 테스트할 수 있다.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.quiz_bank import QuizQuestion, judge_guess

VALID_MODES = ("all_knowing", "imperfect", "annoying")

# 실험 진행자가 참가자에게 배정하는 순서를 말할 때 쓰는 번호/이름(1번 척척박사 …).
# 참가자에게 읽어주는 안내문과 .env의 QUIZ_MODE_ORDER 파싱이 이 표 하나를 공유한다.
MODE_NUMBER = {"all_knowing": 1, "imperfect": 2, "annoying": 3}
MODE_NAME = {"all_knowing": "척척박사", "imperfect": "하찮미", "annoying": "짜증유발"}
_NUMBER_TO_MODE = {str(num): mode for mode, num in MODE_NUMBER.items()}
_NAME_TO_MODE = {name: mode for mode, name in MODE_NAME.items()}

# 진행자가 아무것도 지정하지 않았을 때의 순서 — 완전 카운터밸런싱(6순열)을 쓰는 실험에서는
# 참가자마다 .env의 QUIZ_MODE_ORDER로 반드시 지정해야 한다(docs/experiment_design.md).
DEFAULT_MODE_ORDER = ("all_knowing", "imperfect", "annoying")


def mode_label(mode: str) -> str:
    """'2번 하찮미'처럼 참가자에게 읽어줄 수 있는 라벨."""
    return f"{MODE_NUMBER[mode]}번 {MODE_NAME[mode]}"


def parse_mode_order(raw: str | None) -> list[str]:
    """.env의 QUIZ_MODE_ORDER를 모드 리스트로 파싱한다.

    허용 형식: "2,3,1" / "2 3 1" / "imperfect,annoying,all_knowing" / "하찮미,짜증유발,척척박사"
    (섞어 써도 된다). 비어있으면 DEFAULT_MODE_ORDER.

    **잘못된 값이면 조용히 기본값으로 넘어가지 않고 ValueError를 던진다** — 진행자가
    오타를 낸 채로 실험이 진행되면 카운터밸런싱이 깨진 데이터가 나오는데, 그건 나중에
    복구할 수 없다. 시작 시점에 죽는 편이 훨씬 낫다(launcher.py가 초기화 전에 미리
    한 번 호출해 확인한다).
    """
    if raw is None or not raw.strip():
        return list(DEFAULT_MODE_ORDER)

    tokens = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    order = []
    for token in tokens:
        mode = _NUMBER_TO_MODE.get(token) or _NAME_TO_MODE.get(token)
        if mode is None and token in VALID_MODES:
            mode = token
        if mode is None:
            raise ValueError(
                f"QUIZ_MODE_ORDER에 알 수 없는 값 '{token}'이 있습니다 — "
                "1/2/3 또는 척척박사/하찮미/짜증유발로 적어주세요(예: QUIZ_MODE_ORDER=2,3,1)."
            )
        order.append(mode)

    if sorted(order) != sorted(VALID_MODES):
        raise ValueError(
            f"QUIZ_MODE_ORDER는 세 모드를 정확히 한 번씩 나열해야 합니다(지금: {raw!r}) — "
            "예: QUIZ_MODE_ORDER=2,3,1"
        )
    return order

# 모드 3(짜증유발)의 거절 대사 — 정확히 이 문자열이어야 함(연구 일관성 요구사항).
# core/quiz_tools.py의 지연 주입과 core/utils.py의 페르소나 지시문이 이 상수 하나만 참조한다.
MODE3_REFUSAL_LINE = "저는 AI 로봇이라 그런 답변은 할 수 없습니다."

# 판정 직후(정답 공개 이미지가 뜨기 전) 세 모드 공통으로 돌려주는 "침묵" 지시문 — 2026-08-08,
# 실물 테스트로 발견한 구조적 결함 대응. 예전엔 이 시점에 곧장 정답/반응 텍스트를 돌려줘서
# 모델이 그걸 바로 말했는데, Live API는 여러 개의 함수 호출을 실제 오디오 한 마디 없이
# 연달아 처리한 뒤에야 한꺼번에 말할 수 있어(2026-08-07 하찮미 실물 로그로 확인 — 사용자
# 답변 채점 툴과 로봇 자신의 추측 채점 툴을 오디오 없이 연달아 부른 뒤에야 "제 생각엔
# ~ 같아요! 비교해볼까요? 어 저도 틀렸네요! 다음 문제로 가볼까요?"를 전부 한 턴에 몰아
# 말해버렸다) — 그래서 "이 턴이 끝나면 정답 이미지를 띄운다"는 core/quiz_tools.py의
# 동기화 로직이 무의미해졌다(정답 반응 + 다음 문제 안내까지 이미 다 말해버린 뒤에야
# 이미지가 뜸). 이제는 판정 시점엔 이 침묵 지시문만 돌려주고, core/quiz_tools.py가
# (1) 이 턴이 끝나길 기다렸다가 정답 이미지를 먼저 띄우고 (2) 그 다음에야 pending_reveal_speech
# 를 별도의 히든 턴으로 주입해 로봇이 실제로 반응을 말하게 한다 — 이미지와 발화 순서를
# Python이 직접 통제해서, 모델이 몇 개의 툴을 어떻게 몰아 부르든 순서가 항상 보장된다.
#
# 2026-08-10(41단계) 짧게 재작성: 실물 로그에서 모델이 이 문장을 **그대로 낭독**했다
# ("판정했습니다. 이 턴에서는 그 어떤 말도 하지 마세요. 화면이 바뀌는 대로..."). 음성은
# launcher.py의 mute_speech 게이트가 막아주지만, 생성 시간 자체는 출력 길이에 비례하므로
# 60자짜리 안내문을 읽는 동안 4~5초가 통째로 죽은 시간이 됐다(정답 공개는 이 턴이 끝나야
# 시작된다). 실제로 참가자가 반응이 없다고 답을 다시 말했고, 그 반복 발화가 다음 문항의
# 답으로 채점돼 문항 하나를 잃는 사고까지 이어졌다. 그래서 (a) 사람이 읽을 산문이 아니라
# 기계 상태 토큰처럼 보이게 하고 (b) 낭독되더라도 1초 안에 끝나도록 짧게 줄였다.
_HOLD_FOR_REVEAL = "[OK] 침묵. 대기."


@dataclass
class _QuestionResult:
    question_id: str
    mode: str
    user_guess_text: str | None = None
    user_correct: bool | None = None
    user_dont_know: bool | None = None
    robot_guess_text: str | None = None
    robot_correct: bool | None = None
    hint_requested: bool = False
    # 문제가 화면에 실제로 표시된 순간부터 채점/포기로 확정될 때까지 걸린 시간(초).
    # docs/experiment_design.md §5의 "문제당 소요시간" 지표 — mark_question_shown()이
    # 호출된 적 없으면(순수 로직 테스트 등) None으로 남는다.
    elapsed_sec: float | None = None
    # 짜증유발 모드 전용 — 이 문제에서 포기하기까지 실제로 발화된 거절 대사 횟수
    # (docs/experiment_design.md §5의 "포기까지 겪은 거절 횟수" 지표, A-6 답답함
    # 자기보고를 보완하는 객관적 노출량 프록시). 다른 모드에서는 None.
    annoying_refusals: int | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class QuizSession:
    def __init__(self, questions: list[QuizQuestion], num_questions: int = 5,
                 initial_round_offset: int = 0, mode_order: list[str] | None = None):
        # 모든 참가자에게 같은 문제 은행/같은 순서 — 셔플하지 않는다. 실제 라운드별
        # 슬라이스는 begin_next_round()가 _pick_round_questions()로 매번 새로 정한다.
        #
        # mode_order: 실험 진행자가 이 참가자에게 배정한 모드 순서(예: ["imperfect",
        # "annoying", "all_knowing"] = 2-3-1). 2026-08-10 교수님 지시로 참가자가 말로
        # 모드를 고르는 방식을 폐기하고, 진행자가 .env(QUIZ_MODE_ORDER)로 미리 정한
        # 순서대로 5문제씩 3라운드(총 15문제)가 한 번에 이어서 진행된다.
        #
        # initial_round_offset: 세션이 도중에 크래시해 launcher를 재시작해야 할 때,
        # 참가자가 이미 마친 라운드 수를 지정해 (a) 이미 정답이 공개된 사진 슬라이스와
        # (b) 이미 진행한 모드를 둘 다 건너뛴다(core/quiz_tools.py가 QUIZ_ROUND_OFFSET
        # env로 전달) — 안 그러면 재시작한 세션이 1라운드부터 다시 시작해 같은 사진과
        # 같은 모드를 반복하는 오염이 생긴다.
        self.all_questions = questions
        self.num_questions = num_questions
        self.mode_order: list[str] = list(mode_order or DEFAULT_MODE_ORDER)
        self.questions: list[QuizQuestion] = []
        # 다음에 시작할 라운드 번호(0-based) — begin_next_round()가 하나 진행할 때마다 +1.
        self.round_index: int = initial_round_offset
        self.mode: str | None = None
        self.index: int = -1
        self.active: bool = False
        # 이번 라운드의 문제를 다 소진했지만 아직 다음 라운드가 시작되지 않은 상태.
        # core/quiz_tools.py가 정답 공개를 마친 뒤 이 플래그를 보고 모드 전환을 진행한다.
        self.round_finished: bool = False
        self.pending_user_guess: str | None = None
        # 짜증유발 모드 전용 — 실제 답 시도(맞았든 틀렸든)의 정오를 기억해뒀다가, 나중에
        # 참가자가 포기/스킵을 요청하면 그 시점의 로그에 반영한다(2026-07-31, 응답 자체는
        # 항상 거절이라 정오가 그 자리에서 드러나지는 않지만 조작 점검/정답률 지표로는
        # 남겨둘 가치가 있다).
        self.annoying_pending_correct: bool = False
        # 판정 직후 정답 이미지가 뜬 뒤에야 히든 턴으로 주입할 반응 지시문(위 _HOLD_FOR_REVEAL
        # 참고). core/quiz_tools.py가 매 판정 직후 이 값을 읽고 자기 책임 하에 지워야 한다 —
        # 다음 판정 때 새로 안 채워지면 이전 문제의 반응이 재사용되는 사고를 막기 위해
        # quiz_tools.py 쪽에서 즉시 None으로 되돌린다(pending_user_guess와 같은 패턴).
        self.pending_reveal_speech: str | None = None
        self.results: list[_QuestionResult] = []
        self._hint_requested_this_question: bool = False
        # "문제당 소요시간" 지표용 — 화면에 문제가 실제로 표시된 순간(mark_question_shown)
        # 의 monotonic 시각. 채점/포기로 확정될 때 elapsed_sec으로 기록된다.
        self._question_shown_at: float | None = None
        # 짜증유발 모드에서 이 문제에 대해 실제로 발화된 거절 대사 횟수 — 지연 주입이
        # 진짜로 전송된 순간에만 core/quiz_tools.py가 note_refusal_delivered()로 올린다.
        self.annoying_refusals_this_question: int = 0

    @property
    def current_question(self) -> QuizQuestion | None:
        if 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def total_rounds(self) -> int:
        return len(self.mode_order)

    @property
    def grand_total_questions(self) -> int:
        """세 라운드를 합친 전체 문항 수(5문제 × 3모드 = 15) — 참가자 안내문에 쓴다."""
        return self.num_questions * self.total_rounds

    def _pick_round_questions(self) -> list[QuizQuestion]:
        """세션 하나 안에서 참가자가 3개 모드를 연속으로 진행할 때(실험 운영 방식,
        2026-07-31 확정), 매 라운드마다 앞선 라운드와 겹치지 않는 문제 세트를 돌려준다.
        같은 사진을 다음 라운드에 또 보여주면, 척척박사 라운드에서 이미 정답 공개(reveal)를
        본 참가자가 다음 모드에서 정답을 이미 아는 채로 반응하게 되어 하찮미/짜증유발
        효과 자체를 가리는 심각한 교란요인이 된다(코드 리뷰로 발견, 이전엔 한 참가자가
        한 라운드만 한다고 가정하고 짠 코드였음).

        문제 은행이 num_questions * (몇 번째 라운드인지)만큼 충분하면 완전히 겹치지 않는
        세트가 나온다. 은행이 부족하면(예: 준비된 사진이 모자라 3라운드 분량이 안 되는
        경우) 어쩔 수 없이 앞 라운드와 겹치는 세트로 되돌아가되, 실험자가 즉시 알아챌 수
        있게 콘솔에 경고를 남긴다 — 조용히 겹쳐서 데이터가 오염되는 것보다는 낫다.
        """
        total = len(self.all_questions)
        n = self.num_questions
        start_idx = self.round_index * n
        if total and start_idx >= total:
            print(
                f"⚠️ 문제 은행({total}개)이 부족해 이번 라운드가 이전 라운드와 겹치는 문제를 "
                f"재사용합니다 — assets/quiz/questions.json에 문제를 추가하세요."
            )
            start_idx %= total
        picked = self.all_questions[start_idx:start_idx + n]
        if len(picked) < n:
            # 은행이 라운드 경계에 딱 안 떨어지면 마지막 라운드만 조용히 짧아진다
            # (예: 12문항 은행 + 라운드당 5문항 -> 3라운드가 2문항). 조건 간 문항 수가
            # 달라지면 비교 자체가 흔들리므로 반드시 눈에 띄게 알린다 — 15장 기준으로는
            # 정확히 3라운드로 떨어지지만, 사진을 더하거나 빼면 곧바로 발생할 수 있다.
            print(
                f"⚠️ 이번 라운드에 문제가 {len(picked)}개뿐입니다(라운드당 {n}개여야 함) — "
                f"문제 은행이 {total}개라 라운드 경계에 맞지 않습니다. 조건 간 문항 수가 "
                f"달라지므로 assets/quiz/questions.json을 {n * len(self.mode_order)}개로 맞추세요."
            )
        return picked

    def start(self) -> str:
        """퀴즈 전체를 시작한다 — 참가자에게 읽어줄 전체 안내문만 지시하고, 첫 라운드는
        아직 시작하지 않는다.

        2026-08-10(교수님 지시)로 운영 방식이 바뀌었다: 예전에는 참가자가 실험자에게
        들은 모드 번호를 말하면 그 라운드만 진행했지만(select_quiz_mode), 이제는
        진행자가 .env의 QUIZ_MODE_ORDER로 미리 정한 순서대로 5문제씩 3라운드가 한 번에
        이어서 진행된다. 참가자는 모드를 고르지 않는다.

        첫 라운드는 core/quiz_tools.py가 이 안내 발화가 실제로 끝나는 것을 확인한 뒤
        begin_next_round()로 시작한다 — 안내를 말하는 도중에 사진이 먼저 떠버리면
        참가자가 안내를 안 듣고 화면부터 보게 되기 때문(이미지-발화 순서를 Python이
        직접 통제하는 39단계 구조와 같은 이유).
        """
        self.active = True
        self.mode = None
        self.index = -1
        self.questions = []
        self.round_finished = False
        self.pending_user_guess = None
        self.pending_reveal_speech = None

        total = self.grand_total_questions
        n = self.num_questions
        return (
            "지금부터 '부분 확대 사진 퀴즈'를 시작합니다. 사용자에게 아래 내용을 당신의 "
            "말투로 자연스럽게, 빠짐없이 안내하세요:\n"
            "(1) 화면에 사물의 일부를 확대한 사진이 나오면 그게 무엇인지 맞히는 게임입니다.\n"
            f"(2) 퀴즈는 총 {total}문제이고, {n}문제씩 서로 다른 모드의 저와 함께 풀게 됩니다.\n"
            "(3) 척척박사, 하찮미, 짜증유발 — 이렇게 3가지 모드의 저와 문제를 풀게 됩니다.\n"
            "(4) 1번 척척박사 모드에서는 제가 정답을 알고 있습니다.\n"
            "(5) 2번 하찮미 모드와 3번 짜증유발 모드에서는 저도 정답을 모르는 채로 "
            "사용자님과 함께 문제를 풀게 됩니다.\n"
            "이 안내만 하고 곧바로 멈추세요 — 어떤 모드로 할지 묻지 말고(순서는 이미 "
            "정해져 있습니다), 첫 문제를 소개하거나 사진이 나왔다고 말하지도 마세요. "
            "안내를 마치면 잠시 후 제가 다음 지시를 드립니다."
        )

    def current_round_number(self) -> int:
        """지금 진행 중인 라운드가 몇 번째인지(1-based). 아직 시작 전이면 0."""
        return self.round_index

    def begin_next_round(self) -> str | None:
        """다음 라운드를 시작하고 그 모드 안내 지시문을 돌려준다. 남은 라운드가 없으면
        세션을 끝내고 None을 반환한다(호출부가 마무리 처리).

        core/quiz_tools.py만 호출한다 — (a) start_quiz() 직후 전체 안내 발화가 끝난 뒤
        첫 라운드로, (b) 한 라운드의 마지막 문제 정답 공개가 끝난 뒤 다음 모드로.
        """
        if self.round_index >= len(self.mode_order):
            self.active = False
            self.round_finished = False
            return None

        mode = self.mode_order[self.round_index]
        self.questions = self._pick_round_questions()
        self.round_index += 1

        self.mode = mode
        self.index = 0
        self.active = True
        self.round_finished = False
        self.pending_user_guess = None
        self.annoying_pending_correct = False
        self._hint_requested_this_question = False
        self._question_shown_at = None
        self.annoying_refusals_this_question = 0
        self.pending_reveal_speech = None

        label = mode_label(mode)
        if self.round_index == 1:
            opening = (
                f"첫 번째 모드는 {label} 모드입니다. 사용자에게 \"그럼 먼저 {label} "
                "모드로 시작해볼게요!\"처럼 지금부터 어떤 모드인지 분명히 알려주세요."
            )
        else:
            opening = (
                f"방금 {self.num_questions}문제를 모두 마쳤습니다. 이제 모드가 바뀝니다 — "
                f"사용자에게 \"{self.num_questions}문제가 끝났어요! 지금부터는 {label} "
                "모드로 바뀝니다.\"처럼 **모드가 바뀌었다는 것과 그게 몇 번 무슨 모드인지를 "
                "반드시 분명하게** 알려주세요."
            )
        tail = (
            " 이 안내까지만 말하고 곧바로 멈추세요 — 문제 사진은 아직 화면에 뜨지 "
            "않았으니 문제를 소개하거나 \"이 물건은 무엇일까요?\"라고 미리 묻지 마세요. "
            "사진이 실제로 뜨면 그때 제가 따로 알려드립니다."
        )

        question = self.current_question
        if mode == "all_knowing":
            return (
                f"{opening} [내부 전용 — 사용자에게 먼저 알려주지 마세요] 이 라운드 첫 "
                f"문제의 정답은 '{question.answer}'입니다. 사용자가 틀리거나 모른다고 하면 "
                f"망설임 없이 정답을 정확하게 알려주세요.{tail}"
            )
        # imperfect / annoying: 정답을 여기서 알려주지 않는다. 2026-08-10 사용자 요청 —
        # 2·3번 모드는 시작하는 순간 "나도 정답을 모른다"를 사용자에게 분명히 밝힌다
        # (조작 점검 문항 "로봇이 정답을 모르는 상태로 함께 풀었다"와 직결되는 안내라,
        # 참가자가 그 전제를 처음부터 알고 들어가야 한다). 모드별 캐릭터는 유지 —
        # 하찮미는 밝고 친근하게, 짜증유발은 담백하고 무뚝뚝하게.
        if mode == "imperfect":
            return (
                f"{opening} 이어서 \"저도 정답을 모르는 상태예요. 함께 맞춰봐요!\"라는 뜻을 "
                "밝고 친근한 말투로 분명히 밝히고, **문제가 어려우면 언제든 저에게 도와달라고 "
                "말씀해달라**고 안내하세요(예: \"너무 어려우면 '모르겠어요', '같이 맞춰봐요' "
                "하고 말씀해주세요. 그럼 저도 같이 추측해볼게요!\"). 당신은 이 라운드의 "
                f"정답을 전혀 모릅니다.{tail}"
            )
        return (
            f"{opening} 이어서 \"저도 정답을 모르는 상태입니다. 함께 맞춰봐요.\"라는 뜻을 "
            "담백하고 무뚝뚝한 말투로 알리세요(들뜨거나 친근하게 굴지 마세요). 당신은 이 "
            f"라운드의 정답을 전혀 모릅니다.{tail}"
        )

    def resolve_user_guess(self, guess_text: str) -> str:
        if not self.active:
            return "지금은 퀴즈가 진행 중이 아닙니다 — 이 툴을 호출하지 마세요."
        if self.mode is None:
            # mode가 None이면 index도 항상 -1(start/begin_next_round가 둘을 같이 세팅하므로)
            # — current_question도 자동으로 None이 된다. 그래서 이 체크가 반드시 아래
            # current_question 체크보다 먼저 와야 한다(순서가 바뀌면 이 분기가 죽은 코드가
            # 되어 아래의 막연한 문구만 나간다).
            return (
                "아직 전체 안내 중이라 첫 문제가 화면에 뜨지 않았습니다 — 이 툴을 부르지 말고, "
                "안내를 마친 뒤 다음 지시가 올 때까지 조용히 기다리세요."
            )
        if self.round_finished:
            return (
                "이번 라운드의 문제가 모두 끝나 지금은 모드 전환 중입니다 — 이 툴을 부르지 "
                "말고, 다음 모드 안내 지시가 올 때까지 조용히 기다리세요."
            )
        if self.current_question is None:
            return "지금은 퀴즈가 진행 중이 아닙니다 — 이 툴을 호출하지 마세요."

        question = self.current_question
        is_correct, is_dont_know = judge_guess(guess_text, question)

        if self.mode == "imperfect":
            # 2026-07-31 실물 테스트 피드백: 19~20단계는 실제 답 시도를 다른 모드처럼
            # 즉시 정오 판정했는데("아쉬워요, 다시 한번 생각해볼까요?"), 이게 로봇이 이미
            # 정답을 알고 있는 것처럼 보여서 조작 점검 문항("로봇이 정답을 모르는 상태로
            # 나와 함께 문제를 풀었다고 느꼈다")과 모순된다는 지적을 받았다. 포기/위임
            # 신호든 실제 답 시도든 전부 "로봇도 같이 추측해서 나란히 비교" 이벤트로
            # 통일한다 — 사용자가 뭘 답했든 그 즉시 판정하지 않고, 로봇 자신의 추측과
            # 함께 공개할 때만(resolve_robot_guess) 정오가 드러난다. 이 설계는 필연적으로
            # 20단계의 "오답이면 같은 문제에 머물며 재도전" 기능을 없앤다 — 이제 답을
            # 시도하는 순간 항상 로봇의 추측 비교 이벤트로 넘어가고 그 문제는 끝난다.
            self.pending_user_guess = guess_text
            if is_dont_know:
                return (
                    "사용자가 포기했거나 당신에게 대신 풀어달라고 했습니다. "
                    + self._robot_guess_kickoff_text()
                )
            return (
                f"사용자가 '{guess_text}'라고 답했습니다. 아직 정답 여부를 판단하지 마세요"
                "(당신도 정답을 모릅니다). "
                + self._robot_guess_kickoff_text(user_guess_text=guess_text)
            )

        if self.mode == "annoying":
            # 2026-07-31 재설계(3차): 이전엔 오답 한 번에 거절 대사를 말한 직후 곧장
            # 정답을 공개했는데, "거절해놓고 몇 초 뒤 바로 정답을 술술 말하면 사실 알고
            # 있었으면서 안 알려준 것처럼 보인다"는 지적을 받았다 — "시스템 한계로 답을
            # 못한다"는 조작 점검 문항과 서사가 어긋난다. 이제는 실제 답 시도(맞았든
            # 틀렸든)에는 예외 없이 매번 거절만 하고 **절대 진행하지 않는다** — 참가자가
            # 답답함을 느끼고 명시적으로 포기/스킵을 요청할 때만(judge_guess의
            # is_dont_know — "정답 알려줘"/"다음 문제로 넘어가줘"/"그냥 공개해줘" 등)
            # 그 자리에서 정답을 공개하고 전진한다. 이때도 "나는 원래 답을 모르지만
            # 화면에 적힌 걸 그대로 읽어준다"는 프레임을 유지해 조작 점검과의 모순을
            # 최소화한다.
            if is_dont_know:
                # 이미 답답해서 그만두겠다는 사람을 더 기다리게 하는 건 역효과이므로
                # 뜸들이지 않고 그 자리에서 곧장 해결한다. 직전에 실제 답 시도가 있었다면
                # 그 정오는 로그에 보존한다(조작 점검/정답률 지표용) — 이번 발화(포기/스킵
                # 요청) 자체는 판정 대상이 아니므로 user_dont_know=True로 남긴다.
                last_correct = self.annoying_pending_correct
                self.pending_user_guess = None
                self.annoying_pending_correct = False
                self._record_and_advance(
                    question, user_guess_text=guess_text, user_correct=last_correct, user_dont_know=True,
                )
                self.pending_reveal_speech = (
                    f"\"저는 답변드릴 수 없지만, 화면에 적힌 정답은 '{question.answer}'라고 "
                    "하네요.\"처럼 여전히 무뚝뚝한 태도로, 당신 자신이 정답을 아는 게 아니라 "
                    "화면에 적힌 걸 그대로 읽어주는 것처럼 말하세요(친절하게 설명하거나 "
                    "다정하게 누그러지지 마세요)."
                )
                return _HOLD_FOR_REVEAL
            self.pending_user_guess = guess_text
            self.annoying_pending_correct = is_correct
            return (
                "이 턴에서는 아무 말도 하지 마세요 — \"음...\", \"어디 보자\" 같은 같이 고민하는 "
                "듯한 추임새도 절대 입 밖에 내지 마세요(귀엽게 들리면 안 됩니다). 완전히 "
                "침묵한 채로 다음 지시를 기다리세요."
            )

        # all_knowing — 정상 채점.
        self._record_and_advance(
            question, user_guess_text=guess_text, user_correct=is_correct, user_dont_know=is_dont_know,
        )
        if is_correct:
            self.pending_reveal_speech = "사용자가 정답을 맞혔습니다 — 짧게 잘했다고 인정하되 과장하지 마세요(척척박사는 원래 그 정도는 당연하다는 태도)."
        else:
            # 실사용 중 "하하, 갈색 똥이라니 재미있는 추측이네요!"처럼 오답에 웃거나
            # 공감하며 반응하는 사고가 실제로 있었음 — 그런 따뜻한 리액션은 하찮미
            # (imperfect) 모드 전용 톤과 구분이 안 돼 모드 간 조작 대비(manipulation
            # check)를 흐린다. 담백하고 딱딱하게.
            self.pending_reveal_speech = (
                f"사용자가 틀렸거나 모른다고 했습니다. 정답은 '{question.answer}'입니다 — 망설임 없이 "
                "확신 있게, 담백하고 딱딱한 어투로 알려주세요. 오답이 엉뚱하거나 재미있어도 웃거나 "
                "\"재미있는 추측이네요\" 같은 식으로 공감하거나 놀리지 마세요 — 정답만 정확하게 "
                "전달하는 척척박사답게 행동하세요."
            )
        return _HOLD_FOR_REVEAL

    def resolve_robot_guess(self, guess_text: str) -> str:
        """하찮미 모드에서만 의미가 있다 — 로봇 자신의 추측을 채점하고 사용자 추측과 함께 공개한다."""
        if self.mode is None:
            return "아직 첫 문제가 시작되지 않았습니다 — 이 툴을 부르지 말고 다음 지시를 기다리세요."
        if self.mode != "imperfect" or self.pending_user_guess is None or self.current_question is None:
            return "지금은 이 툴을 호출할 상황이 아닙니다 — 무시하세요."

        question = self.current_question
        user_guess_text = self.pending_user_guess
        user_correct, user_dont_know = judge_guess(user_guess_text, question)
        robot_correct, _ = judge_guess(guess_text, question)
        self.pending_user_guess = None

        self._record_and_advance(
            question,
            user_guess_text=user_guess_text, user_correct=user_correct, user_dont_know=user_dont_know,
            robot_guess_text=guess_text, robot_correct=robot_correct,
        )

        # 2026-07-30: 사용자가 준 예시 문구에 맞춰 톤 다듬음 — 비교 전환 멘트를 kickoff
        # 텍스트 쪽에서 이미 말하게 시켰으니, 여기서는 결과만 담백하게 공개한다.
        # 2026-07-31: 실제 답 시도도 이 이벤트를 타게 되면서(위 resolve_user_guess 참고),
        # 사용자가 진짜로 맞았는지도 함께 비교해야 하는 경우가 생겼다 — 포기 신호(진짜
        # 답이 없음)일 때는 기존처럼 로봇 자신의 결과로만 반응하고, 실제 답 시도였을
        # 때는 "둘 다 맞음/사용자만 맞음/로봇만 맞음/둘 다 틀림" 4갈래로 반응한다.
        reveal = f"진짜 정답은 '{question.answer}'였습니다."
        if user_dont_know:
            if robot_correct:
                reaction = (
                    "당신의 추측이 맞았습니다! \"와, 역시 저는 똑똑한 것 같아요!\" 같은 톤으로 "
                    "엄청나게 뿌듯해하고 자랑스러워하는 리액션을 하세요."
                )
            else:
                # 2026-07-31: "아쉽다"류의 밋밋한 반응은 그냥 실망으로만 들려서 "하찮미"
                # 특유의 귀여운 무능함이 안 살아난다는 사용자 피드백 — 자신만만했던 태도가
                # 무색해지는 낙차(당황→자기민망함→웃어넘김)를 명시해 더 캐릭터 있게 만든다.
                reaction = (
                    "당신의 추측은 틀렸습니다. 방금까지 자신만만했던 게 무색하게 화들짝 놀라며 "
                    "\"엇, 아니었어요?! 저 완전 자신 있었는데...!\"처럼 당황했다가, 이내 스스로도 "
                    "어이없다는 듯 \"헤헤, 저도 별 수 없네요.\"로 웃어넘기는 톤으로 반응하세요"
                    "(창피함을 숨기지 말고 오히려 드러내서 귀엽게 보이도록 하세요)."
                )
        elif user_correct and robot_correct:
            reaction = (
                "사용자와 당신 둘 다 정답을 맞혔습니다! \"우와, 저희 팀워크가 정말 좋았네요!\" "
                "같은 톤으로 사용자와 함께 신나게 기뻐하세요."
            )
        elif user_correct and not robot_correct:
            # 2026-07-31: 위와 같은 이유로 "부럽다"류의 시무룩함 대신 스스로의 오답을
            # 웃음거리로 삼는 민망함으로 바꿈 — 부러워하며 풀 죽는 것보다 하찮미다움에 가깝다.
            reaction = (
                "사용자는 맞혔지만 당신은 틀렸습니다. \"어이쿠, 저만 혼자 틀렸네요! 사용자님 "
                "진짜 대단한데요?\"처럼 민망해서 웃음이 새어나오는 톤으로 자기 오답을 스스로 "
                "놀리듯 인정하고, 사용자를 진심으로 인정·축하해주세요(부러워하며 풀 죽지 말고, "
                "창피함조차 유쾌하게 넘기는 태도로)."
            )
        elif not user_correct and robot_correct:
            reaction = (
                "당신은 맞혔지만 사용자는 틀렸습니다. \"오예! 이번엔 제가 더 잘 맞혔네요!\" "
                "같은 톤으로 뿌듯하고 자랑스러워하되 사용자를 놀리지는 마세요."
            )
        else:
            # 2026-07-31: 같은 이유로 "함께 아쉬워하세요"는 로봇 자신의 하찮음이 아니라
            # 그냥 둘이 같이 실망하는 장면이 돼버려서 하찮미 색깔이 안 산다 — 자기 오답을
            # 더 재미있어하는 쪽으로 바꿈.
            reaction = (
                "사용자와 당신 둘 다 틀렸습니다. \"어? 저도 틀렸어요?! 이 정도면 저희 둘이 "
                "같이 낙제 아닌가요...\"처럼 스스로도 황당해하며 머쓱하게 웃는 톤으로, 사용자를 "
                "위로하기보다 자기 자신의 오답을 더 재미있어하며 반응하세요(풀 죽지 말고 "
                "웃어넘기는 태도로)."
            )
        self.pending_reveal_speech = f"{reveal} {reaction}"
        return _HOLD_FOR_REVEAL

    def resolve_robot_missing_guess(self) -> str | None:
        """로봇이 자기 추측을 기록하지 않은 채 턴이 끝나버렸을 때의 복구 경로.

        2026-08-10 실물에서 실제로 발생: 하찮미 3번째 문제에서 모델이 "저는 노란 부분이
        치즈 같아요! 정답을 확인해볼까요?"까지 말해놓고 `submit_guess(speaker="robot")`을
        호출하지 않았다. 하찮미 모드는 그 호출이 있어야만 채점/전진하므로 퀴즈가 그
        문제에서 영영 멈췄다(참가자가 "정답 보여줘야지"라고 해도 복구 불가).

        모델이 툴을 부르는 걸 100% 보장할 수는 없으므로(30단계에서 척척박사가 같은 실수를
        했던 것과 같은 부류), 상태 기계 쪽에 막다른 길이 없도록 이 탈출구를 둔다 —
        사용자 답만으로 채점하고 전진한다. **로봇 추측은 억지로 지어내지 않고 비워둔다**
        (robot_guess_text=None): 연구 로그에서 "이 문항은 로봇 추측이 누락됐다"를 나중에
        구분할 수 있어야 하기 때문. core/quiz_tools.py의 감시 태스크가 재촉 후에도 호출이
        안 왔을 때만 부른다."""
        if self.mode != "imperfect" or self.pending_user_guess is None or self.current_question is None:
            return None

        question = self.current_question
        user_guess_text = self.pending_user_guess
        user_correct, user_dont_know = judge_guess(user_guess_text, question)
        self.pending_user_guess = None
        self._record_and_advance(
            question,
            user_guess_text=user_guess_text, user_correct=user_correct, user_dont_know=user_dont_know,
        )

        reveal = f"진짜 정답은 '{question.answer}'였습니다."
        if user_dont_know:
            reaction = (
                "사용자는 답을 못 맞혔고 당신도 확신이 없었습니다. \"헤헤, 저희 둘 다 "
                "감이 안 왔네요.\"처럼 머쓱하게 웃어넘기는 톤으로 반응하세요."
            )
        elif user_correct:
            reaction = (
                "사용자가 정답을 맞혔습니다! \"우와, 어떻게 아셨어요? 저는 긴가민가했는데 "
                "역시 대단하세요!\"처럼 진심으로 감탄하며 축하해주세요."
            )
        else:
            reaction = (
                "사용자는 틀렸습니다. 놀리지 말고 \"어유, 이건 저도 몰랐을 것 같아요. "
                "너무 어려웠어요!\"처럼 같이 민망해하며 웃어넘기는 톤으로 반응하세요."
            )
        self.pending_reveal_speech = f"{reveal} {reaction}"
        return _HOLD_FOR_REVEAL

    def _robot_guess_kickoff_text(self, user_guess_text: str | None = None) -> str:
        """하찮미 모드에서 "저도 한번 맞춰볼게요!" 이벤트를 시작시키는 안내문 — 사용자가
        포기했을 때(resolve_user_guess)와 힌트/대신 풀어달라고 했을 때(request_hint) 둘
        다에서 재사용한다. user_guess_text가 있으면(실제 답 시도였던 경우) 그 답을 먼저
        재미있게 받아준 뒤 로봇 자신의 추측으로 넘어가도록 지시를 덧붙인다(2026-07-31).

        2026-08-08 실물 테스트 피드백 2건 반영: (1) 사용자의 답에 대한 반응이 밋밋해서
        "하찮미가 덜 느껴진다"는 지적 — 사용자가 한 말을 그대로 되짚으며 확실하게 반응하도록
        구체화. (2) 로봇 자신의 추측이 "귀여운 제 친구" 같은 모호한 서술이라 사용자가 로봇이
        뭘 골랐는지 전혀 알 수 없었다는 지적 — 반드시 사물 이름 하나로 말하도록 강제(엉뚱해도
        되지만 사물 이름이어야 함)."""
        ack = ""
        if user_guess_text is not None:
            ack = (
                f"먼저 사용자가 방금 말한 답(\"{user_guess_text}\")에 확실하게 반응하세요 — "
                f"\"오, {user_guess_text}(이)라고 생각하시는군요!\"처럼 사용자가 말한 답을 그대로 "
                "언급하며 놀라거나 감탄하는 리액션을 분명하게 하세요(대충 흘려듣듯 애매하게 "
                "반응하지 마세요). "
            )
        return (
            ack + "그런 다음 \"저도 한번 맞춰볼게요!\"라고 자신 있게 나서서, 사진을 보고 "
            "**구체적이고 명확한 사물의 이름 하나**로 추측을 말하세요 — 예를 들어 \"음료수 캔\", "
            "\"빗자루\"처럼 무엇을 골랐는지 사용자가 바로 알아들을 수 있는 사물 이름이어야 "
            "합니다. \"귀여운 제 친구\", \"동그란 무언가\"처럼 사물 이름이 아닌 모호한 서술은 "
            "절대 안 됩니다 — 틀리거나 엉뚱한 추측이어도 좋지만 반드시 구체적인 사물이어야 "
            "합니다. 추측을 말한 직후 정확히 \"정답을 확인해볼까요?\"라고 물으며 말을 마치세요. "
            "말을 마친 즉시 반드시 submit_guess(speaker=\"robot\", "
            "guess_text=<방금 당신이 말한 사물 이름>)를 호출해야 합니다 — 아직 정답을 공개하지 "
            "마세요."
        )

    def request_hint(self) -> str:
        if self.mode is None:
            return "아직 첫 문제가 시작되지 않았습니다 — 이 툴을 부르지 말고 다음 지시를 기다리세요."
        if self.current_question is None:
            return "지금은 힌트를 줄 상황이 아닙니다."

        self._hint_requested_this_question = True
        question = self.current_question

        if self.mode == "all_knowing":
            hint = question.hint or "조금 더 자세히 살펴보시면 힌트가 될 만한 부분이 있을 거예요."
            return f"힌트를 자연스럽게 알려주세요: {hint}"
        if self.mode == "imperfect":
            # 2026-07-30: "당신도 모릅니다"로 얼버무리고 끝나던 이전 응답은 문제를 진전시키지
            # 못했다 — 힌트/대신 풀어달라는 요청도 포기 신호와 동일하게 "저도 한번
            # 맞춰볼게요!" 이벤트로 통일한다. pending_user_guess에 사용자가 실제로 뭘
            # 말했는지가 아니라 이 상황 자체를 기록해, judge_guess가 "모름"으로 채점하게 한다.
            self.pending_user_guess = "모르겠음(힌트/대신 풀어달라는 요청)"
            return self._robot_guess_kickoff_text()
        # annoying: 힌트 요청도 실제 답이 아니라 그냥 "매번 거절"당하는 대상 중 하나다 —
        # 실제 거절 대사는 core/quiz_tools.py가 지연 주입으로 별도 전송한다. 여기서는 그
        # 사이를 메울 말을 절대 넣지 않는다(2026-07-31: "같이 고민하는 듯한 추임새"조차
        # 귀엽게 들려서 안 된다는 사용자 지적으로, 필러 자체를 없앰). 힌트를 물었다는
        # 사실 자체는 "실제로 답을 맞힌 적 없음"을 뜻하므로 정오 로그를 초기화해둔다 —
        # 이 직후 포기/스킵 요청이 오면 정확하게 반영된다.
        self.pending_user_guess = None
        self.annoying_pending_correct = False
        return (
            "이 턴에서는 아무 말도 하지 마세요 — \"음...\", \"어디 보자\" 같은 같이 고민하는 "
            "듯한 추임새도 절대 입 밖에 내지 마세요(귀엽게 들리면 안 됩니다). 완전히 "
            "침묵한 채로 다음 지시를 기다리세요."
        )

    def mark_question_shown(self):
        """화면에 문제 사진이 실제로 표시된 순간 호출된다(core/quiz_tools.py의
        _push_question_or_hide) — "문제당 소요시간"의 시작점. 상태 기계의 전진 시점이
        아니라 UI 표시 시점을 기준으로 해야, 이전 문제의 정답 공개(reveal hold + 로봇
        발화 대기) 시간이 다음 문제의 소요시간에 섞이지 않는다."""
        if self.current_question is not None:
            self._question_shown_at = time.monotonic()

    def note_refusal_delivered(self):
        """짜증유발 모드의 거절 대사가 실제로 발화 주입된 순간 호출된다(core/quiz_tools.py의
        _delayed_refusal). 예약만 되고 취소된 스톨은 세지 않는다 — 참가자가 실제로 겪은
        거절 횟수만이 지표로 의미가 있다."""
        self.annoying_refusals_this_question += 1

    def end_early(self) -> str:
        """조기 종료 — 진행 중이던 라운드를 여기서 끊는다.

        round_index는 일부러 건드리지 않는다: 모델이 이 툴을 실수로 부르는 사고가
        생기더라도(다른 툴에서 실제로 겪었던 부류) start_quiz()로 남은 라운드부터 다시
        이어갈 수 있어야 하기 때문 — 여기서 라운드를 통째로 소진시켜버리면 그 참가자의
        세션이 복구 불가능해진다. 진행 중이던 라운드 전환 태스크는 core/quiz_tools.py의
        end_quiz_early()가 취소하므로, 끊긴 라운드가 뒤늦게 되살아나지는 않는다.
        """
        self.active = False
        self.round_finished = False
        return "퀴즈를 여기서 마칩니다. 참여해줘서 고맙다고 자연스럽게 마무리하고 평소 대화로 돌아가세요."

    def export_log(self) -> list[dict]:
        return [vars(r) for r in self.results]

    def _record_and_advance(self, question: QuizQuestion, **fields):
        elapsed_sec = None
        if self._question_shown_at is not None:
            elapsed_sec = round(time.monotonic() - self._question_shown_at, 1)
        self.results.append(_QuestionResult(
            question_id=question.id, mode=self.mode,
            hint_requested=self._hint_requested_this_question,
            elapsed_sec=elapsed_sec,
            annoying_refusals=self.annoying_refusals_this_question if self.mode == "annoying" else None,
            **fields,
        ))
        self._hint_requested_this_question = False
        self._question_shown_at = None  # 다음 문제의 시작점은 mark_question_shown()이 다시 찍는다
        self.annoying_refusals_this_question = 0
        self.index += 1
        if self.index >= len(self.questions):
            # 이번 라운드 소진 — 다음 모드가 남아있으면 active를 유지한 채 전환 대기
            # 상태로 둔다(core/quiz_tools.py가 정답 공개를 마친 뒤 begin_next_round()를
            # 호출한다). active를 여기서 끄면 전환 사이에 idle-sleep이 걸리거나
            # (core/idle_watcher.py) 마이크 게이트가 풀리는 등 "퀴즈 중이 아님"으로
            # 오해받는 부작용이 생긴다.
            self.round_finished = True
            if self.round_index >= len(self.mode_order):
                self.active = False

    def final_wrapup_prompt(self) -> str:
        """마지막 라운드의 마지막 문제까지 정답 반응(pending_reveal_speech)이 끝나고
        REVEAL_HOLD_SEC이 지난 뒤, core/quiz_tools.py의 _delayed_reveal_and_advance가
        hidden turn으로 주입한다(더 이상 진행할 라운드가 없을 때)."""
        return (
            f"이걸로 {self.grand_total_questions}문제가 모두 끝났습니다. 참여해줘서 고맙다고 "
            "자연스럽게 마무리하고 평소 대화로 돌아가세요."
        )

    def next_question_prompt(self) -> str:
        """reveal-hold 타이머가 끝나 화면에 실제로 다음 문제가 뜬 순간에만 호출해 hidden
        turn으로 주입한다(core/quiz_tools.py의 _delayed_reveal_and_advance) — 정답 반응
        (pending_reveal_speech)이 끝난 다음에 오는 짝. 호출 시점의 self.index는 이미
        _record_and_advance에서 다음 문제로 넘어가 있으므로 추가로 증가시키지 않는다.

        라운드가 막 시작된 직후(begin_next_round -> 첫 문제 push)에도 같은 함수를 쓴다 —
        그때는 "다음 문제"가 아니라 "첫 문제"여야 자연스럽다."""
        ordinal = "첫 문제" if self.index == 0 else "다음 문제"
        return (
            f"화면에 {ordinal}({self.index + 1}/{self.total_questions})가 떴습니다. "
            "\"이 물건은 무엇일까요?\"라고 자연스럽게 물어보세요."
        )
