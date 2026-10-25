// Private subprocess adapter, not a server or production authentication layer.
// No socket, identity header, tool registration, notification or LLM calls.
import fs from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {load, act} from './vendor/src/store.js';
import {mcpView} from './vendor/src/engine.js';

let sql;
let result;
try {
  let input='';
  for await (const chunk of process.stdin) { input+=chunk; if(input.length>65536) throw Error('invalid_request'); }
  const {name,arguments:args,namespace,database}=JSON.parse(input);
  const required={get_game_state:[],choose_game_action:['choice_id','revision'],
    restart_game:['confirmation','revision'],start_next_story:['confirmation','revision','route']}[name];
  if(!required||!args||Array.isArray(args)||typeof args!=='object'
    ||JSON.stringify(Object.keys(args).sort())!==JSON.stringify([...required].sort())
    ||!/^[a-f0-9]{64}$/.test(namespace)||typeof database!=='string') throw Error('invalid_request');
  if(name!=='get_game_state'&&(!Number.isSafeInteger(args.revision)||args.revision<0)) throw Error('invalid_request');
  if(name==='choose_game_action'&&(typeof args.choice_id!=='string'||!args.choice_id||args.choice_id.length>256)) throw Error('invalid_request');
  if(['restart_game','start_next_story'].includes(name)&&args.confirmation!==true) throw Error('confirmation_required');
  if(name==='start_next_story'&&!['continue','stay','move','mobile'].includes(args.route)) throw Error('invalid_request');
  sql=new DatabaseSync(database);
  sql.exec('PRAGMA busy_timeout=5000');
  sql.exec('BEGIN IMMEDIATE');
  sql.exec(fs.readFileSync(new URL('./vendor/drizzle/0000_lovely_quentin_quire.sql',import.meta.url),'utf8')
    .replaceAll('CREATE TABLE','CREATE TABLE IF NOT EXISTS'));
  const DB={
    async batch(statements) {
      sql.exec('SAVEPOINT vendor_batch');
      try {const values=[];for(const s of statements)values.push(await s.run());sql.exec('RELEASE vendor_batch');return values;}
      catch(e){sql.exec('ROLLBACK TO vendor_batch');sql.exec('RELEASE vendor_batch');throw e;}
    },
    prepare(query) {return {bind(...args) {return {
      async first(){return sql.prepare(query).get(...args);},
      async all(){return {results:sql.prepare(query).all(...args)};},
      async run(){return {meta:{changes:sql.prepare(query).run(...args).changes}};},
    };}};},
  };
  let state;
  if(name==='get_game_state')state=await load(DB,namespace);
  else state=await act(DB,namespace,{revision:args.revision,choice:args.choice_id,
    confirm:args.confirmation,route:args.route},
    name==='restart_game'?'restart':name==='start_next_story'?'sequel':'choose',name==='choose_game_action');
  result={ok:true,state:mcpView(state)};
  sql.exec('COMMIT');
} catch(e) {
  if(sql){try{sql.exec('ROLLBACK');}catch{}}
  const error=e.status===409?'conflict':
    ['invalid_request','confirmation_required'].includes(e.message)?e.message:
    e.message?.includes('需要')&&e.message?.includes('确认')?'confirmation_required':
    e.message?.includes('没有找到已完成')||e.message?.includes('请先完成')?'continuation_unavailable':
    e.message?.includes('选项')?'invalid_action':'storage_error';
  result={ok:false,error};
} finally {sql?.close();}
// Never print exceptions, paths, save payloads, identity or hidden story state.
process.stdout.write(JSON.stringify(result));
