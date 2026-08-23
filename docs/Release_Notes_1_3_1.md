# 1.3.1 — Receipt-safe continuity and release closure

[English](#english) | [한국어](#korean)

## Component versions

| Component | Version |
|---|---|
| Product | 1.3.1 (`v1.3.1`) |
| Portable manifest | 2.1.11 |
| Node agent MCP | 0.3.20 |
| Context compactor | 0.4.50 / revision 97 |

## English

1.3.1 is a stabilization release for the Direct Model Mode stack across multiple Unreal Engine versions and projects. It keeps the existing MCP authority boundaries while tightening receipt-chained file mutations, durable context continuity, installation defaults, packaging, and cross-platform CI.

### Highlights

- Bounds model-facing file edits to focused receipt-chained regions while preserving the existing scoped SHA-256 CAS, two-file atomic transaction, deletion, and server-side receipt owners.
- Removes raw runtime-local `fvr1_` file capabilities and snapshot counters from serialized durable continuity, including inherited checkpoints and repeated hard compactions.
- Preserves user-authored payment-receipt, code-symbol, and domain language; only assistant/tool-derived executable file-receipt reuse guidance is neutralized.
- Retains only canonical project-root and contained canonical-path file observations as non-authoritative facts that require a fresh read before mutation. Same-name clones remain isolated by canonical root.
- Keeps the optional context-compactor chat plugin host-controlled and OFF by default. The installer does not activate it, and its internal transparent-compaction opt-in also defaults OFF.
- Consolidates Python test membership under one validated CI suite owner, keeps the repetition gate explicit, and validates the Direct MCP, Context Compactor, static checks, PowerShell, and clean portable package across the supported CI hosts.

### Model guidance

**Qwen 3.8 27B** is the primary currently recommended and validated operating model. **Muse Glimmer is under testing** and is not yet a validated recommendation. Qwen 3.5, community Qwen 3.6 27B checkpoints, and GPT-OSS are not currently recommended.

Historical live-model measurements are not a v1.3.1 score and are intentionally omitted from the README. They remain available in the dedicated measurement-history documents only.

### Release and validation boundary

`portablePackage.releaseReady: true` means publication is gated by the source, package, installer, safety, and cross-platform automation checks. It is not universal certification for every physical host, Unreal project, source or installed engine, plugin, or Editor runtime.

The GitHub release assets include the clean portable ZIP and its SHA-256 digest. Verify the downloaded ZIP against the digest published with the release.

## Korean

1.3.1은 여러 Unreal Engine 버전과 여러 프로젝트에서 사용하는 Direct Model Mode stack의 안정화 릴리스입니다. 기존 MCP authority 경계는 유지하면서 receipt 연쇄 파일 수정, durable context continuity, 설치 기본값, 패키징, cross-platform CI를 강화했습니다.

### 핵심 변경

- 모델이 수행하는 파일 수정을 작은 receipt 연쇄 구간으로 제한하면서 기존 scoped SHA-256 CAS, 2파일 atomic transaction, 삭제, 서버 측 receipt owner를 유지합니다.
- 상속된 checkpoint와 반복 hard compaction을 포함해 serialized durable continuity에서 runtime-local raw `fvr1_` 파일 capability와 snapshot counter를 제거합니다.
- 사용자가 작성한 결제 영수증, 코드 심볼, domain 언어는 보존합니다. assistant/tool이 만든 실행 가능한 파일 receipt 재사용 지시만 중립화합니다.
- canonical project root와 그 내부 canonical path가 증명되는 file observation만 fresh read가 필요한 비권한 사실로 유지합니다. 이름이 같은 clone은 canonical root로 분리됩니다.
- 선택적 context-compactor chat plugin은 LM Studio host가 관리하며 기본 OFF입니다. Installer는 이를 활성화하지 않고 내부 transparent-compaction opt-in도 기본 OFF입니다.
- Python 테스트 membership을 검증된 단일 CI suite owner로 통합하고 repetition gate를 명시적으로 유지합니다. 지원 CI host에서 Direct MCP, Context Compactor, 정적 검사, PowerShell, clean portable package를 검증합니다.

### 모델 기준

현재 주 권장·검증 operating model은 **Qwen 3.8 27B**입니다. **Muse Glimmer는 테스트 중**이며 아직 검증된 추천이 아닙니다. Qwen 3.5, community Qwen 3.6 27B checkpoint, GPT-OSS는 현재 추천하지 않습니다.

과거 live-model 측정값은 v1.3.1 점수가 아니며 README에서 제거했습니다. 과거 측정 근거는 전용 측정·이력 문서에만 보존합니다.

### 릴리스·검증 경계

`portablePackage.releaseReady: true`는 source, package, installer, safety, cross-platform 자동 검증을 통과해야 게시할 수 있다는 뜻입니다. 모든 물리 host, Unreal project, source 또는 installed engine, plugin, Editor runtime 조합에 대한 보편적 인증은 아닙니다.

GitHub Release asset에는 clean portable ZIP과 SHA-256 digest를 함께 게시합니다. 다운로드한 ZIP은 릴리스에 공개된 digest와 대조하세요.
