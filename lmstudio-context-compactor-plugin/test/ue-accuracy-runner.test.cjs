'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const fixture = require('../scripts/ue-accuracy-fixture.cjs');
const { SCHEMA_VERSION, hash, parseVerdict, caseHandlers, createEngineLookup, validateResume, assertConsistentSettings } = require('../scripts/eval-ue-accuracy.cjs');

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
