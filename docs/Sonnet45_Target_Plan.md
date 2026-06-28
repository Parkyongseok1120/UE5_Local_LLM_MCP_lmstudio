# Sonnet 4.5-Oriented Target Plan

The project target is upgraded from a Sonnet 4-oriented workflow to a Sonnet 4.5-oriented workflow.

This is a system target, not a current performance claim.

## Target Scope

The target applies to:

- UE C++ compile-fix
- Unreal project review
- Build.cs/module dependency fixes
- UHT/reflection fixes
- Blueprint/Material/Animation metadata-aware planning
- UBT/Editor validation loops

It does not mean a local model is globally Sonnet 4.5-grade.

## Model Tracks

| Track | Profile | Strategy |
|-------|---------|----------|
| Main local track | `qwen3_6_27b` | broader retrieval, 5-attempt compile loop, strict patch discipline |
| Compact Qwen 9B track | `qwen3_5_9b` | strict compact context, top_k 5, two-file cap, 4-attempt compile loop |
| Community Qwen 9B flash track | `qwen3_5_9b_deepseek_v4_flash` | flash reasoning-style compact patch loop, top_k 6 |
| Compact 20B track | `gpt_oss_20b` | strict JSON, top_k 7, two-file cap, 4-attempt compile loop |
| Community GPT OSS 20B reasoning track | `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | low-temp strict patch/verify loop, top_k 8, 5-attempt compile loop |
| Compact below-20B track | `gpt_oss_small` | top_k 4, one-file cap, minimal retry context |
| Small Qwen track | `qwen3_8b` | top_k 5, two-file cap, no broad refactor modes |

## Optimization Priorities

1. Reduce first-shot errors: report Pass@1 separately from Pass@K.
2. Improve retry quality: use failure-specific RAG context, not the whole original context.
3. Prevent no-op edits: reject identical files and repeated patches.
4. Improve asset awareness: use Blueprint, Material, SkeletalMesh, AnimBP, Montage, Notify, and Sequencer metadata before planning asset work.
5. Keep evaluation honest: separate fixture results from 20 unseen real-project cases.

## Required Evidence Before Strong Claims

Before saying the system reached the target:

- run the internal Tier/KPI suite;
- run 20 unseen real-project errors;
- report Pass@1, Pass@3, final pass, and failure categories;
- verify no eval fixture or answer-like material leaked into the live RAG index;
- confirm UBT or Editor validation for claimed fixes.

## Safe Public Wording

Use:

> This project is moving toward a Sonnet 4.5-oriented local Unreal workflow. Current scores are internal RAG/MCP/UBT metrics and require unseen real-project validation before stronger claims.

Avoid:

> This local model is Sonnet 4.5-grade.
