const test=require('node:test'),assert=require('node:assert/strict');
const {Chat,ChatMessage}=require('@lmstudio/sdk');
const {WorkingContext}=require('../dist/working-context');
const {fileSourceKey}=require('../dist/evidence-identity');
function read(history,id,path,project='A') {
 const value={ok:true,path,canonicalProject:project,sha256:'a'.repeat(64),startLine:1,endLine:2,totalLines:2,content:'void First() {}\nvoid Second() {}'};
 history.append(ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id,name:'read_file',arguments:{path,project}}}]}));
 history.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify(value)}]}));
 return {index:history.length-1,size:JSON.stringify(value).length,key:fileSourceKey({...value,provider:'mcp/unreal-agent'})};
}
const wc=()=>new WorkingContext({conversation:'v3-pins',lineage:'v3-'+Math.random(),repository:'fixture',workspace:'fixture'});
const meta={toolProviders:{read_file:'mcp/unreal-agent'}};
test('v3 a mutation target from the prior checkpoint precedes current recency',()=>{
 const w=wc();try{
  const h=Chat.from([{role:'system',content:'[Direct continuity state v2]\n'+JSON.stringify({currentTurnFindings:{forUserTurn:'a'.repeat(64),items:['Target [found]: Source/Target.cpp:1 needs a fix']}})},{role:'user',content:'Review'}]);
  const target=read(h,'old','Source/Target.cpp');h.append('assistant','Audit finished.');h.append('user','수정 진행해봐');
  read(h,'new','Source/Recent.cpp');
  const pins=w.pinnedResultIndexes(h,target.size+10,{...meta,objectiveKind:'mutate'});
  assert.deepEqual(pins,[target.index]);
 }finally{w.close();}
});
test('v3 current found target precedes unresolved claims and quote recency',()=>{
 const w=wc();try{
  const h=Chat.from([{role:'user',content:'Fix'}]);const target=read(h,'target','Source/Target.cpp');read(h,'other','Source/Other.cpp');h.append('assistant','Source/Other.cpp:1 is quoted.');
  assert.deepEqual(w.pinnedResultIndexes(h,target.size+10,{...meta,objectiveKind:'mutate',mutationTargets:[{path:'Source/Target.cpp',sourceKey:target.key}],unresolvedClaims:[{location:'Source/Other.cpp:1'}]}),[target.index]);
 }finally{w.close();}
});
test('v3 equal paths from different projects are independent pin keys',()=>{
 const w=wc();try{
  const h=Chat.from([{role:'user',content:'Review'}]);const a=read(h,'a','Source/Same.cpp','A'),b=read(h,'b','Source/Same.cpp','B');
  assert.notEqual(a.key,b.key);
  assert.deepEqual(new Set(w.pinnedResultIndexes(h,10000,meta)),new Set([a.index,b.index]));
 }finally{w.close();}
});

test('v3 unresolved claim pins its span or referenced page before newer same-file pages',()=>{
 for(const byRef of [false,true]) {
  const w=wc();try{
   const h=Chat.from([{role:'user',content:'Review'}]);
   const page=(id,line)=>{
    const value={ok:true,path:'Source/Same.cpp',canonicalProject:'A',sha256:'a'.repeat(64),startLine:line,endLine:line,totalLines:200,content:'void Same() {}'};
    h.append(ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id,name:'read_file',arguments:{path:value.path}}}]}));
    h.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify(value)}]}));
    return {index:h.length-1,size:JSON.stringify(value).length};
   };
   const target=page('important',1);page('recent',100);
   const claim={path:'Source/Same.cpp',sha256:'A'.repeat(64),statement:byRef?'Needs investigation':'Source/Same.cpp:1 needs investigation',refs:['tool-call:important']};
   assert.deepEqual(w.pinnedResultIndexes(h,target.size+10,{...meta,unresolvedClaims:[claim]}),[target.index]);
  }finally{w.close();}
 }
});

test('v3 execution targets survive current-turn checkpoint replacement and canonical path forms',()=>{
 const {mutationTargetsFromHistory}=require('../dist/working-context');
 for(const path of ['project://Source/Target.cpp','engine://Source/Target.cpp','C:/Source/Target.cpp']) {
  const w=wc();try{
   const h=Chat.from([{role:'system',content:'[Direct continuity state v2]\n'+JSON.stringify({currentTurnFindings:{forUserTurn:'a'.repeat(64),items:[`Target [found]: ${path}:1 needs a fix`]}})},{role:'user',content:'Review'}]);
   const target=read(h,'old',path);h.append('user','Fix');read(h,'new','Source/Recent.cpp');
   const mutationTargets=mutationTargetsFromHistory(h);
   assert.deepEqual(w.pinnedResultIndexes(h,target.size+10,{...meta,objectiveKind:'mutate'}),[target.index]);
   const rewritten=Chat.empty();for(const message of h.getMessagesArray().slice(1))rewritten.append(message);
   rewritten.append('system','[Direct continuity state v2]\n'+JSON.stringify({currentTurnFindings:{forUserTurn:'b'.repeat(64),items:[]}}));
   assert.deepEqual(w.pinnedResultIndexes(rewritten,target.size+10,{...meta,objectiveKind:'mutate',mutationTargets}),[target.index-1]);
  }finally{w.close();}
 }
});
