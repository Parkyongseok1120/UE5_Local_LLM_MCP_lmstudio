"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),os=require("node:os"),path=require("node:path"),crypto=require("node:crypto");
const {createDirectRuntime}=require("../src/direct-server");
const {toolDefinitions}=require("../src/direct-tool-catalog");
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),"occurrences-v4-")),projectPath=path.join(root,"Fixture.uproject");
 const target=path.join(root,"Source","Fixture.cpp");
 fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(projectPath,'{"FileVersion":3}\n');fs.writeFileSync(target,"int Value = 1;\n");
 t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const runtime=createDirectRuntime({workspaceRoot:root,stateRoot:path.join(root,"state"),configPath:path.join(root,"agent.json"),
 env:{ALLOW_WRITE:"1",AGENT_STATE_ROOT:path.join(root,"state")},getActiveProject:()=>projectPath,
 validateMutationSemanticText:()=>({ok:true,hits:[]})});
 return {runtime,target,patch:{path:"project://Source/Fixture.cpp",oldText:"Value = 1",newText:"Value = 2",
 expectedHash:crypto.createHash("sha256").update("int Value = 1;\n").digest("hex")}};
}
for(const name of ["replace_in_file","apply_edit_bundle"])test(name+" without expectedOccurrences succeeds",async t=>{
 const {runtime,target,patch}=fixture(t);
 const result=(await runtime.callTool(name,name==="apply_edit_bundle"?{patches:[patch]}:patch)).structuredContent;
 assert.equal(result.ok,true,JSON.stringify(result));assert.equal(fs.readFileSync(target,"utf8"),"int Value = 2;\n");
});
test("expectedOccurrences 2 is rejected with the new message",async t=>{
 const {runtime,target,patch}=fixture(t);
 const result=(await runtime.callTool("replace_in_file",{...patch,expectedOccurrences:2})).structuredContent;
 assert.equal(result.ok,false);assert.match(JSON.stringify(result),/omit it to use 1/);
 assert.equal(fs.readFileSync(target,"utf8"),"int Value = 1;\n");
});
test("catalog schema: expectedOccurrences has default 1 and is not required",()=>{
 const schema=toolDefinitions().find(t=>t.name==="replace_in_file").inputSchema;
 assert.equal(schema.properties.expectedOccurrences.default,1);
 assert.ok(schema.oneOf.every(branch=>!branch.required?.includes("expectedOccurrences")));
});
