# 1.3.2 — Multi-round context continuity and clean-host installation

[English](#english) | [한국어](#korean)

## Component versions

| Component | Version |
|---|---|
| Product | 1.3.2 (`v1.3.2`) |
| Portable manifest | 2.1.14 |
| Node agent MCP | 0.3.20 |
| Evidence-First MCP server | 1.1.0 |
| Context compactor | 0.4.51 / revision 98 |

## English

1.3.2 stabilizes long, multi-round Direct Model Mode work across multiple Unreal Engine versions and projects. File reads, focused edits, receipt/CAS validation, and atomic writes keep their existing MCP server ownership. The release changes when context pressure is rechecked, what may survive serialized continuity, how Evidence packets are repaired, and how clean supported hosts enter the installer.

### Highlights

- Executes one complete model/tool round at a time and rechecks context pressure before the next round. Completed assistant/tool messages are appended exactly once; in-progress tool results are not cut in half.
- Preserves tool-call identity across guard, execution, and finalized events, including denied calls, repeated ID-less calls, and reused model IDs in separate rounds.
- Structurally removes raw `fvr1_` capabilities, mutation receipts, and registry-ordering state from serialized continuity and inherited checkpoints. The active objective and latest user message remain available.
- Retains only canonical project-root and contained canonical-path file observations as non-authoritative facts that require a fresh read before mutation. Same-name clones remain isolated by canonical root, while unproven legacy/workspace observations are not assigned actionable ownership.
- Publishes one declarative Evidence packet contract for the MCP server and standalone validator. Schema and semantic validation now share the same required fields, proof-level rules, and bounded repair feedback, reducing repeated validation retries.
- Keeps the existing scoped SHA-256 CAS, server-side receipt, deletion, and atomic-write boundaries unchanged. No task planner, route owner, `requiredNextTool`, or public snapshot-refresh workflow was added.
- Removes the redundant nested compaction enable setting. LM Studio's single host-owned per-chat plugin switch remains the only activation control, stays OFF by default, and is never enabled by the installer.
- Lets the platform launchers bootstrap a pinned, SHA-256-verified managed Python 3.12 runtime on a clean supported host without installing Python system-wide or modifying the system PATH.

### Live E2E evidence

The release operator completed a live Qwen 3.8 27B workflow against a real Unreal project. The transcript covers active-project discovery, repeated RAG searches, directory/file/symbol reads across a large enemy-AI code surface, a synthesized FSM and architecture report, and a follow-up turn. Early invalid search-mode and model tool-call-generation attempts were recovered in-session. The prior mid-session context break and `Unterminated string in JSON` symptom did not recur.

This is functional E2E evidence supplied by the release operator. It is not a standardized benchmark, a paired model-quality score, or universal certification for every LM Studio, Unreal Engine, project, plugin, and host combination.

### Model guidance

**Qwen 3.8 27B is highly recommended** as the primary validated operating model for the current Direct stack. The live v1.3.2 workflow above is the current qualitative validation basis. **Muse Glimmer is under testing** and is not yet a validated recommendation. Qwen 3.5, community Qwen 3.6 27B checkpoints, and GPT-OSS are not currently recommended.

Historical live-model scores and timing records remain outside the README and are retained only in dedicated measurement-history documents. The v1.3.2 live E2E result is not converted into a benchmark score.

### Release and validation boundary

Release publication is gated by the complete Context Compactor suite, Node MCP suite, Python suites, TypeScript/JavaScript/PowerShell/static checks, clean portable package verification, and cross-platform GitHub CI. A successful gate does not claim physical clean-machine certification on every host or universal Unreal project/engine/plugin/editor-runtime compatibility.

The GitHub release assets include the clean portable ZIP and its SHA-256 digest. Verify the downloaded ZIP against the digest published with the release.

## Korean

1.3.2는 여러 Unreal Engine 버전과 여러 프로젝트에서 긴 multi-round Direct Model Mode 작업을 안정화합니다. 파일 읽기, 작은 범위 수정, receipt/CAS 검증, atomic write는 기존 MCP 서버가 계속 소유합니다. 이번 릴리스는 context pressure를 다시 확인하는 시점, serialized continuity에 남길 수 있는 정보, Evidence packet 복구 방식, 새 지원 host의 installer 진입 경로를 수정합니다.

### 핵심 변경

- 모델/tool 작업을 한 번에 하나의 완결된 round로 실행하고 다음 round 전에 context pressure를 다시 확인합니다. 완료된 assistant/tool message는 정확히 한 번만 추가되며 진행 중인 tool result를 중간에서 자르지 않습니다.
- 거부된 호출, 반복되는 ID 없는 호출, 서로 다른 round에서 재사용된 모델 ID를 포함해 guard·실행·finalized event의 tool-call identity를 보존합니다.
- Serialized continuity와 상속된 checkpoint에서 raw `fvr1_` capability, mutation receipt, registry ordering state를 구조적으로 제거합니다. Active objective와 최신 사용자 메시지는 유지합니다.
- Canonical project root와 그 내부 canonical path가 증명되는 file observation만 fresh read가 필요한 비권한 사실로 유지합니다. 같은 이름의 clone은 canonical root로 분리하고, 증명되지 않은 legacy/workspace observation에는 actionable ownership을 부여하지 않습니다.
- MCP 서버와 standalone validator가 하나의 선언적 Evidence packet contract를 공유합니다. Schema와 semantic validation이 required field, proof-level 규칙, 제한된 repair feedback을 함께 사용해 반복 검증 재시도를 줄입니다.
- 기존 scoped SHA-256 CAS, 서버 측 receipt, 삭제, atomic-write 경계는 변경하지 않았습니다. Task planner, route owner, `requiredNextTool`, public snapshot-refresh workflow를 추가하지 않았습니다.
- 중복된 내부 compaction enable 설정을 제거했습니다. LM Studio가 소유한 채팅별 단일 plugin switch만 activation control로 사용하며 기본 OFF이고 installer는 이를 켜지 않습니다.
- 지원되는 새 host에 Python이 없어도 platform launcher가 pin된 managed Python 3.12 runtime을 내려받아 SHA-256을 검증한 뒤 설치를 계속합니다. Python을 system-wide로 설치하거나 system PATH를 변경하지 않습니다.

### 라이브 E2E 근거

릴리스 운영자가 Qwen 3.8 27B와 실제 Unreal 프로젝트로 라이브 워크플로를 완료했습니다. 기록에는 active-project 탐지, 반복 RAG 검색, 대규모 적 AI 코드 영역의 directory/file/symbol 읽기, FSM·architecture 보고서 합성, 후속 턴 연속성이 포함됩니다. 초반의 잘못된 search mode와 모델 tool-call 생성 시도는 같은 세션에서 자체 복구됐습니다. 이전의 중간 context 단절과 `Unterminated string in JSON` 증상은 재발하지 않았습니다.

이는 릴리스 운영자가 제공한 기능 E2E 근거입니다. 표준화된 benchmark, paired 모델 품질 점수, 모든 LM Studio·Unreal Engine·프로젝트·plugin·host 조합의 보편적 인증은 아닙니다.

### 모델 기준

현재 Direct stack의 주 검증 operating model로 **Qwen 3.8 27B를 매우 추천합니다.** 위 v1.3.2 라이브 워크플로가 현재 정성적 검증 근거입니다. **Muse Glimmer는 테스트 중**이며 아직 검증된 추천이 아닙니다. Qwen 3.5, community Qwen 3.6 27B checkpoint, GPT-OSS는 현재 추천하지 않습니다.

과거 live-model 점수와 실행 시간 기록은 README에 다시 넣지 않고 전용 측정·이력 문서에만 보존합니다. v1.3.2 라이브 E2E 결과도 benchmark 점수로 변환하지 않습니다.

### 릴리스·검증 경계

릴리스 게시는 Context Compactor 전체 suite, Node MCP suite, Python suite, TypeScript/JavaScript/PowerShell/static 검사, clean portable package 검증, cross-platform GitHub CI 통과를 요구합니다. Gate 성공은 모든 물리 clean-machine host나 모든 Unreal project/engine/plugin/editor-runtime 조합의 보편적 호환성을 뜻하지 않습니다.

GitHub Release asset에는 clean portable ZIP과 SHA-256 digest를 함께 게시합니다. 다운로드한 ZIP은 릴리스에 공개된 digest와 대조하세요.
