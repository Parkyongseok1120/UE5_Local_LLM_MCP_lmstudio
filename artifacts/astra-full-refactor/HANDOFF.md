# ASTRA full refactor handoff

> **2026-09-25 후속 감사/개선:** 아래 본문은 `fc5373b0`에 포함된 당시의 기록이다. 현재 패치는 `fc5373b0b62c4e07829cea5052f1e1afbacc3104`에서 시작했으며, 결과는 [후속 HANDOFF](../fc5373-audit-fixes/HANDOFF.md)에 있다. 플러그인 전체 회귀 345개가 통과했으나 현재 로드된 모델이 없어 새 실제 38K/누적380K 검증은 BLOCKED다. 기존 96-token 출력 제한 stress의 실패 수치를 제품 컨텍스트 한계로 해석하지 않는다. 기존 synthetic live/ratchet PASS를 native provider 장기 보존 증명으로 확대하지 않으며 323개와 그 안의 집중16개를 중복 합산하지 않는다. 원래 로그는 역사 자료로 보존한다.

## Disposition

**Overall: FAIL for the complete requested objective.** Architecture and regression work passed locally, but the requested actual-runtime 380K cumulative post-compaction usage condition was not met. The stress probe stopped at the first post-compaction truncated model response and no product code was changed in response.

Start SHA: 3c4375fd3041da16b4e6af721c17fa2c230d2f50, commit title 리팩토링 준비. Final HEAD SHA is still 3c4375fd3041da16b4e6af721c17fa2c230d2f50 because no commit was created. Working-tree patch SHA-256 is 13594a93adad7d858155e5f2cf3a657824b6d6fd221220491d524f07876a54ab. The hash covers git diff --binary plus sorted untracked plugin files; artifacts are excluded. Exact inventory is in patch-manifest.json.

PredictionLoop is 80,622 UTF-8/LF bytes, down from its starting 187,252-byte Git blob. It now coordinates independent execution phase/transaction, ContextManager, BudgetBroker, EvidenceManager, ToolCapabilityRegistry/ToolBoundary, RecoveryCoordinator and DeliveryController owners. Old-to-new mapping and modules are in RESPONSIBILITY_MAP.md; final structure is in ARCHITECTURE_AFTER.md.

## Watermarks and measurements

The runtime success metric is exact_templated_model_input from the loaded LM Studio model template and token counter. It is distinct from backend prompt-eval/prefill and backend slot n_tokens/cache state. The historical 26K/34K/42K and 35K/43K/51K observations lack associated counter fields in supplied logs, so they remain unidentified; they were not hardcoded.

With C=38,912, G=8,192, S=2,048 and no read tools in the 380K probe, hard ceiling=max(0,C-G-S)=28,672, R=0, P=0, HIGH=min(22,000,28,672)=22,000, configured LOW=min(18,000,floor(22,000*0.75))=16,500. Effective LOW=max(16,500,mandatory floor). The post-compaction candidates were 15,300 and 16,359, each <=16,500. The first actual compaction-to-model call used 15,300 exact input tokens and then returned maxPredictedTokensReached with no final user-facing answer. The stress run stopped; 380K cumulative input was not reached. Only 15,300 tokens after the first compaction were processed before the first failure. The full probe total of 43,614 also includes 11,955 seed tokens sent before the first compaction and a 16,359-token diagnostic request after the first failure; those are excluded from post-compaction success. A later diagnostic 16,359-token request is excluded from accepted stress progress.

Independent earlier ratchet verification, using its explicit G=1,024 / target=6,000 / trigger=9,000 harness, measured 4,737 → 4,733 → 4,733 exact input tokens across three cycles, with drift 4 and retained SENTINEL-731. Its 30-cycle synthetic equivalent-evidence fixture settled at 5,670 (drift 5). These are bounded checks, not a pass for the 380K cumulative goal. Full calculations and raw measurements are in RATCHET_REPORT.md.

## Acceptance criteria

| ID | Result | Evidence |
|---|---|---|
| ARCH-01 | PASS | PredictionLoop 80,622 bytes; policy owners and 323-test suite |
| ARCH-02 | PASS | ExecutionState legal and illegal transition matrix |
| ARCH-03 | PASS | ToolCapabilityRegistry authority and collision/spoof tests |
| ARCH-04 | PASS | Typed EvidenceManager identity/version/representation/range/content/availability contracts |
| ARCH-05 | PASS | BudgetBroker is the shared watermark and reservation owner |
| ARCH-06 | PASS | RecoveryCoordinator and DeliveryController have independent policies/tests |
| RAT-01 | PASS | ContextManager final exact input LOW postcondition |
| RAT-02 | PASS | Mandatory floor test preserves required request and reports unreachable target |
| RAT-03 | PASS | Projected pressure and pre-dispatch compaction tests |
| RAT-04 | PASS | Shared batch reservation overbook test |
| RAT-05 | PASS | Three live exact-template ratchet cycles have 4-token drift; 30 synthetic cycles drift 5 |
| RAT-06 | PASS | Exact input, backend prompt eval and slot n_tokens are kept distinct |
| TOOL-01 | PASS | Guard allow is independent from actual dispatch |
| TOOL-02 | PASS | Unknown provider execution remains unknown without provider-start signal |
| TOOL-03 | PASS | Result receipt does not invent provider start |
| TOOL-04 | PASS | Raw tool text is not executed |
| TOOL-05 | PASS | Mutation approval/scope tests remain enforced |
| EVD-01 | PASS | Six-source shared lifecycle tests and Git/file runtime handlers |
| EVD-02 | PASS | First-consumer evidence body is retained before projection |
| EVD-03 | PASS | Body/envelope/source ranges use distinct identities |
| EVD-04 | PASS | Empty EOF creates zero coverage |
| EVD-05 | PASS | Historical novelty is not reported as current availability |
| EVD-06 | PASS | Projection/rehydration does not become new source coverage |
| TX-01 | PASS | Canceled/truncated/failed planning does not commit as completed planning |
| TX-02 | PASS | Already-returned provider evidence survives incomplete planning |
| TX-03 | PASS | Denied/failed/canceled reservation releases at most once |
| SDK-01 | PASS | runtime-manifest.json records runtime/model versions and loaded context |
| SDK-02 | PASS | Channel 58 warning correlated with callbacks/results completing in tested flows |
| SDK-03 | BLOCKED | Installed SDK does not expose host-converted/model-normalized schema |
| ASTRA-380K | FAIL | Actual run stopped after 15,300 post-compaction input tokens on truncated response |

The 380K stress harness capped direct generation at 96 tokens and did not include the production delivery controller. The observation is an actual response truncation in this harness; root cause in the complete product workflow is not isolated. It is not counted as proof of a compaction or evidence-archive defect, and it is not counted as success. The harness predicate also accepted marker text from the internal reasoning segment; this was discovered after the call. The raw records are retained; the post-failure diagnostic round is excluded.

## Commands and exit codes

Gate runner: powershell -NoProfile -File artifacts/astra-full-refactor/run-gate.ps1 gate-0 through gate-8. Each gate build, npm test and focused policy test exited 0. Gate 8 plugin npm test reported 323 passed, 0 failed; focused policy test reported 16 passed, 0 failed. Unity npm test: 34 passed, 4 skipped, exit 0. Unreal npm test: 247 passed, 1 platform skip, exit 0. Final npm run build exited 0; repeated build source/dist fingerprints matched; git diff --check exited 0.

Runtime commands: node scripts/astra-ratchet.cjs (exit 0, 30 fixture cycles); node scripts/astra-ratchet.cjs --live (exit 0, three exact-template cycles); node scripts/astra-live-flow.cjs --git (exit 0); node scripts/astra-live-flow.cjs (exit 0). The additional node artifacts/astra-full-refactor/stress-to-380k.cjs was stopped with exit 1 and status FAIL_STOPPED_ON_TRUNCATED_RESPONSE. Full commands/statuses are in test-results.json. Unity/Unreal skips and runtime limitations are documented in REGRESSION_REPORT.md.

## Runtime risks and rollback

- Requested 380K post-compaction cumulative stress success remains unmet.
- SDK-03 host schema comparison is unavailable through this SDK surface.
- Real handler batch testing observed a safe shared-budget denial before provider dispatch, followed by successful recovery/replan and all expected facts. This was recorded, not altered.
- LM Studio emitted unknown-channel channel 58 warnings during completed handler runs; no callback/result loss was observed.
- Live long-flow providers were synthetic read-only fixtures. Installed GUI plugin and live editor state were not exercised.
- A provider whose schema exposes no bounded result parameter cannot be guaranteed a maximum result size before the provider returns it.

Rollback point is the untouched starting commit 3c4375fd3041da16b4e6af721c17fa2c230d2f50. No commit, archive migration or destructive reset occurred. Revert only paths in patch-manifest.json to that SHA to discard the working-tree patch; keep artifacts during review. The initial first failed stress launch and the exact model measurement/failure are preserved in stress-to-380k-setup-issue.json, stress-to-380k.json and stress-to-380k.log.
