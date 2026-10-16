// Read-only verification of the actual durable archive for this GUI conversation.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { hash } = require('../../lmstudio-context-compactor-plugin/src/evidence-archive.js');
const { decodeToolResultRecord } = require('../../lmstudio-context-compactor-plugin/src/compaction-tool-memory.js');
const chat = fs.readFileSync(process.argv[2], 'utf8');
const scopes = [...chat.matchAll(/hybrid-context-v1:([A-Za-z0-9_-]+)\./g)].map(m => JSON.parse(Buffer.from(m[1], 'base64url')));
const root = path.join(os.homedir(), '.lmstudio/unreal-context-compactor/hybrid-v1');
const windowNames = new Set(scopes.map(s => `window-${s.lineage}.json`));
const dirs = fs.readdirSync(root).filter(d => fs.statSync(path.join(root, d)).isDirectory()
  && fs.readdirSync(path.join(root, d)).some(f => windowNames.has(f)));
const project = 'C:/Users/sster/Documents/Git/Human-Bartender/HumanBartender';
const windows = [], records = [];
for (const dir of dirs) {
  for (const file of fs.readdirSync(path.join(root, dir))) {
    if (windowNames.has(file)) {
      const { digest, ...body } = JSON.parse(fs.readFileSync(path.join(root, dir, file), 'utf8'));
      windows.push({ file, digestValid: hash(body) === digest, scopeValid: body.scope === dir,
        generation: body.generation, refs: body.refs.length, rawBindings: body.rawBindings?.length,
        pendingRefs: body.pendingRefs?.length, pendingHistoricalResults: body.pendingHistoricalResults?.length,
        remeasureRequired: body.remeasureRequired, measurementSubject: body.measurementSubject });
    }
    if (!/^ev_[a-f0-9]{64}\.json$/.test(file)) continue;
    const { recordHash, ...body } = JSON.parse(fs.readFileSync(path.join(root, dir, file), 'utf8'));
    const value = decodeToolResultRecord(body.body).value;
    const result = { id: body.evidenceId, recordIntegrity: hash(body) === recordHash,
      bodyIntegrity: hash(body.body) === body.archivedBodyHash, scopeValid: body.scope === dir,
      path: value?.path, sourceRange: body.metadata?.originRanges, callId: body.metadata?.providerRequestId };
    if (['Packages/manifest.json', 'Packages/packages-lock.json'].includes(value?.path)) {
      const source = fs.readFileSync(path.join(project, value.path));
      const lines = source.toString('utf8').replace(/^\uFEFF/, '').split(/\r\n|\n|\r/);
      result.sourceVersionMatches = value.hash === hash(source.toString('utf8'));
      result.sourceTextMatches = value.text === lines.slice(value.startLine - 1, value.endLine).join('\n');
      result.sourceContentEquivalent = result.sourceTextMatches;
      if (!result.sourceTextMatches) {
        try { result.sourceContentEquivalent = JSON.stringify(JSON.parse(value.text))
          === JSON.stringify(JSON.parse(lines.slice(value.startLine - 1, value.endLine).join('\n'))); }
        catch { /* Partial source text must remain exact. */ }
      }
      result.archiveRedacted = body.redacted;
      result.startLine = value.startLine; result.endLine = value.endLine;
    }
    records.push(result);
  }
}
const result = { archiveDirectories: dirs, recordCount: records.length, windows, records,
  allRecordIntegrity: records.length > 0 && records.every(r => r.recordIntegrity && r.bodyIntegrity && r.scopeValid),
  sourcePages: records.filter(r => r.sourceTextMatches !== undefined).length,
  allSourcePagesMatch: records.length > 0 && records.every(r => r.sourceTextMatches !== false && r.sourceVersionMatches !== false),
  allSourceContentEquivalent: records.length > 0 && records.every(r => r.sourceContentEquivalent !== false && r.sourceVersionMatches !== false),
  limitation: 'Verification covers retained records at this instant, not indefinite retention or semantic recall.' };
fs.writeFileSync(path.join(__dirname, 'live-archive-verification.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ ...result, records: undefined }));
