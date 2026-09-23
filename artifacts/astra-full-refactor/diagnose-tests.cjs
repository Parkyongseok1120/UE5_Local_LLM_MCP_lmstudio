const Module=require('node:module');
const old=Module._load;
Module._load=function(name,...args){
 const loaded=old.call(this,name,...args);
 if(name!=='node:test') return loaded;
 const wrapped=function(title,...rest){
   const i=rest.findIndex(x=>typeof x==='function');
   if(i>=0){const fn=rest[i];rest[i]=async(...a)=>{process.stderr.write('START '+title+'\n'); try {return await fn(...a)}finally{process.stderr.write('END '+title+'\n')}};}
   return loaded(title,...rest);
 };
 return Object.assign(wrapped,loaded);
};
require('../../lmstudio-context-compactor-plugin/test/prediction-loop.test.cjs');
