"use strict";

function compactTaskAuthorization(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const compact = {};
  for (const key of ["taskSessionId", "ownerCapability"]) {
    if (String(value[key] || "").trim()) compact[key] = value[key];
  }
  return compact;
}

function withCompactTaskAuthorization(argumentsValue, authorization) {
  const args = argumentsValue && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)
    ? { ...argumentsValue }
    : {};
  const ownership = compactTaskAuthorization(authorization);
  if (ownership && typeof ownership === "object" && Object.keys(ownership).length) {
    args.taskAuthorization = ownership;
  }
  return args;
}

function sanitizeModelPayload(value) {
  if (Array.isArray(value)) return value.map((item) => sanitizeModelPayload(item));
  if (!value || typeof value !== "object") return value;
  const sanitized = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "expiryTransition") continue;
    sanitized[key] = key === "taskAuthorization"
      ? compactTaskAuthorization(item)
      : sanitizeModelPayload(item);
  }
  return sanitized;
}

module.exports = {
  compactTaskAuthorization,
  sanitizeModelPayload,
  withCompactTaskAuthorization,
};
