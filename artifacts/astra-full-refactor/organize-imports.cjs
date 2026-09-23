const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../../lmstudio-context-compactor-plugin');
const ts=require(path.join(root,'node_modules/typescript'));
const config=ts.readConfigFile(path.join(root,'tsconfig.json'),ts.sys.readFile);
const parsed=ts.parseJsonConfigFileContent(config.config,ts.sys,root);
const host={getScriptFileNames:()=>parsed.fileNames,getScriptVersion:()=>"0",
  getScriptSnapshot:file=>fs.existsSync(file)?ts.ScriptSnapshot.fromString(fs.readFileSync(file,'utf8')):undefined,
  getCurrentDirectory:()=>root,getCompilationSettings:()=>parsed.options,
  getDefaultLibFileName:opts=>ts.getDefaultLibFilePath(opts),fileExists:ts.sys.fileExists,readFile:ts.sys.readFile,
  readDirectory:ts.sys.readDirectory};
const service=ts.createLanguageService(host);
const edits=parsed.fileNames.filter(f=>f.endsWith('.ts')).flatMap(fileName=>service.organizeImports({type:'file',fileName},{},{}));
for(const edit of edits){let text=fs.readFileSync(edit.fileName,'utf8'); for(const change of [...edit.textChanges].sort((a,b)=>b.span.start-a.span.start))text=text.slice(0,change.span.start)+change.newText+text.slice(change.span.start+change.span.length);fs.writeFileSync(edit.fileName,text);}
