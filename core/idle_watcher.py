"""기본 대화 상태(퀴즈 제외)에서 사용자가 오래 조용하면 SLEEPY로 전환하고, 다시 말하기
시작하면 깨우는 판단 로직 — asyncio/session/하드웨어를 몰라야 launcher.py 없이도 유닛
테스트할 수 있다(core/quiz_state.py와 같은 이유로 순수 로직만 분리).

launcher.py의 idle_watcher()가 매 tick마다 decide_idle_action()을 호출해 반환값에 따라
실제 emotion_queue.put(...)/shared_state['mode']=...를 건드린다.
"""

IDLE_SLEEP_SEC = 40.0


def decide_idle_action(idle_for: float, is_sleeping: bool, quiz_active: bool) -> str:
    """(idle_for, is_sleeping, quiz_active) -> "wake" | "sleep" | "none".

    깨우기(wake)는 퀴즈 진행 여부와 무관하게 항상 최우선으로 판정한다 — 자다가 바로
    퀴즈가 시작되는 경우에도(사용자의 첫 마디가 "퀴즈 풀자"인 경우) 팬/틸트 추적과
    표정이 SLEEPY에 갇힌 채로 퀴즈가 진행되는 사고를 막기 위함이다.
    """
    if is_sleeping:
        return "wake" if idle_for < IDLE_SLEEP_SEC else "none"
    if quiz_active:
        return "none"
    return "sleep" if idle_for >= IDLE_SLEEP_SEC else "none"
