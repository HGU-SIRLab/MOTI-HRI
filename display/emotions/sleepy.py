# emotions/sleepy.py — v1(capston_mk1/motirobotics)에서 이식, 침/Zzz 애니메이션 포함.
# 15초 무응답 idle-sleep 기능(launcher.py의 idle_watcher)에서 사용.

import pygame
import math
import random
from ..common_helpers import *

MOUTH_FILL_COLOR = (255, 255, 255, 150)


class Emotion:
    def __init__(self):
        self.z_particles = []
        self.next_z_time = 0
        try:
            self.z_font = pygame.font.SysFont('Arial', 40, bold=True)
        except Exception:
            self.z_font = pygame.font.Font(None, 50)

    def draw(self, surface, common_data):
        left_eye, right_eye, offset, time = common_data['left_eye'], common_data['right_eye'], common_data['offset'], common_data['time']

        breathing_offset = math.sin(time * 0.0008) * 4
        body_offset = (0, breathing_offset)

        if time > self.next_z_time:
            eye_top_x = surface.get_width() // 2 + 100
            eye_top_y = surface.get_height() // 2 - 80
            self.z_particles.append({
                'pos': [eye_top_x, eye_top_y + body_offset[1]],
                'size': random.randint(30, 50),
                'alpha': 255,
                'x_drift': random.uniform(0.1, 0.4),
                'char': "z"
            })
            self.next_z_time = time + random.randint(1500, 2500)

        for p in self.z_particles[:]:
            p['pos'][1] -= 0.7
            p['pos'][0] += p['x_drift']
            p['alpha'] -= 1.2
            if p['alpha'] <= 0:
                self.z_particles.remove(p)
            else:
                z_text = self.z_font.render(p['char'], True, WHITE)
                z_text = pygame.transform.scale(z_text, (p['size'], p['size']))
                z_text.set_alpha(p['alpha'])
                surface.blit(z_text, p['pos'])

        mouth_center_x = surface.get_width() // 2
        mouth_y = surface.get_height() // 2 + 135 + body_offset[1]

        mouth_rect = pygame.Rect(mouth_center_x - 50, mouth_y, 100, 70)
        mouth_surface = pygame.Surface(mouth_rect.size, pygame.SRCALPHA)
        pygame.draw.ellipse(mouth_surface, MOUTH_FILL_COLOR, (0, 0, mouth_rect.width, mouth_rect.height))
        pygame.draw.rect(mouth_surface, (0, 0, 0, 0), (0, 0, mouth_rect.width, mouth_rect.height / 2))
        surface.blit(mouth_surface, mouth_rect.topleft)

        drool_start_x = mouth_rect.right - 25
        drool_start_y = mouth_rect.bottom - 20
        drool_length = 30
        pygame.draw.circle(surface, SKY_BLUE_START, (drool_start_x, drool_start_y + drool_length), 10)
        pygame.draw.line(surface, SKY_BLUE_START, (drool_start_x, drool_start_y), (drool_start_x, drool_start_y + drool_length), 5)

        for i in [-1, 1]:
            eye_center = (left_eye if i == -1 else right_eye)
            eye_x = eye_center[0]
            eye_y = eye_center[1] + body_offset[1]
            arc_width = 150
            arc_height = 100
            arc_rect = pygame.Rect(eye_x - arc_width // 2, eye_y - arc_height // 2 + 10, arc_width, arc_height)
            pygame.draw.arc(surface, WHITE, arc_rect, math.pi, 2 * math.pi, 15)
