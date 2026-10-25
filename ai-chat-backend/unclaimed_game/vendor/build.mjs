import fs from 'node:fs';
import { Compiler } from 'inkjs/compiler/Compiler';
import { build } from 'esbuild';
const errors=[];
const c=new Compiler(fs.readFileSync('src/story.ink','utf8'),{errorHandler:(m,t)=>{if(t===2)errors.push(m);else console.log(m)}});
const story=c.Compile();
if(errors.length)throw Error(errors.join('\n'));
fs.writeFileSync('src/story.json',story.ToJson());
await build({entryPoints:['src/worker.js'],bundle:true,format:'esm',platform:'browser',target:'es2022',outfile:'dist/server/index.js',loader:{'.html':'text'}});
