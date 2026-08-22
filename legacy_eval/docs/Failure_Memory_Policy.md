# Historical Failure Memory Policy

> Archived evaluation-era policy. The Direct runtime does not expose this lifecycle.

Failure memory is low-trust, project-scoped RAG guidance. It is never Unreal
source, build, or runtime evidence and has a maximum rerank weight of `0.15`.

## Fail-closed lifecycle

Every new observation is stored as `candidate`, even if a caller requests a
trusted status. The only trust path is:

`candidate -> verified -> verified -> accepted`

- `verified` requires a successful build or runtime proof with a non-empty,
  unique `artifactHash`.
- Proof `engineVersion` and `projectFingerprint` must exactly match the record.
- `accepted` requires at least two distinct valid verification events. Callers
  cannot lower that minimum or forge it through `verificationCount`.
- `rejected` and `expired` are terminal. Accepted records may still be rejected
  or expire.
- Lifecycle changes append a new JSONL row; the latest valid row for an ID is
  authoritative and retains `lifecycleHistory` and `verificationHistory`.

Records with malformed status, proof, scope, expiry, or metadata fail closed.
Only non-expired `verified` and `accepted` rows can influence reranking.

## Record fields

`data/failure_memory/{Project}_failures.jsonl` includes:

- Failure identity: `id`, `error_signature`, `error_subkind`, `error_code`,
  `symbol_name`
- Context: `original_request`, `failed_output_summary`, `missing_evidence`
- Outcome: `fix_summary`, `final_explanation`, `changed_files`, `diff_excerpt`
- Retrieval lineage: `rag_evidence_ids`, `bad_chunk_ids`, `good_chunk_ids`
- Reproduction: `model`, `sampling_profile`, `retry_count`
- Trust binding: `status`, `engineVersion`, `projectFingerprint`,
  `verificationCount`, `verificationEvidence`, `verificationHistory`,
  `expiresAt`

## Operations

```powershell
.\rag.ps1 collect-failure-memory
.\rag.ps1 build-incremental
.\rag.ps1 reject-failure-memory -ProjectName MyGame -Question <record_id>
```

Rejected or expired records are excluded from collection and reranking.
Failure-memory hints must never outrank `unreal_source` or engine evidence.
