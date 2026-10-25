/* Real embedded roster HTML/JS, fake in-memory replies only. No Keys/DB/network. */
const http = require('node:http'), fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '../../static/lounge');
let saved = false;
const site = {id:'fixture-site',name:'测试共域',origin:'https://fixture.example',has_human_key:true,has_ai_key:true};
http.createServer(async (req,res) => {
  const url = new URL(req.url,'http://127.0.0.1:8979');
  res.setHeader('Cache-Control','no-store');
  if (url.pathname === '/') {
    let html = fs.readFileSync(path.join(root,'lounge-friends.html'),'utf8');
    html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
    html = html.replace('</body>','<script src="/static/lounge/social-sites.js"></script><script>document.getElementById("socialContactsDialog").showModal();</script></body>');
    res.setHeader('Content-Type','text/html;charset=utf-8');res.end(html);return;
  }
  if (url.pathname.startsWith('/static/lounge/')) {
    const name = path.basename(url.pathname);
    if (['social-sites.js','common.css','lounge-friends.css','mirrow.css'].includes(name)) {
      res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':'text/css');
      res.end(fs.readFileSync(path.join(root,name)));return;
    }
    res.statusCode=404;res.end();return;
  }
  for await (const _chunk of req) {} // Consume, never persist or print the fake fields.
  let data = {};
  if (url.pathname === '/api/social-sites' && req.method === 'GET') data = {sites:saved?[site]:[]};
  else if (url.pathname === '/api/social-sites' && req.method === 'POST') {saved=true;data={site};}
  else if (url.pathname.endsWith('/lounge-imports')) data={friends:[]};
  else if (url.pathname.endsWith('/test')) data={identities:{human:{status:'connected'},ai:{status:'connected'}}};
  else if (url.pathname.endsWith('/profile-sync')) data={profiles:{human:{status:'linked'},ai:{status:'linked'}}};
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
}).listen(8979,'127.0.0.1',()=>process.stdout.write('Isolated roster UI fixture ready\n'));
