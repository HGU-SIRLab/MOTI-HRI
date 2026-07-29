# emotions/wake.py — v1(capston_mk1/motirobotics)에서 이식. SLEEPY 상태에서 깨어날 때
# 잠깐(약 2.5초) 재생되는 눈뜨는 애니메이션. launcher.py의 idle_watcher가 "AWAKENING" 키로
# 사용하고, 애니메이션이 끝나면 직접 "NEUTRAL"로 되돌린다(display/main.py의 30초 자동
# 복귀 타이머보다 훨씬 짧으므로 그 타이머가 끼어들 일은 거의 없음).

import pygame
import math
from . import neutral
from ..common_helpers import WHITE


class Emotion:
    def __init__(self):
        self.is_animating = True
        self.start_time = 0
        self.duration_phase1 = 750
        self.duration_pause = 1000
        self.duration_phase2 = 750
        self.total_duration = self.duration_phase1 + self.duration_pause + self.duration_phase2

    def reset(self):
        self.is_animating = True
        self.start_time = pygame.time.get_ticks()

    def draw(self, surface, common_data):
        time = common_data['time']

        neutral.Emotion().draw(surface, common_data)

        if self.is_animating:
            elapsed = time - self.start_time

            if elapsed < self.duration_phase1:
                progress = elapsed / self.duration_phase1 / 2
            elif elapsed < self.duration_phase1 + self.duration_pause:
                progress = 0.5
            else:
                phase2_elapsed = elapsed - (self.duration_phase1 + self.duration_pause)
                progress = 0.5 + (phase2_elapsed / self.duration_phase2) / 2

            progress = min(progress, 1.0)
            lid_height = 100 * (1 - progress)

            for eye_center in [common_data['left_eye'], common_data['right_eye']]:
                top_lid_rect = (eye_center[0] - 100, eye_center[1] - 100, 200, lid_height + 10)
                pygame.draw.rect(surface, (0, 0, 0), top_lid_rect)

                bottom_lid_rect = (eye_center[0] - 100, eye_center[1] + 100 - lid_height, 200, lid_height + 10)
                pygame.draw.rect(surface, (0, 0, 0), bottom_lid_rect)

            if elapsed >= self.total_duration:
                self.is_animating = False

        pygame.draw.arc(surface, WHITE, (surface.get_width() // 2 - 40, surface.get_height() // 2 + 120, 80, 40), math.pi, 2 * math.pi, 5)
