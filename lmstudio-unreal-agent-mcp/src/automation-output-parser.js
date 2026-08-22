"use strict";

const { cleanTestNames } = require("./automation-command-contract");

function completedAutomationTests(text) {
  const completedTests = [];
  for (const match of text.matchAll(/Test Completed\.\s*Result=\{([^}]+)\}([^\r\n]*)/gi)) {
    const tail = String(match[2] || "");
    const pathMatch = tail.match(/\bPath=\{([^}]+)\}/i);
    const nameMatch = tail.match(/\bName=\{([^}]+)\}/i);
    completedTests.push({
      name: String(pathMatch?.[1] || nameMatch?.[1] || "").trim(),
      result: String(match[1] || "").trim().toLowerCase(),
    });
  }
  if (completedTests.length === 0) {
    for (const match of text.matchAll(/Automation Test (Succeeded|Failed)\s*\(([^)]+)\)/gi)) {
      completedTests.push({
        name: String(match[2] || "").trim(),
        result: String(match[1] || "").trim().toLowerCase(),
      });
    }
  }
  return completedTests;
}

function parseAutomationOutput(output, exitCode = 0, options = {}) {
  const text = String(output || "");
  const completedTests = completedAutomationTests(text);
  const isSuccess = (item) => ["success", "succeeded"].includes(item.result);
  const succeededTests = completedTests.filter(isSuccess).map((item) => item.name).filter(Boolean);
  const failedTests = completedTests.filter((item) => !isSuccess(item))
    .map((item) => item.name).filter(Boolean);
  const succeeded = completedTests.length > 0
    ? completedTests.filter(isSuccess).length
    : (text.match(/Automation Test Succeeded/gi) || []).length;
  const failed = completedTests.length > 0
    ? completedTests.length - succeeded
    : (text.match(/Automation Test Failed/gi) || []).length;
  const terminalExitMatch = text.match(/TEST COMPLETE\.\s*EXIT CODE:\s*(-?\d+)/i);
  const terminalExitCode = terminalExitMatch ? Number(terminalExitMatch[1]) : 0;
  const queueEmpty = /Automation Test Queue Empty/i.test(text) || Boolean(terminalExitMatch);
  const expectedTests = cleanTestNames(options.expectedTests);
  const succeededKeys = new Set(succeededTests.map((name) => name.toLowerCase()));
  const missingTests = expectedTests.filter((name) => {
    const expected = name.toLowerCase();
    return ![...succeededKeys].some((succeededName) => (
      succeededName === expected || succeededName.startsWith(`${expected}.`)
    ));
  });
  const common = {
    succeededCount: succeeded,
    failedCount: failed,
    queueEmpty,
    terminalComplete: queueEmpty,
    completedTests,
    succeededTests: cleanTestNames(succeededTests),
    failedTests: cleanTestNames(failedTests),
    expectedTests,
    missingTests,
  };
  if (failed > 0 || Number(exitCode) !== 0 || terminalExitCode !== 0) {
    return {
      ok: false,
      errorCode: failed > 0 ? "AUTOMATION_TEST_FAILED" : "AUTOMATION_PROCESS_FAILED",
      ...common,
    };
  }
  if (!queueEmpty) return { ok: false, errorCode: "AUTOMATION_INCOMPLETE", ...common };
  if (succeeded === 0) return { ok: false, errorCode: "NO_AUTOMATION_TESTS_EXECUTED", ...common };
  if (missingTests.length > 0) {
    return { ok: false, errorCode: "AUTOMATION_COVERAGE_INCOMPLETE", ...common };
  }
  return { ok: true, errorCode: "", ...common };
}

module.exports = { parseAutomationOutput };
