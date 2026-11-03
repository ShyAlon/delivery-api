// Link real ES modules without evaluating DOM or fetching any network resource.
import {SourceTextModule} from 'node:vm';
import {readFileSync,readdirSync} from 'node:fs';
import {resolve,dirname,relative} from 'node:path';
const root=resolve(process.argv[2]||'src/delivery_api');
const entries=['web','backoffice_web'].flatMap(dir=>readdirSync(resolve(root,dir)).filter(n=>n.endsWith('.js')).map(n=>resolve(root,dir,n)));
for(const entry of entries){
 const cache=new Map();
 function load(path){
  if(!path.startsWith(root+'/'))throw Error('Module escapes asset root: '+path);
  if(!cache.has(path))cache.set(path,new SourceTextModule(readFileSync(path,'utf8'),{identifier:path}));
  return cache.get(path);
 }
 await load(entry).link((specifier,parent)=>{
  const path=specifier==='preact'?resolve(root,'web/vendor/preact.module.js'):
   specifier.startsWith('/assets/')?resolve(root,'web',specifier.slice(8)):
   specifier.startsWith('/backoffice-assets/')?resolve(root,'backoffice_web',specifier.slice(19)):
   specifier.startsWith('.')?resolve(dirname(parent.identifier),specifier):null;
  if(!path)throw Error('Unsupported import '+specifier+' in '+relative(root,parent.identifier));
  return load(path);
 });
}
console.log('Linked '+entries.length+' browser modules: all imports resolve.');
