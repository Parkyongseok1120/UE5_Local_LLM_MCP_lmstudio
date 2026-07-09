"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");

const SOURCE_EXTENSIONS = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs"]);
const PATCH_ONLY_EXTENSIONS = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs", ".ini", ".uproject", ".uplugin"]);
const DENY_PATH_SEGMENTS = new Set(["saved", "binaries", "intermediate", "deriveddatacache", ".git", ".vs"]);
const ALLOWED_CREATE_DIR_SEGMENTS = new Set(["source", "content", "config"]);

function isSourceLikeExt(ext) {
  return SOURCE_EXTENSIONS.has(String(ext || "").toLowerCase());
}

function isPatchOnlyExistingFile(filePath) {
  return PATCH_ONLY_EXTENSIONS.has(path.extname(String(filePath || "")).toLowerCase());
}

function isDeniedPath(absPath) {
  const parts = String(absPath || "").split(/[\\/]/).map((part) => part.toLowerCase());
  return parts.some((part) => DENY_PATH_SEGMENTS.has(part));
}

function isDefaultConfigIni(filePath) {
  const base = path.basename(String(filePath || "")).toLowerCase();
  const ext = path.extname(base).toLowerCase();
  return ext === ".ini" && base.startsWith("default");
}

async function walkSourceFiles(sourceRoot, onFile) {
  async function walk(dir) {
    let entries;
    try {
      entries = await fsp.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (DENY_PATH_SEGMENTS.has(entry.name.toLowerCase())) {
          continue;
        }
        await walk(full);
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      const ext = path.extname(entry.name).toLowerCase();
      if (!SOURCE_EXTENSIONS.has(ext)) {
        continue;
      }
      await onFile(full);
    }
  }
  await walk(sourceRoot);
}

async function findSourceBasenameCollisions(targetAbsPath, workspaceRoot, activeProjectDir) {
  const ext = path.extname(targetAbsPath).toLowerCase();
  if (!SOURCE_EXTENSIONS.has(ext)) {
    return [];
  }
  const basename = path.basename(targetAbsPath);
  const normalizedTarget = path.resolve(targetAbsPath);
  const projectRoot = activeProjectDir ? path.resolve(activeProjectDir) : null;
  const searchRoots = [];
  if (projectRoot) {
    const sourceDir = path.join(projectRoot, "Source");
    if (fs.existsSync(sourceDir)) {
      searchRoots.push(sourceDir);
    }
  } else {
    try {
      const entries = await fsp.readdir(workspaceRoot, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory()) {
          continue;
        }
        const sourceDir = path.join(workspaceRoot, entry.name, "Source");
        if (fs.existsSync(sourceDir)) {
          searchRoots.push(sourceDir);
        }
      }
    } catch {
      return [];
    }
  }

  const matches = [];
  for (const sourceRoot of searchRoots) {
    await walkSourceFiles(sourceRoot, async (filePath) => {
      if (path.basename(filePath).toLowerCase() !== basename.toLowerCase()) {
        return;
      }
      if (path.resolve(filePath) === normalizedTarget) {
        return;
      }
      matches.push(path.relative(workspaceRoot, filePath));
    });
  }
  return matches;
}

function resolveProjectRootFromTarget(targetAbsPath, activeProjectPath) {
  let dir = path.dirname(targetAbsPath);
  for (let depth = 0; depth < 12; depth += 1) {
    try {
      const entries = fs.readdirSync(dir);
      if (entries.some((entry) => entry.toLowerCase().endsWith(".uproject"))) {
        return dir;
      }
    } catch {
      break;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  if (activeProjectPath) {
    return path.dirname(path.resolve(activeProjectPath));
  }
  return null;
}

function isAllowedCreateDir(parentAbsPath, workspaceRoot, activeProjectPath) {
  const projectRoot = resolveProjectRootFromTarget(parentAbsPath, activeProjectPath);
  if (!projectRoot) {
    return false;
  }
  const rel = path.relative(projectRoot, parentAbsPath);
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) {
    return false;
  }
  const top = rel.split(/[\\/]/)[0]?.toLowerCase() || "";
  return ALLOWED_CREATE_DIR_SEGMENTS.has(top);
}

async function validateWriteTarget({
  targetAbsPath,
  workspaceRoot,
  activeProjectPath,
  createDirs,
  fileExists,
  allowExistingWrite,
}) {
  if (isDeniedPath(targetAbsPath)) {
    return {
      ok: false,
      message: `write blocked: path is in a protected directory (Saved/Binaries/Intermediate): ${path.relative(workspaceRoot, targetAbsPath)}`
    };
  }

  const targetExists = fileExists ? await fileExists(targetAbsPath) : fs.existsSync(targetAbsPath);

  if (targetExists && isDefaultConfigIni(targetAbsPath)) {
    return {
      ok: false,
      message: `write_file blocked for existing Default*.ini. Use replace_in_file with exact oldText/newText: ${path.relative(workspaceRoot, targetAbsPath)}`
    };
  }

  if (targetExists && isPatchOnlyExistingFile(targetAbsPath)) {
    const rel = path.relative(workspaceRoot, targetAbsPath);
    return {
      ok: false,
      message: `write_file blocked for existing protected file: ${rel}. Use replace_in_file instead.`
    };
  }

  // Create-only: block ALL other existing files regardless of extension.
  // write_file is for creating new files; use replace_in_file to modify existing ones.
  // ALLOW_EXISTING_SOURCE_WRITE=1 is the only deliberate manual override.
  if (targetExists && !allowExistingWrite) {
    const rel = path.relative(workspaceRoot, targetAbsPath);
    return {
      ok: false,
      message: `write_file blocked because file already exists: ${rel}. Use replace_in_file. Do not retry write_file.`
    };
  }

  const projectRoot = resolveProjectRootFromTarget(targetAbsPath, activeProjectPath);
  const collisions = await findSourceBasenameCollisions(
    targetAbsPath,
    workspaceRoot,
    projectRoot
  );
  if (collisions.length > 0) {
    return {
      ok: false,
      message: `write_file blocked: basename collision under Source/. Existing file(s): ${collisions.join(", ")}. Use replace_in_file on the existing path or delete_file (extended mode) after cleanup.`
    };
  }

  if (createDirs) {
    const parent = path.dirname(targetAbsPath);
    if (!(await fileExists(parent)) && !isAllowedCreateDir(parent, workspaceRoot, activeProjectPath)) {
      return {
        ok: false,
        message: "createDirs blocked outside active project Source/Content/Config tree. Create directories under the active project only."
      };
    }
  }

  return { ok: true };
}

// Stale-safe rollback decision: only revert a write if the file on disk still holds
// exactly what this request wrote. If it differs, a newer operation owns the file and
// rolling back would clobber it, so the caller must skip rollback and report a conflict.
function shouldRollback(currentContent, ownWriteContent) {
  return currentContent === ownWriteContent;
}

function isDeleteAllowedPath(targetAbsPath, workspaceRoot, activeProjectPath) {
  if (isDeniedPath(targetAbsPath)) {
    return { ok: false, message: "delete blocked in protected directory." };
  }
  const projectRoot = resolveProjectRootFromTarget(targetAbsPath, activeProjectPath);
  if (!projectRoot) {
    return { ok: false, message: "delete blocked: could not resolve active project root." };
  }
  const rel = path.relative(projectRoot, targetAbsPath);
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) {
    return { ok: false, message: "delete blocked: path is outside active project." };
  }
  const relLower = rel.replace(/\\/g, "/").toLowerCase();
  if (!relLower.startsWith("source/")) {
    return { ok: false, message: "delete blocked: only files under Source/ may be deleted." };
  }
  const ext = path.extname(targetAbsPath).toLowerCase();
  if (!SOURCE_EXTENSIONS.has(ext)) {
    return { ok: false, message: "delete blocked: only source-like extensions (.h/.cpp/.cs) are allowed." };
  }
  return { ok: true, projectRoot };
}

module.exports = {
  PATCH_ONLY_EXTENSIONS,
  SOURCE_EXTENSIONS,
  isPatchOnlyExistingFile,
  isDeniedPath,
  findSourceBasenameCollisions,
  validateWriteTarget,
  shouldRollback,
  isDeleteAllowedPath,
  resolveProjectRootFromTarget,
};
