# 1.3.0 RC2 Release Notes

[English](#english) | [한국어](#korean)

## English

**Status:** Release Candidate  
**Product label:** `1.3.0 RC2`  
**SemVer/tag form:** `1.3.0-rc.2` / `v1.3.0-rc.2`

RC2 is a startup/install hotfix on top of RC1. It does not rewrite or move the existing `v1.3.0-rc.1` tag. The focus is keeping Stable Essential MCP catalogs visible on first launch and fixing macOS Python launcher detection.

### Highlights

- Keep Stable Essential `tools/list` catalogs advertised across clean, active, expired, corrupt, and multi-route task states; execution stays fail-closed at CallTool authorization.
- Stop treating blocked/corrupt routes as a partial MCP install in LM Studio Integrations (no recovery-only catalog collapse).
- Emit stderr-only `mcp_catalog_initialized` diagnostics and expose `toolCatalog` fields from `get_workspace_info` / `unreal_project_status`.
- Expand `install.sh` to probe `$PYTHON`, `python3.14` … `python3.10`, `python3`, and `python`, selecting the first usable 3.10+ interpreter on POSIX hosts.

### Compatibility and component versions

| Component | Version |
|---|---|
| Product | 1.3.0 RC2 |
| Node agent MCP | 0.3.4 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.3 |

Python 3.10+, Node.js 20+, and LM Studio 0.4+ remain required according to the selected installer profile.

### Evidence boundary

- Live-model scores remain the historical v1.2.5 baselines; RC2 does not claim a new paired model-quality uplift.
- Catalog/authorization and installer launcher changes are covered by automated tests.
- Physical Ubuntu/macOS Unreal certification remains pending.

### Known RC limitations

- Same RC1 limitations remain: conservative architecture graphs, opt-in AGENT authority, and pending Ollama/frontend-parity/physical host certification.

## Korean

**상태:** Release Candidate  
**제품 표기:** `1.3.0 RC2`  
**SemVer/tag 표기:** `1.3.0-rc.2` / `v1.3.0-rc.2`

RC2는 RC1 위의 startup/install hotfix입니다. 기존 `v1.3.0-rc.1` 태그는 재작성하거나 옮기지 않습니다. 목표는 최초 실행에서도 Stable Essential MCP catalog가 보이고, macOS Python launcher 탐지를 고치는 것입니다.

### 주요 변경

- clean/active/expired/corrupt/multi-route 상태에서도 Stable Essential `tools/list` catalog를 유지하고, 실행은 CallTool authorization에서 fail-closed로 차단합니다.
- blocked/corrupt route를 LM Studio Integrations에서 부분 설치처럼 보이게 만드는 recovery-only catalog 축소를 제거합니다.
- stderr 전용 `mcp_catalog_initialized` 진단과 `get_workspace_info` / `unreal_project_status`의 `toolCatalog` 필드를 추가합니다.
- `install.sh`가 `$PYTHON`, `python3.14` … `python3.10`, `python3`, `python`을 검사해 첫 번째 사용 가능한 3.10+ interpreter를 선택합니다.

### 호환성과 컴포넌트 버전

| 컴포넌트 | 버전 |
|---|---|
| 제품 | 1.3.0 RC2 |
| Node agent MCP | 0.3.4 |
| Context compactor | 0.3.5 / revision 8 |
| Portable manifest | 2.1.3 |

### 근거 범위

- live-model 점수는 여전히 historical v1.2.5 baseline입니다.
- catalog/authorization과 installer launcher 변경은 자동 테스트로 검증합니다.
- 실제 Ubuntu/macOS Unreal 인증은 아직 남아 있습니다.
