# Unity v1.4.0 beta 1: design and implementation record

## Repository audit (2026-09-15)

- Branch `v1.4.0-alpha-1`, base `901a2e3` (`v1.3.3-4-g901a2e3`). README product version is 1.3.3; the Unreal npm package is 0.3.22. No AGENTS.md was found in the repository or ancestor directories.
- `lmstudio-unreal-agent-mcp/src/direct-server.js` composes explicit capability handlers and the SDK stdio transport. `direct-tool-catalog.js` is the tool schema catalog. The default does not own a planner; `strict-server.js` is separate.
- `scripts/unreal_rag_direct.py` is the independent Python retrieval entry point. Unity does not reuse its Unreal project binding or expose the Unreal catalog.
- `direct-read-capabilities.js`, `direct-file-snapshot.js`, `direct-file-version-policy.js` and `file-snapshot-registry.js` implement bounded reads and explicit version evidence. `write-guards.js` assumes Unreal directories, so it cannot authorize Unity paths.
- `write-locks.js` implements canonical-path cooperative process locks, PID/start identity and conservative stale reclamation. `atomic-io.js` fsyncs a temporary file and renames it for replacement. Its create helper uses exclusive copy, not an atomic publication guarantee. These are not OS-enforced CAS against arbitrary editors. `direct-edit-bundle-*` recovery is not a general Unity transaction.
- `direct-delete-capabilities.js` binds proposals to paths and content receipts, but its caller-supplied `userApproved` flag is not independent human approval. Unity now has a separate one-use Editor approval window and no MCP approval endpoint; existing Unreal behavior is not changed.
- SAFE uses `ALLOW_WRITE=0`, `ALLOW_COMMANDS=0`, `ALLOW_UNREAL_BUILD=0`. Unity maps Observe/Edit/Execute independently and checks them again in the Bridge. No model flag is allowed to enable permissions.
- `scripts/build_integrated_package.py` uses an explicit directory/file allowlist. Unity components must be included explicitly. Existing Intel Mac/CLI edits are preserved.

## Responsibility and rollout

`lmstudio-unity-mcp` is a Node.js stdio adapter. `shared-tool-core` contains engine-independent file/data operations parameterized by a Unity path policy. It reuses existing low-level read, lock and replacement primitives without rewriting Unreal. The RPC Bridge is Editor-only; its UPM package also has a small runtime debug-registration contract and optional-package runtime observation probes. Test framework dependencies remain separate. Symbols, references, real debug adapters, snapshots and bounded recording are mandatory v1.4 release gates; see [the expanded contract](Unity_Debug_1_4.md).

Project roots, engine installation paths and game types are never constants. Unity 2022.3+ common APIs form the compatibility baseline; the installed 6000.3 Editor is one test environment, not a product requirement for that exact version. Capabilities describe the implemented runtime surface; testing one version does not establish compatibility with all releases. Projects with unsupported versions/dependencies must resolve those prerequisites explicitly, not via a server-selected upgrade.

The implementation is a beta 1 vertical slice. The capability table in `Unity_Setup.md` is the authoritative implemented scope; missing operations fail with `capability_unavailable`, never synthetic success. The runtime makes no LLM calls, chooses no models or next actions, installs no packages and performs no Git/build/deployment actions.

## Wire and identity contract

- Protocol `1`, bridge/server `1.4.0-beta.1`. Local TCP, IPv4 loopback, OS-selected port, one bounded newline-delimited JSON request/reply per connection. No HTTP/browser origin surface. Discovery is under `Library/EvidenceFirst/bridge.json` with a random token, process ID, canonical root and session identity. Tokens are transport-only, never returned in MCP results.
- `projectIdentity` is a hash of the canonical root, not a copied project UUID. Handshake checks the canonical root and live Editor PID/session. `editorSessionId` survives domain reload via SessionState; `domainGeneration` increments at reload. `playSessionId` changes on entering/exiting Play. Object handles are also domain-bound.
- Every request carries `requestId`; mutations also require caller-chosen `operationId`. Mutation content, project and editor session are durably journaled before execution. Same ID/same content returns the record; different content conflicts. Pending records after reload/crash become `outcome_unknown`, never rerun. This is at-most-one attempt while the journal survives, not exactly-once execution. Records are bounded; capacity fails closed rather than evicting deduplication evidence.
- Connection loss is `disconnected` with any last-known identity, not assumed reload. Reads can reconnect through discovery; mutations are never automatically retransmitted. Operation lookup is the recovery path. This beta's cancellation endpoint reports `not_cancelled`; it does not claim to abort running Unity APIs or expose a cancellable long-running test queue.

## ObjectRef and receipts

- Assets: GUID + signed local file ID as a decimal string. Saved scene objects: GlobalObjectId plus scene path. Temporary edit objects: editor session + domain + opaque handle. Runtime objects: those fields plus Play session. Resolution fails on expired/missing references, never falls back to names.
- SerializedObject/SerializedProperty is the Inspector interface. Responses distinguish `value`, `null`, `missing_reference`, `container`, `managed_reference`, and `unsupported`. Managed graphs use IDs and paths, not recursive expansion or arbitrary getter execution.
- File receipt: per-adapter signed/registered observation of full file bytes, canonical project/path, expiry and server session. Existing edits require the receipt, checked inside a path lock and immediately before replacement. No silent rebase. Create requires `mustNotExist`.
- Object receipt: Bridge-owned observation, target, project/session/domain, scope and digest of serialized state and related hierarchy. Arrays are covered by serialized state including order. Read pagination binds to the same digest. External changes conflict. This does not cover all native/nonserialized state or delayed callbacks.

## Permissions, save and failure policy

- Observe is the default. Adapter Edit uses `ALLOW_WRITE=1`; Execute uses `ALLOW_COMMANDS=1`. Bridge menu permissions default off. Runtime mutation remains unavailable. Destructive edits additionally require exact request/state/session-bound approval in the Editor window.
- Scene edits and SO patches mark dirty. Saving requires the exact scene/asset and acknowledgement that existing dirty changes in that target may also be saved. Never SaveAssets/SaveOpenScenes globally. SaveAssetIfDirty bypasses OnWillSaveAssets; the response/documentation says so.
- Prefab source mutations use isolated contents and file receipts; nested/variant apply has an explicit source destination; revert requires independent approval. Managed-reference type replacement and scope-expanding array override application remain unavailable. See `Unity_Authoring_Tests.md` for actual supported scope.
- A short Undo group is confined to one synchronous operation. Undo is not a database transaction and cannot undo arbitrary project-code side effects. Failure states distinguish `not_applied`, `rolled_back`, `partially_applied`, `outcome_unknown`; re-read values are point-in-time observations. No universal `atomic=true`.
- General file access rejects symlink/reparse components, protected Unity formats and hidden/generated directories. Assets text is writable; Packages/ProjectSettings are read-only in beta 1. Same-account malicious filesystem races and executing an untrusted Unity project are not sandboxed.

## Official API references

- [GlobalObjectId limitations](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/GlobalObjectId.html): authoring IDs, saved/loaded scene requirements; runtime handles are separate.
- [SerializedProperty](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/SerializedProperty.html), [managed references](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/SerializedProperty-managedReferenceValue.html).
- [SaveAssetIfDirty](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/AssetDatabase.SaveAssetIfDirty.html), [isolated Prefab editing](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/PrefabUtility.LoadPrefabContents.html).
- [CompilationPipeline](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/Compilation.CompilationPipeline.html), [SessionState](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/SessionState.html).

Actual test evidence and remaining scenarios are recorded separately in `Unity_Validation.md`.
