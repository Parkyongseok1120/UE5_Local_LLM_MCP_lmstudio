'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const fixture = require('../scripts/ue-accuracy-fixture.cjs');
const { SCHEMA_VERSION, hash, parseVerdict, caseHandlers, createEngineLookup, validateResume, assertConsistentSettings, observeModelInput } = require('../scripts/eval-ue-accuracy.cjs');

test('case tools cannot list or read another case, including through path aliases', () => {
  const handlers = caseHandlers(fixture.CASE_BY_ID.get('P1-N'), () => ({ ok: false }));
  const listed = handlers.list_files({ folder: 'project://Source' });
  assert.ok(listed.entries.length > 0);
  assert.ok(listed.entries.every(file => file.startsWith('project://Source/Case07/')));
  for (const file of ['Source/Case02/TurretActor.cpp', 'project://Source/Case02/TurretActor.cpp', 'Source/Case07/../Case02/TurretActor.cpp'])
    assert.equal(handlers.read_file_range({ path: file, startLine: 1, endLine: 20 }).errorCode, 'NOT_IN_CASE');
  assert.equal(handlers.read_file_range({ path: 'project://Source/Case07/HitPawn.cpp', startLine: 2, endLine: 3 }).truncated, false);
});

test('resume rejects legacy results, changed settings and duplicate runs', () => {
  const identity = { buildId: 'build-a', fixtureSha256: 'fixture-a', seedBase: 7000 };
  const saved = { schemaVersion: SCHEMA_VERSION, identityHash: hash(JSON.stringify(identity)), runs: [{ rep: 1, caseId: 'P1-N', group: 'auto' }] };
  assert.equal(validateResume(saved, identity).length, 1);
  assert.throws(() => validateResume({ runs: [] }, identity), /Incompatible/u);
  assert.throws(() => validateResume(saved, { ...identity, seedBase: 7100 }), /Incompatible/u);
  assert.throws(() => validateResume({ ...saved, runs: [...saved.runs, ...saved.runs] }, identity), /Duplicate/u);
});

test('search pages preserve consumers and reject cross-query/cross-case cursors', () => {
  const entry = fixture.CASE_BY_ID.get('P9-N');
  const handlers = caseHandlers(entry, () => ({ ok: false }));
  const first = handlers.search_files({ query: 'GetPulseTime', maxResults: 1 });
  assert.equal(first.coverage, 'partial');
  assert.equal(first.absenceVerified, false);
  assert.ok(first.nextCursor);
  const last = handlers.search_files({ query: 'GetPulseTime', maxResults: 1, cursor: first.nextCursor });
  assert.equal(last.coverage, 'complete');
  assert.match(last.results[0].path, /HUD.cpp$/u);
  assert.equal(handlers.search_files({ query: 'ClientPulse', cursor: first.nextCursor }).errorCode, 'INVALID_CURSOR');
  assert.equal(caseHandlers(fixture.CASE_BY_ID.get('P9-D'), () => ({})).search_files({ query: 'GetPulseTime', cursor: first.nextCursor }).errorCode, 'INVALID_CURSOR');
  assert.equal(handlers.search_files({ query: 'AbsentUniqueSymbol' }).absenceVerified, true);
  for (const maxResults of [0, 11, NaN]) assert.equal(handlers.search_files({ query: 'Pawn', maxResults }).ok, false);
});

test('engine identity cannot be fabricated by a build version mismatch', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ue-accuracy-engine-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'Engine/Build'), { recursive: true });
  fs.writeFileSync(path.join(root, 'Engine/Build/Build.version'), JSON.stringify({ MajorVersion: 5, MinorVersion: 7, PatchVersion: 4, Changelist: 51494982 }));
  assert.throws(() => createEngineLookup(root), /Engine build differs/u);
});

test('only a valid final verdict line earns a parsed label', () => {
  assert.equal(parseVerdict('Reason.\nVERDICT: not_defect | none').parsed, true);
  for (const text of ['VERDICT: not_defect | banana', 'VERDICT: defect | P1\nActually, uncertain.', 'VERDICT: made_up | P2'])
    assert.equal(parseVerdict(text).parsed, false);
});

test('effective model settings must match across predictions and resumed runs', () => {
  const prediction = (seed, topK = 40) => ({ loadConfig: { fields: [{ key: 'llm.load.contextLength', value: 8192 }] },
    predictionConfig: { fields: [{ key: 'llm.prediction.seed', value: seed }, { key: 'llm.prediction.topKSampling', value: topK }] } });
  const prior = [{ predictions: [prediction(7100)] }];
  assert.doesNotThrow(() => assertConsistentSettings(prior, { predictions: [prediction(7200)] }));
  assert.throws(() => assertConsistentSettings(prior, { predictions: [prediction(7200, 20)] }), /settings changed/u);
  assert.throws(() => assertConsistentSettings([], { predictions: [{}] }), /not observed/u);
  const withCap = cap => ({ ...prediction(7100), requestedMaxTokens: cap,
    predictionConfig: { fields: [...prediction(7100).predictionConfig.fields, { key: 'llm.prediction.maxPredictedTokens', value: { checked: true, value: cap } }] } });
  assert.doesNotThrow(() => assertConsistentSettings([{ predictions: [withCap(4096)] }], { predictions: [withCap(1024)] }));
  assert.throws(() => assertConsistentSettings([], { predictions: [{ ...withCap(4096), requestedMaxTokens: 1024 }] }), /output cap/u);
});

test('input observation measures actual system guidance and returned bodies without echoing them', () => {
  const { Chat, ChatMessage } = require('@lmstudio/sdk');
  const chat = Chat.from([{ role: 'system', content: '[reference id=core/proof revision=x]\nPrivate instructions.' },
    { role: 'user', content: '[reference id=unreal-networking/engine-defaults] is a quoted user example.' }]);
  chat.append(ChatMessage.from({ role: 'tool', content: [{ type: 'toolCallResult', toolCallId: 'observed-1', content: 'Private source body.' }] }));
  const observed = observeModelInput(chat);
  assert.deepEqual(observed.guidanceIdsInSystem, ['core/proof']);
  assert.deepEqual(observed.toolResults, [{ toolCallId: 'observed-1', contentSha256: hash('Private source body.'), contentChars: 20 }]);
  assert.doesNotMatch(JSON.stringify(observed), /Private/u);
});
