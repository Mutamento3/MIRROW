import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync,existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const read=p=>readFileSync(join(root,p),'utf8');
function sources(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?sources(join(dir,e.name)):/\.(tsx?|java)$/.test(e.name)?[join(dir,e.name)]:[]);}
test('excluded module routes and consumers are absent from source',()=>{
 const forbidden=/\/api\/(?:intimacy-book|image-generation|wardrobe|qq|taobao|federated-rooms|group-chat|harem)|\bscp_daily\b|\bscp_display_cap\b|\bTHEATER_PORTAL_ID\b|\bmind_room\b/;
 for(const file of sources(join(root,'src')))assert.equal(forbidden.test(readFileSync(file,'utf8')),false,file);
});
test('room extension and focus ports have no network effects',()=>{
 for(const f of ['src/pages/LoungePage.tsx','src/hooks/useLoungeFocus.ts'])assert.doesNotMatch(read(f),/fetch\s*\(|new WebSocket|iframe|XMLHttpRequest/);
 assert.match(read('src/pages/LoungePage.tsx'),/当前未接入/);
 assert.match(read('src/pages/LoungePage.tsx'),/留言板扩展接口/);
});
test('source has no literal device addresses',()=>{
 for(const f of [...sources(join(root,'src')),...sources(join(root,'android/app/src/main/java'))])assert.doesNotMatch(readFileSync(f,'utf8'),/(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}/,f);
});
test('toy commands remain with empty private address defaults and honest failure',()=>{
 const s=read('android/app/src/main/java/com/mirrow/app/plugins/BLEController.java');
 assert.match(s,/XXD_MAC = ""/);assert.match(s,/DEFAULT__tianyanMac = ""/);
 assert.match(s,/device address is not configured/);assert.match(s,/public String tianyanApply/);
});
test('music, perception and attribution remain, private image assets do not',()=>{
 assert.ok(existsSync(join(root,'src/components/music/MusicHost.tsx')));
 assert.ok(existsSync(join(root,'android/app/src/main/java/com/mirrow/app/miband/MiBandRuntime.java')));
 assert.match(read('android/app/src/main/java/com/mirrow/app/miband/LICENSE-AionsHome.txt'),/MIT License/);
 assert.doesNotMatch(read('src/workshop/WorkshopScene.tsx'),/from ['"].*assets\//);
 assert.doesNotMatch(read('android/app/src/main/java/com/mirrow/app/MainActivity.java'),/TheaterExport/);
});
