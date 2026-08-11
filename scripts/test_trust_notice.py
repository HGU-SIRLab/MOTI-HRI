"""프라이버시 안내 멘트(core/trust_notice.py) + launcher의 전달 확인/보정 로직 오프라인 테스트.

launcher.py의 handle_trust_notice()는 Live 세션 안에 있어 직접 부를 수 없으므로, **같은
상태 전이를 그대로 옮긴 사본**으로 검증한다(core/quiz_state.py를 순수 함수로 분리한 것과
같은 이유로 판정 로직 자체는 trust_notice.py에 있고, 여기서는 그 조합만 확인한다).
로직을 고치면 이 사본도 같이 고칠 것.

사용:
    python scripts/test_trust_notice.py
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from bootstrap import ensure_utf8_console

ensure_utf8_console()

from core import trust_notice


def check(label, condition):
    print(("OK  " if condition else "FAIL") + ": " + label)
    return condition


class FakeFlow:
    """launcher.handle_trust_notice()와 같은 상태 전이. injected에 주입된 히든 턴을 쌓는다."""

    def __init__(self, name=None, quiz_running=False):
        self.name = name
        self.quiz_running = quiz_running
        self.injected = []
        self.state = {"opening_delivered": False, "closing_delivered": False,
                      "opening_attempts": 0, "closing_attempts": 0}

    def turn(self, spoken: str, should_end: bool = False) -> bool:
        s = self.state
        delivered = trust_notice.was_delivered(spoken)
        if should_end or s["closing_attempts"] > 0:
            if delivered:
                s["closing_delivered"] = True
                return True
            if s["closing_attempts"] >= 1:
                return True
            s["closing_attempts"] += 1
            self.injected.append(trust_notice.closing_prompt(self.name))
            return False
        if delivered and not s["opening_delivered"]:
            s["opening_delivered"] = True
            return False
        if (self.name and not s["opening_delivered"]
                and s["opening_attempts"] < 2 and not self.quiz_running):
            s["opening_attempts"] += 1
            self.injected.append(trust_notice.opening_prompt(self.name))
        return False


def main():
    ok = True

    # --- 문장 자체 ---
    ok &= check("시작 안내에 이름이 들어간다",
                "조형민님의 대화는" in trust_notice.opening_notice("조형민"))
    ok &= check("마무리 안내에 이름이 들어간다",
                "조형민님과 나눈 대화는" in trust_notice.closing_notice("조형민"))
    # launcher.py는 core.utils.short_name()을 거쳐 넘긴다 — 로봇이 평소 "형민님"이라고
    # 부르다가 안내할 때만 "조형민님"이라고 하면 튄다.
    from core.utils import short_name
    ok &= check("호칭이 로봇이 평소 부르는 방식과 같다(조형민 -> 형민님)",
                "형민님의 대화는" in trust_notice.opening_notice(short_name("조형민"))
                and "형민님과 나눈" in trust_notice.closing_notice(short_name("조형민")))
    ok &= check("이름을 모르면 호칭 없는 문장을 쓴다(어색한 'None님' 방지)",
                "None" not in trust_notice.opening_notice(None)
                and "None" not in trust_notice.closing_notice(None))

    # --- 발화 판정 ---
    ok &= check("정확한 문장은 전달로 판정된다",
                trust_notice.was_delivered(trust_notice.opening_notice("조형민"))
                and trust_notice.was_delivered(trust_notice.closing_notice("조형민")))
    ok &= check("띄어쓰기가 달라도 판정된다",
                trust_notice.was_delivered("오직 모티만  기억하고 있을게요"))
    ok &= check("관련 없는 발화는 전달로 판정되지 않는다",
                not trust_notice.was_delivered("안녕하세요! 오늘 기분이 어떠세요?")
                and not trust_notice.was_delivered("")
                and not trust_notice.was_delivered(None))
    # 안내를 안 했는데 '기억'이라는 단어만 나왔다고 통과하면 안 된다(오탐 = 안 들은 참가자를
    # 들었다고 기록하는 것이라 가장 위험한 실패다).
    ok &= check("비슷한 다른 말은 전달로 오판하지 않는다",
                not trust_notice.was_delivered("제가 기억하고 있을게요")
                and not trust_notice.was_delivered("모티가 다 기억할게요"))

    # --- 시작 안내 흐름 ---
    f = FakeFlow(name=None)
    f.turn("안녕하세요! 저는 모티예요. 이름을 알려주실래요?")
    ok &= check("이름을 모르는 동안에는 안내를 시키지 않는다", f.injected == [])

    f.name = "조형민"   # remember_fact(field="name")로 이름이 확정된 상황
    f.turn("조형민님이시군요! 반가워요.")
    ok &= check("이름을 알게 되면 안내 히든 턴이 주입된다",
                len(f.injected) == 1 and "조형민님의 대화는" in f.injected[0])

    f.turn(trust_notice.opening_notice("조형민"))
    ok &= check("실제로 발화하면 전달로 기록된다", f.state["opening_delivered"])
    f.turn("오늘 어떤 하루였어요?")
    ok &= check("한 번 전달되면 다시 주입하지 않는다", len(f.injected) == 1)

    # 모델이 계속 안 말하는 경우 — 무한 재시도로 대화를 망치지 않아야 한다
    f2 = FakeFlow(name="조형민")
    for _ in range(5):
        f2.turn("네, 그렇군요!")
    ok &= check("모델이 계속 안 말해도 주입은 2회에서 멈춘다", len(f2.injected) == 2)
    ok &= check("끝내 안 했으면 전달 안 됨으로 남는다", not f2.state["opening_delivered"])

    # 퀴즈 진행 중에는 끼어들지 않는다(문제/정답 시퀀스를 Python이 통제하는 구간)
    f3 = FakeFlow(name="조형민", quiz_running=True)
    f3.turn("정답입니다! 다음 문제예요.")
    ok &= check("퀴즈 중에는 안내를 주입하지 않는다", f3.injected == [])

    # --- 마무리 안내 흐름 ---
    f4 = FakeFlow(name="조형민")
    f4.state["opening_delivered"] = True
    end = f4.turn(f"오늘 이야기 즐거웠어요. {trust_notice.closing_notice('조형민')}", should_end=True)
    ok &= check("작별 인사에 안내가 들어있으면 곧바로 종료한다",
                end and f4.state["closing_delivered"] and f4.injected == [])

    f5 = FakeFlow(name="조형민")
    f5.state["opening_delivered"] = True
    end = f5.turn("오늘 이야기 즐거웠어요. 안녕히 가세요!", should_end=True)
    ok &= check("안내가 빠지면 종료를 미루고 한 번 더 시킨다",
                not end and len(f5.injected) == 1 and "모티만 기억" in f5.injected[0])
    # 주입한 턴에는 [대화종료] 태그를 다시 붙이지 말라고 했으므로 should_end=False로 온다 —
    # 이때 평상시 턴으로 취급하면 세션이 영영 안 닫힌다(실제로 빠지기 쉬운 함정).
    end = f5.turn(trust_notice.closing_notice("조형민"), should_end=False)
    ok &= check("재요청 후 발화하면 [대화종료] 태그 없이도 종료된다",
                end and f5.state["closing_delivered"])

    f6 = FakeFlow(name="조형민")
    f6.turn("잘 가요!", should_end=True)
    end = f6.turn("네, 안녕히 가세요!", should_end=False)
    ok &= check("재요청에도 안 하면 더 붙잡지 않고 종료한다",
                end and not f6.state["closing_delivered"] and len(f6.injected) == 1)

    # --- 페르소나 지시문 ---
    block = trust_notice.persona_instruction_block()
    ok &= check("페르소나 지시문에 두 문장이 모두 들어있다",
                "안내사항이 있습니다" in block and "모티만 기억하고 있을게요" in block)

    # 실험 모드에서도 안내 지시가 살아있어야 한다 — 이 멘트는 측정 대상 자체라
    # 토큰 절감(46단계) 대상에서 제외돼야 한다.
    from core.utils import build_persona_system_instruction
    os.environ["QUIZ_EXPERIMENT_MODE"] = "true"
    lean_persona = build_persona_system_instruction("조형민", None)
    ok &= check("실험 모드에서도 안내 지시가 페르소나에 남는다",
                "안내사항이 있습니다" in lean_persona
                and "모티만 기억하고 있을게요" in lean_persona)

    print()
    if ok:
        print("✅ 전부 통과")
    else:
        print("❌ 일부 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
