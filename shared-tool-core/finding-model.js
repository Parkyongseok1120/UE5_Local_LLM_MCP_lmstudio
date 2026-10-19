"use strict";
// Same metadata contract as Unreal's lexical findings. Warnings are candidates,
// never a compile/runtime verdict or proof of a missing implementation.
function lexicalFinding(ruleId, path, line, message, confidence, contractIds, limitations = []) {
  return { code: ruleId, severity: "warning", blocking: false, path, line, message,
    metadata: { ruleId, analysisKind: "lexical", confidence, contractIds,
      limitations: ["No semantic binding, control-flow proof, inheritance or runtime activation analysis.", ...limitations] } };
}
module.exports = { lexicalFinding };
