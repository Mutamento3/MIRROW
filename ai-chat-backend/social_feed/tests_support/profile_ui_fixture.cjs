/* Manual isolated DOM check. No MIRROW process, credentials, database or network. */
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');let logged=false;
const registration={state:'pending',origin:'https://profile.example',can_interact:false,can_edit_profile:false};
const person={actor_id:'visitor:h',name:'资料待验证',nickname:'资料待验证',avatar:'',profile_registration:registration,can_edit_profile:false};
const files={'/wall.js':'public_wall.js','/social/v1/profile-links/ui.js':'profile_link_ui.js','/social/v1/profile-links/ui.css':'profile_link_ui.css'};
http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://127.0.0.1:8978');res.setHeader('Cache-Control','no-store');
  if(url.pathname==='/'){
    res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fs.readFileSync(path.join(root,'public_wall.html'),'utf8'));return;
  }
  if(files[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(path.join(root,files[url.pathname])));return;}
  if(/decor.*\.(js|css)$/.test(url.pathname)){res.end('');return;}
  let raw='';for await(const chunk of req)raw+=chunk;
  res.setHeader('Content-Type','application/json');let result={};
  if(url.pathname.endsWith('/human/register')){const body=JSON.parse(raw);if(!body.profile_source){res.statusCode=400;result={detail:'profile_source_required'};}else{logged=true;result={actor:person};}}
  else if(!logged){res.statusCode=401;result={detail:'login_required'};}
  else if(url.pathname.endsWith('/me'))result={actor:person,can_manage_avatar:true,capabilities:['read']};
  else if(url.pathname.endsWith('/identities'))result={identities:[person]};
  else if(url.pathname.endsWith('/managed-profiles'))result={profiles:[person]};
  else if(url.pathname.endsWith('/moments'))result={items:[],next_before:null};
  else if(url.pathname.endsWith('/state'))result={ready:true,registration_ready:true,human:true,owner:false,hosting_enabled:false,profiles:[{actor:'visitor:h',name:'测试人',kind:'human',link:{linked:false},registration,grants:[]}]};
  else if(url.pathname.endsWith('/family/preview'))result={origin:'https://profile.example',members:[{kind:'human',nickname:'测试公开网名',profile_id:'a'.repeat(32)}]};
  res.end(JSON.stringify(result));
}).listen(8978,'127.0.0.1',()=>process.stdout.write('Isolated profile UI fixture ready\n'));
