"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { deriveValidationScope } = require("../src/validation-scope.js");

test("task validation is bounded to the selected slice", () => {
  const result = deriveValidationScope({
    status: "running",
    continuity: {
      checkpoint: {
        mutationGeneration: 3,
        modifiedFiles: [
          "Source/Demo/Old.cpp",
          "Source/Demo/Current.cpp",
          "Plugins/Feature/Source/Feature/Current.h",
        ],
      },
    },
    toolRoute: {
      selectedSlice: {
        files: [
          "project://Source/Demo/Current.cpp",
          "C:/Repo/Demo/Plugins/Feature/Source/Feature/Current.h",
        ],
      },
    },
  }, 3, { taskBound: true });

  assert.deepStrictEqual(result, {
    kind: "task_slice",
    targets: [
      "Source/Demo/Current.cpp",
      "Plugins/Feature/Source/Feature/Current.h",
    ],
  });
});

test("project-wide validation requires an unbound call or explicit full audit", () => {
  assert.deepStrictEqual(
    deriveValidationScope(null, 0, { taskBound: false }),
    { kind: "full_audit", targets: [] }
  );
  assert.deepStrictEqual(
    deriveValidationScope(null, 0, { taskBound: true, fullAudit: true }),
    { kind: "full_audit", targets: [] }
  );
});

test("a bound task never silently falls back to a full audit", () => {
  const result = deriveValidationScope({
    status: "running",
    continuity: { checkpoint: { mutationGeneration: 2 } },
    toolRoute: { selectedSlice: { files: ["Source/Demo/Foo.cpp"] } },
  }, 3, { taskBound: true });

  assert.strictEqual(result.kind, "task_scope_unavailable");
  assert.deepStrictEqual(result.targets, []);
});

test("validation scope folds path case on Windows only", () => {
  const state = {
    status: "running",
    continuity: {
      checkpoint: {
        mutationGeneration: 5,
        modifiedFiles: ["source/demo/feature.cpp"],
      },
    },
    toolRoute: {
      selectedSlice: { files: ["project://Source/Demo/Feature.cpp"] },
    },
  };

  assert.deepStrictEqual(
    deriveValidationScope(state, 5, { taskBound: true, hostPlatform: "win32" }),
    { kind: "task_slice", targets: ["Source/Demo/Feature.cpp"] }
  );
  const posix = deriveValidationScope(state, 5, {
    taskBound: true,
    hostPlatform: "linux",
  });
  assert.equal(posix.kind, "task_scope_unavailable");
  assert.deepStrictEqual(posix.targets, []);
});

test("validation target deduplication preserves POSIX case-distinct files", () => {
  const state = {
    status: "running",
    continuity: {
      checkpoint: {
        mutationGeneration: 6,
        modifiedFiles: [
          "Source/Demo/Feature.cpp",
          "Source/Demo/feature.cpp",
        ],
      },
    },
    toolRoute: { selectedSlice: { files: [] } },
  };

  assert.deepStrictEqual(
    deriveValidationScope(state, 6, { taskBound: true, hostPlatform: "linux" }).targets,
    ["Source/Demo/Feature.cpp", "Source/Demo/feature.cpp"]
  );
  assert.deepStrictEqual(
    deriveValidationScope(state, 6, { taskBound: true, hostPlatform: "win32" }).targets,
    ["Source/Demo/Feature.cpp"]
  );
});
