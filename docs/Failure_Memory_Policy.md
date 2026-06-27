# Failure Memory Policy

Failure memory is **hint-only RAG**, never engine evidence.

## Schema (v2)

Extended fields in `data/failure_memory/{Project}_failures.jsonl`:

- `error_signature`, `original_request`, `failed_output_summary`
- `bad_chunk_ids`, `good_chunk_ids`, `missing_evidence`
- `final_explanation`, `retry_count`, `model`, `sampling_profile`
- `status`: `accepted` | `rejected`

## Weight

Default RAG weight: **0.15** (`failure_memory_rag_weight()`).

Query expansion: `failure_memory_rerank.expand_query_with_memory()` appends low-trust hints.

## Reject bad fixes

```powershell
.\rag.ps1 reject-failure-memory -ProjectName MyGame -Question <record_id>
```

Rejected records are excluded from ingest and rerank.

## Collect + index

```powershell
.\rag.ps1 collect-failure-memory
.\rag.ps1 build-incremental
```

## Rules

- Scoped by project name in metadata
- Never outrank `unreal_source` / engine chunks
- Rejected fixes must not pollute retrieval
