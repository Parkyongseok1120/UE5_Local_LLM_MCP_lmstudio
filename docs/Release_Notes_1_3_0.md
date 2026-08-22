# 1.3.0 — Direct Model Mode and release hardening

[English](#english) | [한국어](#korean)

## Component versions

| Component | Version |
|---|---|
| Product | 1.3.0 (`v1.3.0`) |
| Portable manifest | 2.1.8 |
| Node agent MCP | 0.3.19 |
| Context compactor | 0.4.47 / revision 94 |

## English

1.3.0 makes Direct Model Mode the supported default. The selected model owns tool choice and sequencing, while the MCP servers provide bounded capabilities and enforce filesystem, process, build, and Unreal-project safety.

### Highlights

- Replaced the supported server-owned task/router/planner/synthesis workflow with direct model-driven MCP capabilities; the legacy evaluation path remains historical and is excluded from the portable package.
- Hardened project, engine, RAG generation, Editor metadata, and provenance handling across Windows, Ubuntu, and Apple Silicon macOS.
- Added scoped file-version receipts and SHA-256 compare-and-swap protection for edits, bundles, and deletes.
- Hardened bounded Build/Automation execution, output decoding, process-tree termination, and Unreal target resolution.
- Preserved the active objective, tool outcomes, file evidence, and remaining work across hard context compaction.

### Release and validation boundary

`portablePackage.releaseReady: true` means publication is gated by the final source, package, installer, safety, and cross-platform automation checks. It is not a universal physical-host certification.

- Apple Silicon has a recorded physical FULL-install pass, with the documented Editor-export, API-connectivity, and signing/notarization limitations.
- A prior native Windows LM Studio GUI session exercised the RAG/MCP tools and reached a real UBT invocation against a local Unreal project. This is runtime workflow evidence, not a clean-machine physical installer-lifecycle test.
- Universal compatibility across hosts, Unreal projects, source/installed engines, plugins, and Editor runtimes is not claimed.
- Qwen 3.8 27B is the primary currently recommended and validated operating model, although no paired 1.3.0 Direct-mode score is claimed. Saved Qwen 3.5 and community fine-tuned Qwen 3.6 27B results remain historical v1.2.5 measurements, and those checkpoints and GPT-OSS are not current recommendations. Muse Glimmer is under testing and is not yet a validated recommendation.

The GitHub release assets carry the package SHA-256 digest. Verify the downloaded ZIP against the digest published with that release.

## Korean

1.3.0은 Direct Model Mode를 기본 지원 경로로 전환합니다. 선택한 모델이 tool 선택과 실행 순서를 소유하고, MCP 서버는 bounded capability와 파일시스템·프로세스·빌드·Unreal 프로젝트 안전 경계를 제공합니다.

### 핵심 변경

- 지원 runtime에서 서버 소유 task/router/planner/synthesis workflow를 제거하고 모델 주도 MCP capability로 전환했습니다. legacy evaluation 경로는 과거 기록으로만 남고 portable package에서 제외됩니다.
- Windows, Ubuntu, Apple Silicon macOS의 project·engine·RAG generation·Editor metadata·provenance 처리를 강화했습니다.
- 수정·bundle·삭제에 scoped file-version receipt와 SHA-256 compare-and-swap 보호를 추가했습니다.
- Build/Automation의 bounded 실행, output decoding, process-tree 종료, Unreal target 해석을 강화했습니다.
- hard context compaction 이후에도 활성 목표, tool 결과, file 근거, 남은 작업이 유지되도록 개선했습니다.

### 릴리스·검증 경계

`portablePackage.releaseReady: true`는 최종 source·package·installer·safety·cross-platform 자동 검증을 통과해야 게시할 수 있다는 뜻입니다. 모든 물리 host에 대한 보편적 인증은 아닙니다.

- Apple Silicon은 physical FULL-install PASS 기록이 있으며 Editor export, API 연결, signing/notarization 제한은 그대로 공개합니다.
- 과거 native Windows LM Studio GUI 세션에서 RAG/MCP tool과 로컬 Unreal 프로젝트에 대한 실제 UBT 호출을 확인했습니다. 이는 runtime workflow 근거이며 clean-machine physical installer lifecycle 검증은 아닙니다.
- 모든 host, Unreal project, source/installed engine, plugin, Editor runtime 조합의 보편적 호환성은 주장하지 않습니다.
- Qwen 3.8 27B가 현재 주 권장·검증 operating model이지만 paired 1.3.0 Direct-mode 점수는 아직 주장하지 않습니다. 저장된 Qwen 3.5와 community fine-tuned Qwen 3.6 27B 결과는 historical v1.2.5 측정이며 해당 checkpoint들과 GPT-OSS는 현재 추천 대상이 아닙니다. Muse Glimmer는 테스트 중이며 아직 검증된 추천이 아닙니다.

GitHub Release asset에는 package SHA-256 digest를 함께 게시합니다. 다운로드한 ZIP은 해당 릴리스에 공개된 digest와 대조하세요.
