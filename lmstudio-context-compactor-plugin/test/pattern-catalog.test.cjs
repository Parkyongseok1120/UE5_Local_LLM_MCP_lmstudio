"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),os=require("node:os"),path=require("node:path");
const {renderPatternCatalog,canonical}=require("../scripts/build-design-guidance.cjs");
const root=path.resolve(__dirname,"../../docs/model-guidance"),{catalog}=require("./helpers/architecture.cjs");
function fixture(fn){const temp=fs.mkdtempSync(path.join(os.tmpdir(),"pattern-catalog-"));try{for(const file of ["catalog.json","unreal-patterns.md"])fs.copyFileSync(path.join(root,file),path.join(temp,file));return fn(temp);}finally{fs.rmSync(temp,{recursive:true,force:true});}}
test("C01 duplicate IDs and missing sections fail generation",()=>fixture(temp=>{
  const value=JSON.parse(fs.readFileSync(path.join(temp,"catalog.json")));value.patterns.push(value.patterns[0]);fs.writeFileSync(path.join(temp,"catalog.json"),JSON.stringify(value));assert.throws(()=>renderPatternCatalog(temp),/Invalid architecture/);
  value.patterns.pop();value.patterns[0].section="missing";fs.writeFileSync(path.join(temp,"catalog.json"),JSON.stringify(value));assert.throws(()=>renderPatternCatalog(temp),/Missing/);
}));
test("C02 content hashes ignore OS path and newline spelling",()=>fixture(temp=>{
  const first=renderPatternCatalog(temp),file=path.join(temp,"unreal-patterns.md");fs.writeFileSync(file,fs.readFileSync(file,"utf8").replace(/\r?\n/g,"\r\n"));assert.deepEqual(renderPatternCatalog(temp),first);
}));
test("C03 semantic content and selection changes invalidate hashes",()=>fixture(temp=>{
  const first=renderPatternCatalog(temp),file=path.join(temp,"catalog.json"),value=JSON.parse(fs.readFileSync(file));value.patterns[0].facets.push("new_facet");fs.writeFileSync(file,JSON.stringify(value));assert.notEqual(renderPatternCatalog(temp).catalogHash,first.catalogHash);
}));
test("C04 each canonical card includes application, alternatives, lifetime and cost",()=>{
  assert.equal(catalog.patterns.length,16);for(const card of catalog.patterns)for(const field of ["적용:","비적용:","소유권:","연결과 수명:","비용과 검증:"])assert.ok(card.text.includes(field),card.patternId+field);
});
test("C05 six existing section contracts remain the section limit",()=>{
  const {REFERENCE_LIMITS}=require("../dist/reference-context");assert.equal(REFERENCE_LIMITS.sections,6);
  assert.ok(catalog.patterns.every(p=>p.sectionId.startsWith("unreal-patterns/")));
});
test("C06 concept cards do not certify API versions",()=>assert.ok(catalog.patterns.every(p=>p.applicability.concept==="unreal" && p.applicability.validatedApiVersions.length===0)));
test("C07 checked export and generated runtime share identical content",()=>{
  const exported=JSON.parse(fs.readFileSync(path.join(root,"generated-pattern-catalog.json"),"utf8"));assert.deepEqual(canonical(exported),canonical(catalog));assert.deepEqual(renderPatternCatalog(root),exported);
});
