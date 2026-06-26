---
title: Combat Or Primary Verb
genre: action_combat
design_area: combat
---

# Combat Or Primary Verb

## Core Verb

플레이어가 가장 자주 누르고 판단하는 행동을 적는다.

## Input And Timing

- 입력:
- 선딜:
- 활성 구간:
- 후딜:
- 취소 가능 여부:

## Target Reaction

- 피격 반응:
- 경직/스태거:
- 사망/실패:
- 피드백:

## Responsibility Draft

| System | Owns | Must Not Own |
|---|---|---|
| Player Character | movement and camera-facing control | final damage application |
| Verb/Combat Component | request, timing, combo/process state | target health mutation |
| Trace/Hit Component | hit detection and duplicate-hit record | final combat result |
| Target State Component | health/stagger state and events | player input |
| Feedback Component | VFX/SFX/hit stop/camera shake requests | combat rule judgment |

## First Test

작은 맵에서 입력, 판정, 대상 반응, 피드백이 30초 안에 반복되는지 확인한다.
