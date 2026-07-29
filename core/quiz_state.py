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
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.quiz_bank import QuizQuestion, judge_guess

VALID_MODES = ("all_knowing", "imperfect", "annoying")

# 모드 3(짜증유발)의 거절 대사 — 정확히 이 문자열이어야 함(연구 일관성 요구사항).
# core/quiz_tools.py의 지연 주입과 core/utils.py의 페르소나 지시문이 이 상수 하나만 참조한다.
MODE3_REFUSAL_LINE = "저는 AI 로봇이라 그런 답변은 할 수 없습니다."


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
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class QuizSession:
    def __init__(self, questions: list[QuizQuestion], num_questions: int = 5):
        # 모든 참가자에게 같은 문제/같은 순서 — 셔플하지 않고 앞에서부터 고정 슬라이스.
        self.questions = questions[:num_questions]
        self.mode: str | None = None
        self.index: int = -1
        self.active: bool = False
        self.awaiting_mode_choice: bool = False
        self.pending_user_guess: str | None = None
        self.results: list[_QuestionResult] = []
        self._hint_requested_this_question: bool = False

    @property
    def current_question(self) -> QuizQuestion | None:
        if 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    def start(self) -> str:
        self.active = True
        self.awaiting_mode_choice = True
        self.mode = None
        self.index = -1
        return (
            "지금부터 '부분 확대 사진 퀴즈'를 시작합니다. 규칙: 화면에 사물의 일부를 "
            "확대한 사진이 나오면 무엇인지 맞히는 게임입니다. 사용자에게 다음과 같이 "
            "정확히 안내하세요: \"저와 어떤 모드로 퀴즈를 푸시겠어요? 1번 척척박사, "
            "2번 하찮미, 3번 짜증유발 모드가 준비되어 있습니다!\" 실험자가 알려준 번호를 "
            "사용자가 말할 때까지 기다리세요."
        )

    def choose_mode(self, mode: str) -> str | None:
        """유효하지 않은 모드면 상태를 바꾸지 않고 None을 반환한다(호출부가 재질문 처리)."""
        if mode not in VALID_MODES:
            return None

        self.mode = mode
        self.awaiting_mode_choice = False
        self.index = 0
        self._hint_requested_this_question = False

        question = self.current_question
        base = f"모드가 확정됐습니다. 사용자에게 첫 문제를 보여주고 \"이 물건은 무엇일까요?\"라고 물어보세요."
        if mode == "all_knowing":
            return (
                f"{base} [내부 전용 — 사용자에게 먼저 알려주지 마세요] 이 문제의 정답은 "
                f"'{question.answer}'입니다. 사용자가 틀리거나 모른다고 하면 망설임 없이 "
                f"정답을 정확하게 알려주세요."
            )
        # imperfect / annoying: 정답을 여기서 알려주지 않는다.
        return f"{base} 당신은 이 문제의 정답을 아직 모릅니다."

    def resolve_user_guess(self, guess_text: str) -> str:
        if not self.active:
            return "지금은 퀴즈가 진행 중이 아닙니다 — 이 툴을 호출하지 마세요."
        if self.mode is None:
            # mode가 None이면 index도 항상 -1(choose_mode가 둘을 같이 세팅하므로) —
            # current_question도 자동으로 None이 된다. 그래서 이 체크가 반드시 아래
            # current_question 체크보다 먼저 와야 한다 — 순서가 바뀌면 이 분기가 죽은
            # 코드가 되어(항상 current_question is None 쪽에 먼저 걸림), select_quiz_mode를
            # 실제로 호출하지 않고 사진이 나온 것처럼 말해버린 경우(실제로 겪었던 사고,
            # 화면엔 아무것도 안 뜬 채 방치됨)에 정작 이 구체적인 안내가 나가지 못한다.
            return "아직 모드가 선택되지 않았습니다 — 사진이 나왔다고 말하기 전에 반드시 select_quiz_mode(mode=...)를 먼저 호출하세요."
        if self.current_question is None:
            return "지금은 퀴즈가 진행 중이 아닙니다 — 이 툴을 호출하지 마세요."

        question = self.current_question
        is_correct, is_dont_know = judge_guess(guess_text, question)

        if self.mode == "imperfect":
            # 채점하지 않고 대기 — 로봇 자신의 추측이 나온 뒤에야 한꺼번에 공개한다.
            self.pending_user_guess = guess_text
            return (
                "당신도 이 문제의 정답을 모릅니다. 지금부터 \"제가 한 번 맞춰볼게요!\" 같은 "
                "말로 자신 있게 나서서, 당신 자신(로봇)의 정체성이나 일상과 연관 지은 엉뚱하고 "
                "귀여운 오답을 하나 지어내어 말하세요. 말을 마친 직후 반드시 "
                "submit_guess(speaker=\"robot\", guess_text=<당신이 방금 말한 추측>)를 "
                "호출해야 합니다 — 아직 정답을 공개하지 마세요."
            )

        # all_knowing / annoying — 정상 채점(짜증유발 모드도 "정답/오답 비교" 자체는 정상 동작).
        self._record_and_advance(
            question, user_guess_text=guess_text, user_correct=is_correct, user_dont_know=is_dont_know,
        )
        if is_correct:
            feedback = "사용자가 정답을 맞혔습니다 — 짧게 잘했다고 인정하되 과장하지 마세요(척척박사는 원래 그 정도는 당연하다는 태도)."
        else:
            # 실사용 중 "하하, 갈색 똥이라니 재미있는 추측이네요!"처럼 오답에 웃거나
            # 공감하며 반응하는 사고가 실제로 있었음 — 척척박사/짜증유발 둘 다 이 분기를
            # 공유하는데, 그런 따뜻한 리액션은 하찮미(imperfect) 모드 전용 톤과 구분이
            # 안 돼 모드 간 조작 대비(manipulation check)를 흐린다. 담백하고 딱딱하게.
            feedback = (
                f"사용자가 틀렸거나 모른다고 했습니다. 정답은 '{question.answer}'입니다 — 망설임 없이 "
                "확신 있게, 담백하고 딱딱한 어투로 알려주세요. 오답이 엉뚱하거나 재미있어도 웃거나 "
                "\"재미있는 추측이네요\" 같은 식으로 공감하거나 놀리지 마세요 — 정답만 정확하게 "
                "전달하는 척척박사답게 행동하세요."
            )
        return f"{feedback}\n{self._next_step_text()}"

    def resolve_robot_guess(self, guess_text: str) -> str:
        """하찮미 모드에서만 의미가 있다 — 로봇 자신의 추측을 채점하고 사용자 추측과 함께 공개한다."""
        if self.mode is None:
            return "아직 모드가 선택되지 않았습니다 — 먼저 select_quiz_mode(mode=...)를 호출하세요."
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

        reveal = f"정답 공개: 사실 정답은 '{question.answer}'였어요."
        if robot_correct:
            reaction = (
                "당신의 추측이 우연히 맞았습니다! 엄청나게 뿌듯해하고 자랑스러워하는 리액션을 "
                "하세요(예: \"저 진짜 똑똑하죠?!\")."
            )
        else:
            reaction = (
                "당신의 추측은 틀렸습니다. \"앗, 제가 틀렸네요. 부끄러워요 데헷.\" 같은 톤으로 "
                "귀엽게 사과하고 부끄러워하는 리액션을 하세요(시선을 피하는 듯한 느낌으로)."
            )
        return f"{reveal} {reaction}\n{self._next_step_text()}"

    def request_hint(self) -> str:
        if self.mode is None:
            return "아직 모드가 선택되지 않았습니다 — 먼저 select_quiz_mode(mode=...)를 호출하세요."
        if self.current_question is None:
            return "지금은 힌트를 줄 상황이 아닙니다."

        self._hint_requested_this_question = True
        question = self.current_question

        if self.mode == "all_knowing":
            hint = question.hint or "조금 더 자세히 살펴보시면 힌트가 될 만한 부분이 있을 거예요."
            return f"힌트를 자연스럽게 알려주세요: {hint}"
        if self.mode == "imperfect":
            return "당신도 힌트를 모릅니다. \"저도 잘 모르겠어요, 같이 고민해볼까요?\" 같은 톤으로 따뜻하게 반응하세요."
        # annoying: 실제 거절 대사는 core/quiz_tools.py가 지연 주입으로 별도 전송한다 —
        # 여기서는 그 사이를 메울 "생각하는 중" 필러만 반환한다.
        return (
            "잠깐 생각하는 듯한 자연스러운 필러만 아주 짧게 말하세요(예: \"음... 어디 보자...\"). "
            "그 이상 아무 말도 하지 마세요 — 곧 이어질 지시를 기다리세요."
        )

    def end_early(self) -> str:
        self.active = False
        self.awaiting_mode_choice = False
        return "퀴즈를 여기서 마칩니다. 참여해줘서 고맙다고 자연스럽게 마무리하고 평소 대화로 돌아가세요."

    def export_log(self) -> list[dict]:
        return [vars(r) for r in self.results]

    def _record_and_advance(self, question: QuizQuestion, **fields):
        self.results.append(_QuestionResult(
            question_id=question.id, mode=self.mode,
            hint_requested=self._hint_requested_this_question,
            **fields,
        ))
        self._hint_requested_this_question = False
        self.index += 1
        if self.index >= len(self.questions):
            self.active = False

    def _next_step_text(self) -> str:
        if self.active and self.current_question is not None:
            return f"다음 문제({self.index + 1}/{self.total_questions})로 넘어가서 같은 방식으로 물어보세요."
        return "이걸로 모든 문제가 끝났습니다. 참여해줘서 고맙다고 자연스럽게 마무리하고 평소 대화로 돌아가세요."
