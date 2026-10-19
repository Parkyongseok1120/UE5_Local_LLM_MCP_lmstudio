'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');

test('stdio client receives a scoped locator and reaches a late symbol without first-page reads', { timeout: 20000 }, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ue-locate-stdio-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const project = path.join(root, 'Game.uproject');
  fs.writeFileSync(project, JSON.stringify({ FileVersion: 3, EngineAssociation: '5.8' }));
  fs.mkdirSync(path.join(root, 'Source'));
  fs.writeFileSync(path.join(root, 'Source', 'Combat.cpp'),
    [...Array.from({ length: 350 }, (_, i) => `// unrelated line ${i + 1}`), 'float DamageCooldown() { return 0.75f; }'].join('\n'));
  const client = new Client({ name: 'locate-first-regression', version: '1' });
  try {
    await client.connect(new StdioClientTransport({ command: process.execPath,
      args: [path.resolve(__dirname, '../src/direct-server.js')], cwd: root, stderr: 'pipe',
      env: { ...process.env, WORKSPACE_ROOT: root, AGENT_STATE_ROOT: path.join(root, 'state'),
        SHARED_UNREAL_CONFIG: path.join(root, 'shared.json'), ALLOW_WRITE: '0', ALLOW_COMMANDS: '0', ALLOW_UNREAL_BUILD: '0' } }));
    const refused = await client.callTool({ name: 'read_file', arguments: { project, path: 'project://Source/Combat.cpp' } });
    const payload = refused.structuredContent;
    assert.equal(payload.errorCode, 'WHOLE_READ_TOO_LARGE');
    assert.equal(payload.suggestion.args, undefined);
    assert.equal(payload.suggestion.argsTemplate.project, project);
    assert.deepEqual(JSON.parse(refused.content[0].text).suggestion, payload.suggestion);
    const result = await client.callTool({ name: payload.suggestion.tool,
      arguments: { ...payload.suggestion.argsTemplate, query: 'DamageCooldown' } });
    assert.equal(result.structuredContent.ok, true);
    assert.match(JSON.stringify(result.structuredContent), /0\.75f/u);
    const read = await client.callTool({ name: 'read_file_range', arguments: { project, path: 'project://Source/Combat.cpp', startLine: 351, endLine: 351 } });
    assert.equal(read.structuredContent.rangeComplete, true);
    assert.equal(read.structuredContent.nextStartLine, undefined);
    assert.match(read.structuredContent.content, /DamageCooldown/u);
  } finally { await client.close(); }
});
