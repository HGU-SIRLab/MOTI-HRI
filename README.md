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

스캐폴딩 단계. 로드맵은 `docs/architecture.md` §10 참고.
