// Synthetic unit-test inputs have explicit fixture authority. Production gets
// its registry from the host and never infers a provider from a payload name.
const {EvidenceManager}=require('../../dist/evidence-manager');
const {TOOL_CAPABILITIES}=require('../../dist/generated-tool-capability-data');
function ledgerView(messages) {
 const names=[...new Set(messages.flatMap(m=>m.getToolCallRequests().map(r=>r.name)))];
 const tools=names.map(name=>({name,pluginIdentifier:Object.keys(TOOL_CAPABILITIES).find(p=>TOOL_CAPABILITIES[p][name]),parametersJsonSchema:{properties:{}}}));
 const manager=new EvidenceManager(null,tools);manager.recordReturnedSources(messages,'fixture');
 return manager.returnedSources();
}
module.exports={ledgerView};
