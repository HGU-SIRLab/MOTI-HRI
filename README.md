# moti (v3)

한동대 공감서비스로봇 모티 — v1([hlri-iua-motirobotics](https://github.com/HandongSF/hlri-iua-motirobotics))의 모션/제스처 자산과 v2([Empathy-service-motirobot](https://github.com/HGU-SIRLab/Empathy-service-motirobot))의 대화 설계를 통합한 3번째 버전.

설계 배경과 아키텍처 전체는 [`docs/architecture.md`](docs/architecture.md) 참고.

## 구조

```
core/       페르소나 시스템 인스트럭션, 메모리(remember_fact), 리포트 생성
hardware/   모터 I/O, 설정 상수, Layer 1/2 모션(매크로 + 파라미터 제스처)
vision/     얼굴 추적, art_brain(FuzzyART 얼굴 인식)
display/    표정 UI(pygame), 자막
media/      오디오 입출력
docs/       설계 문서
```

## 상태

2026-07-27 실물 로봇 연결 후 핵심 기능 전부 완성 — 얼굴인식(처음 보는 사람은 이름을 물어보고 자동 등록), 팬/틸트 추적, Layer 1/2 제스처, 로봇이 먼저 인사하는 대화 시작, 에코캔슬레이션(AEC, 이어폰 없이 실제 대화로 최종 검증)까지 전부 실제 로봇으로 검증됨. 진입점은 `launcher.py`(`python launcher.py`). 남은 건 우선순위 낮은 항목뿐(`docs/integration-points.md` 참고) — 로드맵은 `docs/architecture.md` §10.
