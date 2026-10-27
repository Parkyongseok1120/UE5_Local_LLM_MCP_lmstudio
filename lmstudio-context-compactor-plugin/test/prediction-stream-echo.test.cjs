'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {DeliveredPrefixEchoFilter}=require('../dist/prediction-stream');
function run(prefix,text,fragmentSize) {
 const filter=new DeliveredPrefixEchoFilter(prefix);let output='';
 for(let i=0;i<text.length;i+=fragmentSize)output+=filter.push(text.slice(i,i+fragmentSize));
 return {output:output+filter.finish(),suppressed:filter.duplicateSuppressed,chars:filter.suppressedChars};
}
for(const size of [1,3,1000])test(`delivered prefix filtering preserves deltas and the 90% boundary (fragments=${size})`,()=>{
 const prefix='abcdefghij';
 assert.deepEqual(run(prefix,'abcdefghi',size),{output:'',suppressed:true,chars:9});
 assert.deepEqual(run(prefix,'abcdefgh',size),{output:'abcdefgh',suppressed:false,chars:0});
 assert.equal(run(prefix,'abcdefghX',size).output,'abcdefghX');
 assert.equal(run(prefix,'abcdefghiX',size).output,'X');
 assert.equal(run(prefix,prefix+'\n추가 결과',size).output,'\n추가 결과');
 assert.equal(run('  보고 A\n결론 B  ','\n보고   A\t결론 B\n\n추가😀',size).output,'\n\n추가😀');
 assert.equal(run('123456789보고 😀','123456789보고 😃',size).output,'😃');
 assert.equal(run('','ordinary output',size).output,'ordinary output');
 assert.equal(run(prefix,'unrelated response',size).output,'unrelated response');
});
