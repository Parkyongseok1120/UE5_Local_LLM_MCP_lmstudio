# v1.4.0 mandatory investigation and debug scope

Symbols, all four reference categories, working debug adapters, snapshots and bounded recording are **v1.4.0 release gates**, not optional post-1.4 features. Package-dependent adapters activate only if their package is already installed. Missing packages and missing registrations are distinct states. No package, model, analyzer, source generator or build task is installed/executed by the investigation tools.

The server performs the requested primitive. It never interprets observations as a cause, selects another adapter, repairs code, retries a mutation or starts a model. `scripts/test_unity_debug.js` is a development-only predetermined test sequence, not an autonomous product endpoint.

## Semantic symbols

Build `unity-symbol-worker/UnitySymbolWorker.csproj` with a compatible .NET SDK (`dotnet publish -c Release -o <worker-output>`). Configure the MCP process with `UNITY_DOTNET` pointing to the matching dotnet executable and `UNITY_SYMBOL_WORKER` pointing to the published `UnitySymbolWorker.dll`. There is no hardcoded engine installation or project path. `scripts/build_unity_symbol_worker.js` can instead build with an explicitly supplied compatible Roslyn/runtime toolchain for offline development; its observed compiler version is returned, not presented as the package's reference version.

Call `unity_symbols`:

1. `action: assemblies` lists the current Editor compilation context.
2. `action: index, assemblies: ["YourAssembly"]` explicitly selects semantic analysis. Returns an accepted job; poll `action: status`.
3. Use the returned exact `indexVersion` with `find` (exact symbol name or containing symbol), `usages`, `relations`, `diagnostics`, `omissions`, `versions` (source/metadata/response hashes).

Unity exports assembly closure, sources, defines, compiled references, compiler arguments/response files, language, unsafe and optimization settings. The external worker uses Roslyn declarations, symbol binding, inheritance and interface implementation—not text matches. Code locations carry source hashes and script GUIDs where Unity supplies them. External metadata/response hashes and manifest/compilation/session identifiers bind the index. Queries read an immutable index; **current sources are not rechecked**, so the caller selects reindexing. `completeWithinScope` concerns only the returned selected-assembly and explicit semantic-binding coverage; errors, omitted generators/options/missing references are disclosed. Implicit/lowered compiler operations and unavailable generated sources are not claimed as complete uses. Invalid portions do not suppress resolvable symbols.

Limits: 1 active worker, 90 s parent timeout/80 s worker deadline, 1 GiB managed heap, 16 MiB manifest, 256 assembly closure, 5000 files, 4 MiB/file, 128 MiB source total, 512 MiB metadata accounting, 100000 symbols, 300000 uses, 100000 relations, 1000 diagnostics, 2000 omissions, 64 MiB output/index. One index retained in MCP memory; explicit reindex replaces it; MCP restart loses it. This is not a sandbox against a malicious same-user executable/runtime configuration.

## References

`unity_symbols.usages` provides compiler code-use edges, with forward/reverse symbol IDs.

`unity_references` has `collect`, `query`, `release`. A collection returns `indexId`; query supports exact `endpoint` and `direction`. It is stored evidence, not an automatically maintained live index.

- `asset_dependency`: explicit Assets paths; `AssetDatabase.GetDependencies`, path-level only, explicit recursive flag.
- `serialized_reference`: explicit ObjectRefs; actual SerializedProperty object reference values, propertyPath and missing-reference omissions. MonoScript/compiled-type links use Unity's actual binding and script GUID, never a name guess.
- `observed_runtime_reference`: explicit endpoints; only retained instrumentation events with actual source/target objects. Event IDs, time/frame and explicit action correlation are evidence; unobserved dynamic references remain unknown.

Reverse queries cover only the collected sources, **not the whole project**. Empty results never imply unused. Closed Scenes are not opened or saved. Loaded authoring scene objects, selected assets and live handles obey the existing identity contract. Script/compiled-type evidence does not establish that current disk source equals the loaded assembly.

Limits: 32 explicit object endpoints or 100 asset paths, 10000 serialized fields per object (truncation disclosed), 2000 edges/collection. Private disk store: 64 collections, 1 MiB each, 16 MiB total. Explicit release only; full stores fail closed.

## Debug adapters

`unity_debug_query(adapter: "catalog", input: {})` lists registrations, schemas, versions, query/action permission and optional-package availability. Query uses Observe. `unity_debug_action` requires Execute at both MCP and Editor plus a caller operationId. Actions are journaled; duplicates are not rerun. Registered game actions require Play. A callback/output failure is conservatively `outcome_unknown`, because project side effects may already have occurred. No automatic Undo or save is promised for gameplay.

- `object.state`: known object activity and identity; `unity_object_read` provides explicit serialized fields. No arbitrary getters.
- `input.state` / `input.observe`: Input System control state, and actual changed-control state/delta events after subscription. Explicit `controlPath` and duration.
- `ui.state` / `ui.observe`: uGUI Selectable/EventSystem state and actual pointer-click/submit/select/deselect callbacks. This is **uGUI, not UI Toolkit**.
- `physics.state` / `physics.observe`: Rigidbody/collider state and actual CollisionEnter/TriggerEnter callbacks. This is **3D, not Physics2D**.
- `events.start`: explicit kinds, maximum milliseconds and frames; `events` reads retained rows; `events.stop` stops collection. Only main-thread instrumentation is collected. Event retention is latest 500 rows/256 KiB and exposes first-retained sequence; it is not historical coverage.

UI/physics observation is explicit instrumentation: it adds only its own temporary DontSave probe to a selected live runtime GameObject, never an asset/prefab source. An owned-allocation registry enforces 16 probes per adapter/48 total, including inactive/DontSave objects. Editor updates clean up expired probes even while Play is paused, and reload/Stop cleans up owned probes only. Maximum lifetime is 60 seconds. Input subscriptions have a deadline and unload cleanup. No caller-selected methods are reflected or invoked.

`EvidenceFirst.Debugging.DebugRegistry.Register(id, kind, version, inputSchema, outputSchema, callback)` is the project extension contract. Dispose the registration on lifecycle end. Queries must be side-effect-free; actions are explicit trusted project code. The registry cannot sandbox a malicious callback. Closed schema subset: type, properties, required, additionalProperties:false, bounded items/maxItems, maxLength, enum, minimum/maximum; max depth 8, 64 registrations, 32 KiB callback data. `DebugRegistry.Emit` records an actual named event/source/target; it does not cause subsequent server actions. The fixture includes a real FSM query, damage/reset actions and transition instrumentation.

## Stored snapshots and recording

`unity_snapshot` actions: `capture`, `read`, `diff`, `release`, `record_start`, `record_status`, `record_stop`.

Capture requires `targets: [{target: ObjectRef, propertyPaths: ["health", "config"]}]`. An explicit GameObject root can also supply `descendantDepth: 1..6` to observe the same selected fields on its descendants. Maximum 32 captured objects total × 32 exact fields; overlapping scopes and exceeding that bound are rejected, with no recursive getter expansion. Arrays expose structure; request individual array element propertyPaths for element values. Capture returns ID and metadata, **not all values**. Read is metadata-only by default; `targetIndex` and optional propertyPaths/paging select stored values. Reading never resolves the present object again.

Metadata includes editor/domain/Play identity, target refs, time and frame start/end, schema and Bridge version, observed compilation ID and compiling flag, scope, omissions and per-target failures. Source/loaded-assembly verification can be unknown and is labeled. Multi-target collection is explicitly non-atomic. Pause is a separate caller-selected Editor action.

If metadata exceeds the caller's response budget, successful capture still returns the stored snapshot ID and `metadataOmitted:true`. Use `read` with `section:metadata/scope/failures` and paging, or `targetIndex`, for bounded retrieval. Diff ignores `editable` metadata changes when the observed value is unchanged; Edit→Play alone is not a value change.

Diff uses exact ObjectRefs only, never names. It distinguishes value/reference/collection-structure changes, a known selected object's disappearance/reappearance, field not-collected and incomparable states. Null, missing reference, inaccessible field, missing object and expired handle are different. Observed object additions/removals require the same successfully collected explicit descendant root/depth/field scope and compatible session; a changed selection is not a removal. Reparenting out of that scope means absence **within the scope**, not proven destruction. Different runtime sessions are incomparable. No cause is inferred and no rollback/replay guarantee is made; separately queried event correlation is the only recorded action evidence.

Recording requires all bounds: `maxDurationMs` 1..60000, `maxFrames` 1..3600, `intervalMs` 50..60000, `maxSamples` 1..32, `maxBytes` 1024..4194304. One active recording. Samples use the same immutable store; collection stops on the first bound, read/store failure or session change. Latest recording metadata and sample IDs survive reload; unfinished recording becomes stopped, never automatically resumed. Starting another recording replaces only latest-recording metadata, not retained samples. Each sample is non-atomic. No monitoring-driven actions.

Snapshot private store: 64 items, 1 MiB/item, 16 MiB total. Full store fails; explicit release is permanent deletion of that snapshot only. There is no automatic rollback.

## Release gate

Required independent integration sequence: semantic symbol → uses and serialized references → registered FSM query → snapshot A → explicit test action → B → diff → caller's judgment → Receipt patch → caller-selected re-test. Test harness additionally asserts Input/UI/physics observations and bounded recording. Run with an isolated `UNITY_TEST_DEBUG=1` fixture; this intentionally supplies test-only package dependencies, never mutates the user's game manifest. Prefab source editing, trusted approval and Test Framework primitives are now implemented; see `Unity_Authoring_Tests.md` and the latest section of `Unity_Validation.md`. Tested core paths do not establish a complete engine/OS matrix or every remaining original v1.4 capability.
