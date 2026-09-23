# Ratchet and cumulative context measurement

## Metric

The final-input metric is exact_templated_model_input: applyPromptTemplate(history, toolDefinitions) followed by the loaded model's countTokens(fullPrompt). This is the full assembled candidate and the compaction success metric. Backend prompt-eval tokens count prompt work performed in an inference; slot n_tokens records backend sequence occupancy/history state; prefix cache reuse can change prefill work. These are separate fields. Historical 26K/34K/42K and 35K/43K/51K observations have no associated raw metric fields in supplied logs, so their counter remains unknown.

## Watermarks and independent 3-cycle check

For the 38,912 context and the product defaults used by the stress probe: C=38,912, generation reserve G=8,192, safety S=2,048, hard ceiling=28,672. There were no model-facing read tools in that probe, so R=0 and P=0; HIGH=22,000 and LOW=min(18,000, floor(22,000×0.75))=16,500. Compaction must end at or below LOW and the hard ceiling.

The earlier independent live ratchet probe used an explicit 6,000/9,000 target/trigger and G=1,024 test fixture. It measured final exact inputs 4,737 → 4,733 → 4,733, drift 4; the sentinel was present in each completion. The 30-cycle synthetic fixture settled from 5,675 to 5,670 (drift 5). These results are separate bounded checks and do not satisfy the later requested 380K cumulative usage stress by themselves.

## 380K cumulative stress attempt

The user clarified 380K means the sum of context input used after compaction across successive model calls, with active model context remaining 38,912. The measurement used the loaded model, product-default target/trigger/reserve/safety, exact template counts and actual model.respond calls. It did not increase the model context. It stopped after the first completed compaction followed by a truncated model response; no product code was adjusted.

| Phase | Compacted before call | Exact full input | Backend prompt eval | Compaction result | Actual response |
|---|---:|---:|---:|---|---|
| Seed/setup cycle (before first compaction) | No; 11,955 was already below LOW | 11,955 | 11,955 | unchanged | maxPredictedTokensReached; reasoning segment only |
| First call after actual compaction | Yes | 15,300 | 15,300 | complete_exchange_cap_4; result <=16,500 LOW | maxPredictedTokensReached; reasoning segment only; no user-facing final answer |
| Later diagnostic, excluded after failure | Yes | 16,359 | 16,359 | complete_exchange_cap_4; result <=16,500 LOW | same truncation; not counted as an accepted stress cycle |

The cumulative post-compaction input processed up to the first failed response was 15,300 exact tokens, far short of 380,000. The file's 43,614-token total also includes 11,955 seed tokens sent before the first compaction and a 16,359-token diagnostic request after the first failure; those two values are excluded from accepted post-compaction progress. Although fixture facts remained in assembled history, the model did not deliver them in a completed final answer, so that response cannot count as preserved data delivery. The initial raw-string predicate incorrectly accepted hidden reasoning text; the limitation and unedited traces remain in stress-to-380k.json and stress-to-380k-setup-issue.json. The third cycle was collected after the first failure was visible and is excluded. Overall 380K result: FAIL / not demonstrated.

An earlier actual-handler flow with synthetic file and Git read tools completed all three requested facts. In the Git/file batch a shared result budget denied one not-yet-dispatched request, after which the model recovered and the provider returned each fact once. This non-terminal pressure/replan is recorded in live-git-flow.json and live-non-git-flow.json; no data loss was observed in those flows.

Logs keep exact inputs and per-call backend prompt counts separate for later investigation.
