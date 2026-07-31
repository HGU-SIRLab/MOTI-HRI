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
        # 모든 참가자에게 같은 문제 은행/같은 순서 — 셔플하지 않는다. 실제 라운드별
        # 슬라이스는 start()가 _pick_round_questions()로 매번 새로 정한다(아래 참고).
        self.all_questions = questions
        self.num_questions = num_questions
        self.questions: list[QuizQuestion] = []
        self._rounds_played: int = 0
        self.mode: str | None = None
        self.index: int = -1
        self.active: bool = False
        self.awaiting_mode_choice: bool = False
        self.pending_user_guess: str | None = None
        # 짜증유발 모드 전용 — 실제 답 시도(맞았든 틀렸든)의 정오를 기억해뒀다가, 나중에
        # 참가자가 포기/스킵을 요청하면 그 시점의 로그에 반영한다(2026-07-31, 응답 자체는
        # 항상 거절이라 정오가 그 자리에서 드러나지는 않지만 조작 점검/정답률 지표로는
        # 남겨둘 가치가 있다).
        self.annoying_pending_correct: bool = False
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

    def _pick_round_questions(self) -> list[QuizQuestion]:
        """세션 하나 안에서 참가자가 1/2/3번 모드를 연속으로 진행할 때(실험 운영 방식,
        2026-07-31 확정), 매 라운드(=매 start() 호출)마다 앞선 라운드와 겹치지 않는
        문제 세트를 돌려준다. 같은 사진을 다음 라운드에 또 보여주면, 척척박사 라운드에서
        이미 정답 공개(reveal)를 본 참가자가 다음 모드에서 정답을 이미 아는 채로 반응하게
        되어 하찮미/짜증유발 효과 자체를 가리는 심각한 교란요인이 된다(코드 리뷰로 발견,
        이전엔 한 참가자가 한 라운드만 한다고 가정하고 짠 코드였음).

        문제 은행이 num_questions * (몇 번째 라운드인지)만큼 충분하면 완전히 겹치지 않는
        세트가 나온다. 은행이 부족하면(예: 준비된 사진이 모자라 3라운드 분량이 안 되는
        경우) 어쩔 수 없이 앞 라운드와 겹치는 세트로 되돌아가되, 실험자가 즉시 알아챌 수
        있게 콘솔에 경고를 남긴다 — 조용히 겹쳐서 데이터가 오염되는 것보다는 낫다.
        """
        total = len(self.all_questions)
        n = self.num_questions
        start_idx = self._rounds_played * n
        self._rounds_played += 1
        if total and start_idx >= total:
            print(
                f"⚠️ 문제 은행({total}개)이 부족해 이번 라운드가 이전 라운드와 겹치는 문제를 "
                f"재사용합니다 — assets/quiz/questions.json에 문제를 추가하세요."
            )
            start_idx %= total
        return self.all_questions[start_idx:start_idx + n]

    def start(self) -> str:
        self.active = True
        self.awaiting_mode_choice = True
        self.mode = None
        self.index = -1
        self.questions = self._pick_round_questions()
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
                feedback = (
                    f"\"저는 답변드릴 수 없지만, 화면에 적힌 정답은 '{question.answer}'라고 "
                    "하네요. 다음으로 넘어가겠습니다.\"처럼 여전히 무뚝뚝한 태도로, 당신 "
                    "자신이 정답을 아는 게 아니라 화면에 적힌 걸 그대로 읽어주는 것처럼 "
                    "말하세요(친절하게 설명하거나 다정하게 누그러지지 마세요)."
                )
                return f"{feedback}\n{self._next_step_text()}"
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
            feedback = "사용자가 정답을 맞혔습니다 — 짧게 잘했다고 인정하되 과장하지 마세요(척척박사는 원래 그 정도는 당연하다는 태도)."
        else:
            # 실사용 중 "하하, 갈색 똥이라니 재미있는 추측이네요!"처럼 오답에 웃거나
            # 공감하며 반응하는 사고가 실제로 있었음 — 그런 따뜻한 리액션은 하찮미
            # (imperfect) 모드 전용 톤과 구분이 안 돼 모드 간 조작 대비(manipulation
            # check)를 흐린다. 담백하고 딱딱하게.
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
                reaction = (
                    "당신의 추측은 틀렸습니다. \"헤헤... 틀렸네요.. 아쉽다.\" 같은 톤으로 귀엽고 "
                    "머쓱하게 반응하세요."
                )
        elif user_correct and robot_correct:
            reaction = (
                "사용자와 당신 둘 다 정답을 맞혔습니다! \"우와, 저희 팀워크가 정말 좋았네요!\" "
                "같은 톤으로 사용자와 함께 신나게 기뻐하세요."
            )
        elif user_correct and not robot_correct:
            reaction = (
                "사용자는 맞혔지만 당신은 틀렸습니다. \"와... 저는 틀렸는데 사용자님은 "
                "맞히셨네요, 부럽다...\" 같은 톤으로 살짝 시무룩하되 사용자를 인정하고 "
                "축하해주세요."
            )
        elif not user_correct and robot_correct:
            reaction = (
                "당신은 맞혔지만 사용자는 틀렸습니다. \"오예! 이번엔 제가 더 잘 맞혔네요!\" "
                "같은 톤으로 뿌듯하고 자랑스러워하되 사용자를 놀리지는 마세요."
            )
        else:
            reaction = (
                "사용자와 당신 둘 다 틀렸습니다. \"어머, 둘 다 틀렸네요... 아쉽다.\" 같은 "
                "톤으로 함께 아쉬워하세요."
            )
        return f"{reveal} {reaction}\n{self._next_step_text()}"

    def _robot_guess_kickoff_text(self, user_guess_text: str | None = None) -> str:
        """하찮미 모드에서 "저도 한번 맞춰볼게요!" 이벤트를 시작시키는 안내문 — 사용자가
        포기했을 때(resolve_user_guess)와 힌트/대신 풀어달라고 했을 때(request_hint) 둘
        다에서 재사용한다. user_guess_text가 있으면(실제 답 시도였던 경우) 그 답을 먼저
        재미있게 받아준 뒤 로봇 자신의 추측으로 넘어가도록 지시를 덧붙인다(2026-07-31)."""
        ack = ""
        if user_guess_text is not None:
            ack = (
                f"먼저 사용자가 방금 말한 답(\"{user_guess_text}\")에 대해 \"오, 사용자님 생각은 "
                "그거군요!\"처럼 재미있게 반응한 뒤, "
            )
        return (
            ack + "\"저도 한번 정답을 맞춰볼게요! 사진을 보니 제 생각엔 이거인 것 같아요!\"처럼 "
            "자신 있게 나서서 사진을 보고 그럴듯한 추측을 하나 말한 뒤(당신 자신의 정체성이나 "
            "일상과 연관 지은 엉뚱하고 귀여운 오답이어도 좋습니다), \"한번 진짜 정답과 "
            "비교해볼까요?\"라고 덧붙이세요. 말을 마친 직후 반드시 "
            "submit_guess(speaker=\"robot\", guess_text=<방금 당신이 말한 추측>)를 호출해야 "
            "합니다 — 아직 정답을 공개하지 마세요."
        )

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
        # 2026-07-30 실사용 중 발견: 여기서 곧장 "다음 문제로 넘어가서 물어보세요"라고
        # 돌려주면, 화면이 아직 reveal-hold(REVEAL_HOLD_SEC) 중이라 이전 문제의 정답
        # 공개 화면인 채로 모델이 다음 문제를 물어버려 말과 화면이 따로 노는 문제가 있었다.
        # 대신 여기서는 짧게 대기만 시키고, 실제로 화면이 넘어간 순간(core/quiz_tools.py의
        # _delayed_advance)에 next_question_prompt()를 hidden turn으로 주입해 동기화한다.
        if self.active and self.current_question is not None:
            return (
                "다음 문제가 화면에 뜰 때까지 아직 몇 초 걸립니다 — 지금 바로 다음 문제를 "
                "묻지 마세요. \"그럼 다음 문제로 가볼까요?\" 정도의 짧은 말만 하고 자연스럽게 "
                "기다리면, 화면이 실제로 바뀌는 순간 다시 안내해 드리겠습니다."
            )
        return "이걸로 모든 문제가 끝났습니다. 참여해줘서 고맙다고 자연스럽게 마무리하고 평소 대화로 돌아가세요."

    def next_question_prompt(self) -> str:
        """reveal-hold 타이머가 끝나 화면에 실제로 다음 문제가 뜬 순간에만 호출해 hidden
        turn으로 주입한다(core/quiz_tools.py의 _delayed_advance) — _next_step_text()가
        돌려준 "잠시 기다리세요" 다음에 오는 짝. 호출 시점의 self.index는 이미
        _record_and_advance에서 다음 문제로 넘어가 있으므로 추가로 증가시키지 않는다."""
        return (
            f"화면에 다음 문제({self.index + 1}/{self.total_questions})가 떴습니다. "
            "\"이 물건은 무엇일까요?\"라고 자연스럽게 물어보세요."
        )
