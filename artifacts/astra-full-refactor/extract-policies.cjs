// Gate 1 mechanical extraction, using TypeScript symbols to preserve dependency edges.
const fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '../../lmstudio-context-compactor-plugin');
const ts = require(path.join(root, 'node_modules/typescript'));
const file = path.join(root, 'src/prediction-loop.ts');
const program = ts.createProgram([file], {target:ts.ScriptTarget.ES2022, module:ts.ModuleKind.CommonJS, allowJs:true, esModuleInterop:true});
const checker=program.getTypeChecker(), source=program.getSourceFile(file);
const entries=[], symbols=new Map();
function bindings(name) { return ts.isIdentifier(name) ? [name] : name.elements.flatMap(e=>e.name?bindings(e.name):[]); }
for(const node of source.statements) {
  if(ts.isImportDeclaration(node)) {
    const c=node.importClause;
    const names=[...(c?.name?[c.name]:[]), ...(c?.namedBindings && ts.isNamedImports(c.namedBindings)?c.namedBindings.elements.map(e=>e.name):[])];
    for(const id of names) symbols.set(checker.getSymbolAtLocation(id), {name:id.text,importNode:node});
    continue;
  }
  const names=ts.isVariableStatement(node)?node.declarationList.declarations.flatMap(d=>bindings(d.name)):node.name?[node.name]:[];
  const entry={node,names:names.map(n=>n.text),line:source.getLineAndCharacterOfPosition(node.getStart()).line+1};
  const l=entry.line;
  entry.module = l>=1967 ? 'prediction-loop'
    : l<102 ? (l===79?'execution-contracts':'context-ports')
    : l<257 ? 'evidence-telemetry'
    : l<360 ? 'execution-contracts'
    : l<430 ? 'execution-instructions'
    : l<507 ? 'tool-capability-registry'
    : l<513 ? 'execution-config'
    : l<531 ? 'evidence-telemetry'
    : l<547 ? 'execution-config'
    : l<601 ? 'runtime-identity'
    : l<653 ? 'execution-config'
    : l<674 ? 'evidence-telemetry'
    : l<894 ? 'context-manager'
    : l<1075 ? 'prediction-ui'
    : l<1233 ? 'raw-tool-intent'
    : l<1292 ? 'recovery-coordinator'
    : l<1616 ? 'evidence-manager'
    : l<1669 ? 'recovery-coordinator'
    : l<1726 ? 'delivery-controller'
    : l<1782 ? 'tool-boundary'
    : l<1875 ? 'delivery-controller'
    : 'context-manager';
  // Capability-related constants must have a single owner.
  if(entry.names.some(n=>['READ_ONLY_RECOVERY_TOOL_NAMES','isUnregisteredGitReadIntent','UNSAFE_UNKNOWN_NAME_PATTERN'].includes(n))) entry.module='tool-capability-registry';
  entries.push(entry);
  names.forEach(id=>symbols.set(checker.getSymbolAtLocation(id),{name:id.text,entry}));
}
const modules=[...new Set(entries.map(e=>e.module))];
for(const module of modules) {
 const own=entries.filter(e=>e.module===module), dependencies=new Map(), external=new Set();
 function visit(node) {
   if(ts.isIdentifier(node)) {
     let symbol=checker.getSymbolAtLocation(node);
     if(ts.isShorthandPropertyAssignment(node.parent)) symbol=checker.getShorthandAssignmentValueSymbol(node.parent)||symbol;
     const ref=symbols.get(symbol);
     if(ref?.importNode) external.add(ref.importNode);
     if(ref?.entry && ref.entry.module!==module) {
       if(!dependencies.has(ref.entry.module)) dependencies.set(ref.entry.module,new Set());
       const isType=ts.isTypeAliasDeclaration(ref.entry.node)||ts.isInterfaceDeclaration(ref.entry.node);
       dependencies.get(ref.entry.module).add((isType?'type ':'')+ref.name);
     }
   }
   ts.forEachChild(node,visit);
 }
 own.forEach(e=>visit(e.node));
 const imports=[...external].map(n=>n.getText(source)).concat([...dependencies].map(([m,names])=>`import { ${[...names].join(', ')} } from "./${m}";`));
 const body=own.map(e=>{
   let text=e.node.getFullText(source).trim();
   if(!e.node.modifiers?.some(m=>m.kind===ts.SyntaxKind.ExportKeyword)) {
     const offset=e.node.getStart()-e.node.getFullStart();
     const full=e.node.getFullText(source);
     text=(full.slice(0,offset)+'export '+full.slice(offset)).trim();
   }
   return text;
 }).join('\n\n');
 fs.writeFileSync(path.join(root,'src',module+'.ts'),imports.join('\n')+'\n\n'+body+'\n');
}
fs.writeFileSync(path.join(__dirname,'gate-1-symbol-map.json'),JSON.stringify(entries.map(({names,line,module})=>({names,line,module})),null,2)+'\n');
