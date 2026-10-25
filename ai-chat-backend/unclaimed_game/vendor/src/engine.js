import { Story } from 'inkjs';
import content from './story.json' with {type:'json'};
import legacy from './story-v5.json' with {type:'json'};
import {freshSequel,sequelView,sequelChoose} from './sequel.js';
import {sequelView as oldSequelView,sequelChoose as oldSequelChoose} from './sequel-v6.js';
export const VERSION='two-part-novel-7';
export const supportedVersion=v=>[VERSION,'two-part-routes-6','part-one-routes-5'].includes(v);
const keys=['chapter','day','power','parts','stay_ready','move_ready','delivery_route','cash','prep','business_used','held','discount','earned','spent','ending','selected_cost','overtime','rescue_job','notice_known','quotes_known','seven_known','rivet_known','mobile_known','common_work','stay_work','move_work','mobile_work','goal','outcome','mail_here','loan','truth','brew_known','preparation_closed','route','visit','mara_relay'];
function read(story){const lines=[];let title='',speaker='';while(story.canContinue){const t=story.Continue().trim();if(t)lines.push(...t.split('\u2029').map(x=>x.trim()).filter(Boolean));for(const tag of story.currentTags){if(tag.startsWith('title:'))title=tag.slice(6);if(tag.startsWith('speaker:'))speaker=tag.slice(8);}}return {lines,title,speaker};}
export function fresh(){const story=new Story(content);const scene=read(story);return {version:VERSION,revision:0,ink:story.state.ToJson(),scene,lastBatch:[scene],history:[],lastDelta:[]};}
function restore(s){if(!supportedVersion(s.version))throw Error('存档版本不匹配，请保留存档并联系作者');const story=new Story(s.version==='part-one-routes-5'?legacy:content);story.state.LoadJson(s.ink);return story;}
export function view(s){if(s.episode===2)return s.version==='two-part-routes-6'?{...oldSequelView(s),legacy:true}:sequelView(s);const story=restore(s);const stats=Object.fromEntries(keys.map(k=>[k,story.variablesState[k]]));stats.storage_capacity=[2,3].includes(stats.outcome)?3:6;return {episode:1,continuation:story.currentChoices.length===0&&[1,2,3].includes(stats.ending),legacy:s.version!==VERSION,version:s.version,revision:s.revision,scene:s.scene,stats,choices:story.currentChoices.map((c,i)=>({id:`${s.revision}:${i}`,text:c.text,kind:c.tags?.includes('continue')?'continue':'action'})),ended:story.currentChoices.length===0,history:s.history,lastDelta:s.lastDelta,board:board(stats)};}
export function choose(s,id){if(s.episode===2)return s.version==='two-part-routes-6'?oldSequelChoose(s,id):sequelChoose(s,id);const story=restore(s);const before=view(s);const c=before.choices.find(c=>c.id===id);if(!c)throw Error('选项已过期，请刷新当前进度');story.ChooseChoiceIndex(Number(id.split(':')[1]));const scene=read(story);const next={...s,revision:s.revision+1,ink:story.state.ToJson(),scene,lastBatch:[scene],history:[...s.history,{...s.scene,chosen:c.text}].slice(-100)};const after=view(next);const names={power:'电量',parts:'零件',cash:'现金',prep:'准备',held:'保管'};next.lastDelta=Object.entries(names).filter(([k])=>after.stats[k]!==before.stats[k]).map(([k,name])=>`${name} ${after.stats[k]-before.stats[k]>0?'+':''}${after.stats[k]-before.stats[k]}`);return next;}

export function chooseBatch(s,id){let next=choose(s,id);const scenes=[next.scene];for(let i=0;i<40;i++){const v=view(next);if(v.ended||v.choices.length!==1||v.choices[0].kind!=='continue'){next.lastBatch=scenes;return next;}next=choose(next,v.choices[0].id);scenes.push(next.scene);}throw Error('剧情自动推进超出安全范围');}
export function mcpView(s){const v=view(s);if(s.episode===2)return {version:s.version,episode:2,route:s.route,revision:s.revision,chapter:v.stats.chapter,resources:s.route==='move'?{}:{power:s.power},inheritance:v.inheritance,clues:v.clues,characters:v.characters,legacy:v.legacy||false,board:v.board,scenes:s.lastBatch||[v.scene],choices:v.choices,ended:v.ended};return {difficulty:'standard',episode:1,continuation:v.continuation,legacy:v.legacy,version:v.version,revision:v.revision,chapter:v.stats.chapter,day:v.stats.day,resources:{power:v.stats.power,parts:v.stats.parts,cash:v.stats.cash,stored:v.stats.held,storage_capacity:v.stats.storage_capacity,...(v.stats.quotes_known?{business_slots_left:3-v.stats.business_used}:{}),...(v.stats.day>=6?{emergency_work_left:v.stats.preparation_closed||v.stats.day>6||v.stats.overtime||v.stats.rescue_job?0:1}:{})},board:v.board,settlement:v.stats.selected_cost>0||v.ended?{paid:v.stats.selected_cost,cash_remaining:v.stats.cash}:null,scenes:s.lastBatch||[v.scene],choices:v.choices,ended:v.ended};}

export function board(s){
 const timeline=[{day:1,label:s.brew_known?'接待Brew':'接待来客'}];
 if(s.day>=2)timeline.push({day:2,label:'Brew带咖啡回来',done:true});
 if(s.notice_known){timeline.push({day:3,label:'结清旧账单250点',done:s.day>=3},{day:6,label:'店铺准备截止',done:s.day>=7},{day:7,label:'执行去向／旧线路停用',done:s.day>=7&&s.outcome>0});}
 if(s.quotes_known){for(const [i,day] of [2,4,6].entries())timeline.push({day,label:'店务时段',done:s.business_used>i});}
 if(s.mail_here)timeline.push({day:6,label:'Seven取回寄存盒',done:s.day>=6});
 if(s.rivet_known)timeline.push({day:5,label:'Rivet来取工具',done:s.day>=5});
 if(s.outcome===1)timeline.push({day:10,label:'旧店重新开门',done:s.day>=10});
 if(s.outcome===2)timeline.push({day:8,label:'河西新店开门',done:s.day>=8});
 if(s.outcome===3)timeline.push({day:8,label:'第一站交接',done:s.ending===3});
 timeline.sort((a,b)=>a.day-b.day);
 for(const e of timeline)e.status=e.done?'完成':e.day<s.day?'已过':e.day===s.day?'今天':'待办';
 const objective=s.brew_known===false?'听听这位客人遇到了什么':s.outcome>=4?'完成最后交接并保存记录':s.outcome>0?'把已经选择的生活真正开始':s.quotes_known?['尚未投入具体去向','正在准备留在旧街','正在准备搬到河西','正在准备移动送还'][s.goal]:s.notice_known?'弄清楚留下或搬走需要做什么':['','帮Brew赶上咖啡约定','为Seven找一个可靠地址','陪June决定自己的早晨','处理Mara收到的错误提醒','听Rivet讲工具和店的来历','决定店的下一步'][s.chapter];
 const closed=s.preparation_closed||s.day>6;const todayIndex=[2,4,6].indexOf(s.day);const todaySlot=s.quotes_known&&!closed&&todayIndex>=0&&s.business_used<=todayIndex?1:0;const focus=s.quotes_known?{closed,days_left:Math.max(0,6-s.day),today_slots:todaySlot,total_slots:closed?0:3-s.business_used,summary:closed?'准备已截止，按已完成的安排往下走':`今天剩余店务 ${todaySlot} 次 · ${s.day===6?'今天截止':'距截止还有 '+(6-s.day)+' 天'}`} :null;const data={objective,focus,timeline:timeline.map(({done,...e})=>e),work:[],plans:[],rules:[]};
 if(!s.quotes_known)return data;
 const work=[['common_work','清点寄存与主机检查'],['stay_work','接电勘验与施工安排'],['move_work','装箱与运输排期']];
 if(s.delivery_route)work.push(['mobile_work','途中补电与每站交接']);
 data.work=work.map(([key,label])=>({key,label,done:s[key]===1}));
 if(s.outcome>0){data.settlement={paid:s.selected_cost,remaining:s.cash};return data;}
 const overflow=Math.max(0,s.held-3)*80;
 const make=(id,label,cost,requirements,consequences)=>({id,label,cost,shortfall:Math.max(0,cost-s.cash),missing:requirements.filter(k=>s[k]!==1).map(k=>work.find(x=>x[0]===k)[1]),consequences,ready:cost<=s.cash&&requirements.every(k=>s[k]===1)});
 data.plans=[make('stay','留旧街',900-s.discount,['common_work','stay_work'],'停业三天；保留全部柜子；月费100'),make('move','河西新店',650-s.discount+overflow,['common_work','move_work'],`次日开门；三格柜位；月费160；熟客多走二十分钟${overflow?'；已含代管费'+overflow:''}`)];
 if(s.delivery_route)data.plans.push(make('mobile','移动送还',800-s.discount+overflow,['common_work','move_work','mobile_work'],`月租120另计行驶电量；三格柜位${overflow?'；已含代管费'+overflow:''}`));
 data.rules=['第2、4、6天各一个店务时段；阅读不耗时','通用检查可沿用；接电与搬迁工作不能互相代替','第2至5天每日自动补5电；第6天不补电','报价已含设备调试，不重复加收；最终只支付执行的一项'];
 if(s.loan)data.rules.push('借工具自行调试需2零件、15电和一次店务；完成通用检查，报价减200');
 if(s.day>=6)data.rules.push(s.preparation_closed||s.day>6?'准备已截止，不能再接单或补做':s.overtime||s.rescue_job?'今晚的应急工作已用完':'今晚最多补做一项工作（25电）或接急单（20电／180点）');
 return data;
}

export function enterSequel(s,route='continue'){const v=view(s);const continuing=route==='continue';if(continuing){if(s.episode===2||!v.ended||![1,2,3].includes(v.stats.ending))throw Error('请先完成一条继续营业的第一部结局');route=({1:'stay',2:'move',3:'mobile'})[v.stats.ending];}const fromCompleted=s.episode!==2&&v.ended&&[1,2,3].includes(v.stats.ending);const origin=fromCompleted?{demo:!continuing,source:'已完成第一部的实际选择',version:s.version,ending:v.stats.ending,brew:v.stats.route===1?'repair':'practice',seven:v.stats.mail_here?'shop':'workshop',june:v.stats.visit?'visit':'rest',mara:v.stats.mara_relay?'relay':'search',rivet:v.stats.loan?'loan':'gift'}:{demo:true,source:'独立试玩示例前情',brew:'repair',seven:'shop',june:'rest',mara:'search',rivet:'loan'};const next=freshSequel(route,VERSION,origin);next.revision=s.revision+1;return next;}
