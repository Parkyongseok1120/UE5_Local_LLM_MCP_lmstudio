'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), os=require('node:os'),path=require('node:path');
const {createDirectRuntime,statSignature}=require('../src/direct-server');
const {FileSnapshotRegistry}=require('../src/file-snapshot-registry');
function fixture(t,options={}){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'read-contract-'));
  const project=path.join(root,'Game.uproject'), file=path.join(root,'A.cpp');
  fs.writeFileSync(project,JSON.stringify({FileVersion:3,Modules:[{Name:'Game',Type:'Runtime'}]}));
  const snapshots=new FileSnapshotRegistry({maxEntries:2});
  const runtime=createDirectRuntime({workspaceRoot:root,configPath:path.join(root,'config.json'),stateRoot:path.join(root,'state'),fileSnapshots:snapshots,
    getActiveProject:()=>{options.onResolve?.();return project;},env:{ALLOW_WRITE:'0',ALLOW_COMMANDS:'0',ALLOW_UNREAL_BUILD:'0'}});
  t.after(()=>{runtime.close();assert.equal(path.dirname(root),os.tmpdir());fs.rmSync(root,{recursive:true,force:true});});
  const time=new Date('2026-01-01T00:00:00Z');
  const write=text=>{fs.writeFileSync(file,text);fs.utimesSync(file,time,time);};write('int Value = 1;\n');
  const args={path:'project://A.cpp',startLine:1,endLine:1};
  const read=async(extra={},context={})=>(await runtime.callTool('read_file_range',{...args,...extra},context)).structuredContent;
  return {runtime,snapshots,file,project,write,read};
}
test('same file metadata with changed SHA returns the new source body',async t=>{
  const f=fixture(t), before=fs.statSync(f.file), first=await f.read();
  f.write('int Value = 2;\n');assert.equal(statSignature(fs.statSync(f.file)),statSignature(before));
  const second=await f.read({repeatReceipt:first.repeatReceipt});
  assert.notEqual(second.sha256,first.sha256);assert.match(second.content,/Value = 2/);assert.notEqual(second.duplicate,true);
});
test('duplicate reads preserve source identity without issuing hidden mutation snapshots',async t=>{
  const f=fixture(t), owner={sessionId:'owner'},first=await f.read({},owner);
  for(let i=0;i<100;i++){
    const repeated=await f.read({repeatReceipt:first.repeatReceipt},owner);
    assert.equal(repeated.duplicate,true);assert.equal(repeated.sha256,first.sha256);
    assert.equal(repeated.startLine,1);assert.equal(repeated.endLine,1);
    assert.equal(repeated.fileVersionReceipt,undefined,'repeat token is not mutation authority');
  }
  assert.equal(f.snapshots.stats().entries,1);
  assert.equal(f.snapshots.resolve({projectPath:f.project,filePath:f.file,fileVersionReceipt:first.fileVersionReceipt,requestContext:owner}).ok,true);
});
test('a source range that cannot fit creates no mutation snapshot',async t=>{
  const f=fixture(t);f.write('x'.repeat(10000));
  const result=await f.read({byteBudget:2048});assert.equal(result.ok,false);assert.equal(f.snapshots.stats().entries,0);
});
test('cancellation during resolution starts no source observation or receipt',async t=>{
  const cancel=new AbortController(),f=fixture(t,{onResolve:()=>cancel.abort('test cancel')});
  const result=await f.read({},{signal:cancel.signal});
  assert.equal(result.ok,false);assert.equal(result.errorCode,'REQUEST_CANCELLED');assert.equal(f.snapshots.stats().entries,0);
});

test('cancellation after opening a byte reader closes it once and forbids retry',async t=>{
  const f=fixture(t),cancel=new AbortController(),fsp=fs.promises,originalOpen=fsp.open;
  let closes=0,reads=0;
  fsp.open=async function(...args){
    const handle=await originalOpen.apply(this,args);
    if(path.resolve(String(args[0]))===path.resolve(f.file)){
      const close=handle.close.bind(handle),read=handle.read.bind(handle);
      handle.close=async()=>{closes++;return close();};
      handle.read=async(...readArgs)=>{reads++;return read(...readArgs);};
      cancel.abort('cancel after open');
    }
    return handle;
  };
  try {
    const result=(await f.runtime.callTool('read_file',{path:'project://A.cpp',maxBytes:1024},{signal:cancel.signal})).structuredContent;
    assert.equal(result.errorCode,'REQUEST_CANCELLED');assert.equal(result.retry.allowed,false);
    assert.equal(closes,1);assert.equal(reads,0);assert.equal(f.snapshots.stats().entries,0);
  } finally {fsp.open=originalOpen;}
});
