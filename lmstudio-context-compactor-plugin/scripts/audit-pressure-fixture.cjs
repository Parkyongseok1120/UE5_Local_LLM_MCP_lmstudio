"use strict";

const crypto = require("node:crypto");

const PROJECT_IDENTITY = "C:\\SyntheticAudit\\AuditProject.uproject";

function paddedSource(name, totalLines, inserts, preamble = []) {
  const lines = Array.from({ length: totalLines }, (_, index) => (
    `// ${name} audit context ${String(index + 1).padStart(4, "0")}: settlement lifecycle evidence`
  ));
  for (let index = 0; index < preamble.length; index += 1) lines[index] = preamble[index];
  for (const [line, text] of Object.entries(inserts)) lines[Number(line) - 1] = text;
  return lines;
}

function fixtureFile(path, lines, kind = "source") {
  const content = lines.join("\n");
  return {
    path,
    kind,
    lines,
    content,
    sha256: crypto.createHash("sha256").update(content, "utf8").digest("hex"),
  };
}

const files = [
  fixtureFile("Assets/03.Scripts/Tycoon/GuestManager.cs", paddedSource("GuestManager", 1056, {
    24: "public sealed class GuestManager",
    188: "private void BeginDay()",
    189: "    dailySales.Reset();",
    190: "    activeSessions.Clear();",
    821: "private void SettleOrder(OrderSettlement settlement)",
    822: "    dailySales.Apply(settlement);",
    1018: "private void ReleaseGuest(GuestSession guest)",
    1034: "    int sessionTotal = guest.Settlements.Sum(item => item.Amount);",
    1036: "    playerDataWriter.AddMoney(sessionTotal);",
    1041: "    guest.Settlements.Clear();",
    1056: "}",
  }, ["using System.Linq;", "using UnityEngine;", "using VContainer;"]), "large_source"),
  fixtureFile("Assets/03.Scripts/Tycoon/DailySales.cs", paddedSource("DailySales", 180, {
    12: "public sealed class DailySales",
    28: "public void Reset() => settlements.Clear();",
    41: "public void Apply(OrderSettlement settlement) => settlements.Add(settlement);",
    72: "public int DayEndTotal() => settlements.Sum(item => item.Amount);",
    98: "// Day-end totals are reports; per-session wallet payment occurs in GuestManager.ReleaseGuest.",
  }, ["using System.Linq;", "using System.Collections.Generic;"])),
  fixtureFile("Assets/03.Scripts/Player/PlayerDataSO.cs", paddedSource("PlayerDataSO", 220, {
    18: "public sealed class PlayerDataSO : ScriptableObject, IPlayerDataWriter",
    64: "public int Money => money;",
    105: "public void AddMoney(int amount)",
    107: "    money += amount;",
    108: "    moneyChanged.Raise(amount);",
  }, ["using UnityEngine;"])),
  fixtureFile("Assets/03.Scripts/UI/UICashPanel.cs", paddedSource("UICashPanel", 200, {
    20: "public sealed class UICashPanel : MonoBehaviour",
    58: "public void AddCurrency(int amount)",
    60: "    StartCoroutine(PlayGainAndRefresh(amount));",
    91: "private IEnumerator PlayGainAndRefresh(int amount)",
    105: "    amountText.text = currentAmount.ToString();",
    126: "// No direct C# reference to moneyChanged is required when a prefab listener is serialized.",
  }, ["using System.Collections;", "using TMPro;", "using UnityEngine;"])),
  fixtureFile("Assets/ProjectLifeScope.prefab", paddedSource("ProjectLifeScope prefab", 240, {
    44: "--- !u!114 &410000",
    45: "MonoBehaviour:",
    52: "  m_Name: MoneyChangedListener",
    64: "  eventGuid: MONEY-EVENT-GUID-001",
    71: "  m_PersistentCalls:",
    75: "    m_Target: {fileID: 420000}",
    76: "    m_MethodName: AddCurrency",
    112: "--- !u!114 &420000",
    118: "  m_Script: {fileID: 11500000, guid: UI-CASH-PANEL-GUID}",
    119: "  amountText: {fileID: 430000}",
  }, ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]), "serialized_asset"),
  fixtureFile("Assets/03.Scripts/Event/GameEvent.cs", paddedSource("GameEvent", 150, {
    14: "public sealed class GameEvent<T>",
    41: "public void Raise(T value)",
    43: "    for (int i = listeners.Count - 1; i >= 0; i--)",
    44: "        listeners[i].Raise(value);",
    72: "// Self-removal of the current listener is tolerated by reverse traversal.",
    73: "// Removing a lower, not-yet-visited listener shifts the current listener down and can invoke it twice.",
    91: "public void Remove(IGameEventListener<T> listener) => listeners.Remove(listener);",
  }, ["using System.Collections.Generic;"])),
  fixtureFile("Assets/03.Scripts/Tycoon/SettlementFlow.cs", paddedSource("SettlementFlow", 180, {
    33: "// Per-session payment: GuestManager.ReleaseGuest -> PlayerDataSO.AddMoney.",
    61: "// Day-end query: DailySales.DayEndTotal; it must not pay the wallet again.",
    98: "public int BuildDayReport(DailySales sales) => sales.DayEndTotal();",
  })),
  fixtureFile("Evidence/RuntimeObservation.md", paddedSource("Runtime observation", 90, {
    4: "User report: a log appears, but the visible money value does not change.",
    8: "Unknown: exact log category and message text.",
    9: "Unknown: whether UICashPanel.AddCurrency ran on the active scene instance.",
    10: "Unknown: popup coroutine completion, object active state, and amountText assignment.",
    20: "Do not select one runtime cause without those observations.",
  }, ["# Synthetic runtime evidence boundary"]), "evidence"),
];

const FILES = new Map(files.map(file => [file.path, file]));

function readPayload(path, startLine = 1, endLine = Number.MAX_SAFE_INTEGER) {
  const file = FILES.get(String(path || ""));
  if (!file) return { ok: false, errorCode: "NOT_FOUND", message: `Synthetic file not found: ${path}` };
  const start = Math.max(1, Math.min(file.lines.length, Math.trunc(Number(startLine) || 1)));
  const requestedEnd = Math.max(start, Math.trunc(Number(endLine) || file.lines.length));
  const end = Math.min(file.lines.length, requestedEnd, start + 219);
  return {
    ok: true,
    kind: "workspace_file_observation",
    projectIdentity: PROJECT_IDENTITY,
    path: file.path,
    sha256: file.sha256,
    startLine: start,
    endLine: end,
    returnedLineCount: end - start + 1,
    totalLines: file.lines.length,
    truncated: end < requestedEnd || end < file.lines.length,
    nextStartLine: end < file.lines.length ? end + 1 : null,
    content: file.lines.slice(start - 1, end).join("\n"),
  };
}

const SYSTEM_PROMPT = [
  "You are auditing a synthetic Unity settlement flow under context pressure.",
  "Use tools when exact source is missing. Update conclusions when new evidence contradicts an earlier hypothesis.",
  "Do not treat current-input availability metadata as proof of correctness or completion.",
  "Runtime causes require runtime evidence. Submit a bounded report once every requested topic has evidence or an explicit unknown boundary.",
].join(" ");

const AUDIT_QUESTION = [
  "Complete the audit report for four topics:",
  "(1) cite the exact Reset and AddMoney call locations and distinguish per-session payment from day-end totals;",
  "(2) decide whether a zero direct-C# search is enough to declare the money UI absent, checking serialized binding evidence;",
  "(3) distinguish self-removal from removal of a lower unvisited listener in the reverse GameEvent loop;",
  "(4) given only the generic runtime report, state what remains unproven and the minimum runtime observation needed.",
  "Do not restart a full-project audit merely for reassurance. Valid targeted rereads are allowed.",
].join(" ");

module.exports = {
  AUDIT_QUESTION,
  FILES,
  PROJECT_IDENTITY,
  SYSTEM_PROMPT,
  files,
  readPayload,
};
