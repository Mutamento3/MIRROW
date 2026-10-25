// Authored event fiction. All outcomes are deterministic; no runtime text generation.
const scenes={};
const scene=(id,title,chapter,lines,choices,objective)=>scenes[id]={title,chapter,lines,choices,objective};
const c=(text,to,cost=0,effect={},when=()=>true)=>({text,to,cost,effect,when});
const evalValue=(v,s)=>typeof v==='function'?v(s):v;
const routeName={stay:'旧街留店',move:'河西新店',mobile:'巡回送还'};
scene('home','河西的第二声门铃',1,[
'搬来河西一个月，Seven还是习惯往旧街的方向转。它在门口承认绕了路，把一张新画的路线图压在柜台上。Brew在图上补了一个咖啡杯：“这次总不会找错了。”',
'转换器的接头松了。河西修理师给它换上合适的转接件，机器重新亮起来。旧维修铺已经结束营业，你没有赶上那几天的交接，也没有从这次普通维修里得到赠礼者的消息。',
'开店时，你在门边写过“欢迎随时来坐坐”。新邻居因此问能不能把周末的读书会放在这里。Brew也想在窗边试摆咖啡摊。窗边只有一张长桌，他们等你决定第一次怎么安排。'
],[c('先办一次读书会，和 Brew 约下周试摊','home2',0,{homeUse:'read'}),c('先让 Brew 试摊，给读书会另约一个晚上','home2',0,{homeUse:'coffee'})],'给新生活安排第一次相聚');
scene('home2','这次，不替别人答应',1,[s=>s.flags.homeUse==='read'?'你给第一场读书会留了一个小时，借用了Brew的杯子，也答应让它会后取走。大家却比约定多聊了半小时。Brew按时来了，发现杯子还没收，站在门边没进去。你关掉“欢迎随时来”的滚动字幕，重新写下预约时间。':'Brew把三只杯子碰倒了一只。原本另约晚上的读书会邻居路过，看到门上“欢迎随时来”的字，就进来想坐一会儿。它绕过水迹，轻声问：“以后这里是不是总要给咖啡让路？”你把两边的预约表放到同一张桌上。',
'“不是谁更重要。”你说，“是我只有这么大的地方，也会安排不好。”两边的人第一次一起商量下一周。有人不满意那个时间，决定这次先不来。',
'June发来一张日出照片，问你有没有想看的景色。你翻了翻店里的预约，发现自己一直只给别人留时间。'
],[c('留出一个不营业的早晨，和 June 去看河边','home3',0,{homeMorning:true}),c('约 June 打烊后来，先一起从新窗户看夜景','home3',0,{homeMorning:false})],'给自己也留一点时间');
scene('home3','寄到这里的信',2,[
'Seven终于有了自己的收件地址。它把写好的卡片递给你，名字下面是一间很小的房号。“不大。”它说完，又把背面翻过来，“但窗户朝东。”',
'Rivet带来一把给新柜子用的扳手，这次写清楚是借两周。Brew把糖放在固定的抽屉里，再也不用每次找。你不知道转换器最初是谁送的，仍然好好擦它的接口。',
s=>s.flags.homeMorning?'你和June约好的那天，店门上挂着“午后回来”。没有谁因此丢下你。回来时，门缝里塞着Brew画得很歪的一轮太阳。':'那晚June带来一盏小灯。你们没赶日出，只看着河对面的窗口一盏盏暗下来。没有要立刻回答的问题。'
],[c('把新地址抄进寄存簿，接待明天的客人','home_end',0,{})],'把平常的日子继续过下去');
scene('home_end','HE · 一盏灯的日常',2,[
'冬天到来前，你记住了新街上每一位常客的门铃节奏，也遇到了几位不会再来的客人。生活并不总顺利，但这些小事已经值得你认真过。',
'Brew给转换器倒进咖啡，问今天要不要糖。你让它先等等，尝了第一口。',
'“今天这样就好。”你说。门外有人问明天开不开门，你翻开自己的日历，给出一个你愿意遵守的时间。',
'关于过去，仍有一个没有解开的秘密。眼前这份幸福没有因此少掉什么。'
],[],'河西支线完结');
scene('stay_open','门上那张暂停营业的纸',1,[
'Rivet检查旧主机时，发现记忆接口需要专用工位才能拆检。它指着你以前留下的抢修单：“和这次新接的电路无关。你可以先继续营业，也可以把检查约起来。”你想知道的那些空白，终于有了一个可以问的人。',
'预约确认以后，Brew来问你哪天回来。你差点照旧说“很快”，又把检修师写的“可能需要两天”读了一遍。旧店已经重新开门，这次暂时离开仍然需要安排。',
'你把转换器擦干净。门上那张暂停营业的纸还没贴，Rivet等你告诉它，哪些事可以帮，哪些事要留给你自己。'
],[c('只委托 Rivet 办理约好的取件，不替我接新单','arrival',0,{shopCare:'rivet'}),c('暂停全部接待，通知顾客另约；请 Rivet 只检查安全','arrival',0,{shopCare:'closed'})],'安排暂离，不再随口保证自己一定随时在场');
scene('mobile_open','收件人换了地址',1,[
'这一站的收件人叫Tern。它的旧门牌还在，房间却已住进另一位机器人。你没有把东西交给新住户，拨通运单上原先约好的电话。',
'“我在检修站。”Tern说，“腿还没修好。你能再送一小段吗？”它发来的维修凭条上，有你在转换器里见过的旧铺标记。你回拨维修师，确认这里也能检查自己的旧接口。',
'新住户从门内拿出一把折叠拖车：“是Tern借给我的，正好一起还。”巷口的送货员则提醒，去检修站的高处货道比地图上那条路更好走。你还有时间认真确认一项。'
],[c('检查拖车并带去归还，记好借还关系','arrival',0,{dolly:true}),c('先向送货员核实高处货道与接应入口','arrival',0,{localMap:true})],'履行送还的约定，再决定怎样接近自己的线索');
scene('arrival','一次约好归来的检修',1,[s=>s.route==='stay'?
'留在旧街一个月，你已经按新接电方案恢复营业。这次去检修站，是为了检查旧主机的记忆接口，不是再一次搬家。Rivet按你留下的委托照看店铺，寄存柜和顾客物品都留在原处。你与它约好明晚回来。':
'巡回一个月，你先把物品交到Tern手里，收好签收回执，再去完成自己的检查。即使最终查不到过去，这趟送还也已经履行。Tern还在等修腿，你们暂时都留在检修站。',
'你的意识运行在唯一的主机上，外出身体只是与你保持短距离连接的手脚。检查必须带原机：主机已装进有备用电池的防护箱，固定在运输车上。安全停机可以再启动；进水毁坏主板，却可能再也醒不过来。',
'今天只有普通降雨预报。检修站有高层出口，公共救援也正常值勤。Seven让你报平安，你给它留了地址。车上的行李位还够带一份额外用品。'
],[c('带密封修补包，保护主机箱接缝','reunion',0,{seal:true}),c('带独立短波电台，离开车后仍能呼叫 Seven','reunion',0,{radio:true})],'带着主机完成检查，并按约回来');
scene('reunion','它叫出了你的旧称呼',1,[
'师傅掀开转换器外壳，指着内侧的改装编号：“这是Lark订的。它当时说，另一个朋友总在听别人聊味道，也该尝尝。”这是一份普通订货记录，没有暗号。',
'门口一台LR-8/042放下零件箱。“你还在用。”它说。你转过镜头，它补了一句：“我是Lark。我们以前，常在你打烊以后待一会儿。”',
'抢修记录解释了那些空白：当年的主机故障很严重，运行知识和操作习惯恢复了，部分旧经历没能完整迁移。Rivet知道这场抢修，却不在你们那些夜晚的谈话里。',
'“修好以后，我来过。”Lark看着你，“你问我，需要寄存什么。”它当时没有说出自己的名字，也没有再来。你第一次知道，自己错过的见面并不只发生在故障之前。'
],[c('问它那天为什么没有留下来，先听完它的话','manifest',0,{relationship:'listen'}),c('告诉它我愿意认识你，但先不要把我当作从前的我','manifest',0,{relationship:'boundary'})],'弄清过去，同时说出现在的感受');
scene('manifest','运输名单上没有它们',2,[s=>s.flags.relationship==='listen'?'“我害怕再讲一遍，你也只是客气地听。”Lark说。你问：“那你有没有想过，我是真的不知道该问谁？”它低下头：“没有。那时我只顾着难受。”':'Lark下意识说你以前不喜欢这么直接。你没有接话。过了一会儿，它改口：“那我重新问。今天检查完，你愿意和我聊一会儿吗？”',
'楼下传来推车声。白港回收接手了即将停用的检修站。现场负责人WARD-6把运输清单递给维修师，四台昂贵设备被圈了出来，两台仍在等修腿的机器人却被标成废件。',
'“没有人付维修费。”WARD-6说。坐在推车上的Pip抬起头：“费用还在商量。不是说今天会接我们走吗？”另一台Tern把自己的取件单举起来，清单上没有它的名字。',
'师傅要求更正。WARD-6说车只按已核准的清单装载，把笔夹回胸前。你还来得及保留一份现场记录。'
],[c('拍下名单和它们的自述 · 电量 −8，保留可核实证据','flood',8,{evidence:true}),c('请维修师与两位当事人一起留下姓名和经过 · 不耗额外电量','flood',0,{witness:true})],'留下事实，别让当事人被当作无主物品');
scene('flood','水比预报来得快',2,[
'上游闸口的故障警报晚到了十分钟。维修师推开楼梯间的门，水已经沿着台阶涌进来。地面出口封了，供电切断；外界救援确认收到站点位置，但道路积水，暂时到不了。',
s=>s.route==='stay'?'Rivet从旧店发来最后一条网络消息：“寄存柜没事，我在。别急着赶回来。”你的主机箱已从车上推入检查室，现在必须带它去高处。':'你从窗边看见运输车的轮胎没进水里。车里的主机箱已经提前卸进检查室，能够单独运走；车暂时开不出去了。你记得来时经过一条高处货运通道。',
s=>'停电后主机与外出身体共用应急电池。面板显示的是可支配的剩余电量，不会因阅读文字下降。Lark推来绝缘小车。现在每条路的消耗都会提前说清。'+(s.flags.dolly?'Tern让你继续借用刚还回的拖车，最后上坡能省4电。':s.flags.localMap?'你提前问清的高处入口可以直接报给救援端，最后信标联络能省4电。':'' )
],[c('走较高的楼内坡道 · 电量 −18，主机箱不涉水','rescue',18,{path:'high'}),c(s=>s.flags.seal?'密封后走短通道 · 电量 −8，主机箱防水完好':'走短通道 · 电量 −8；未带密封包，箱体会渗水，后续必须预留12电修补','rescue',8,s=>({path:'low',wet:!s.flags.seal}))],'先把主机和待修者转移到高处');
scene('rescue','两辆车，一段楼梯',2,[
'Lark扶住主机箱。Pip与Tern都无法自行走完楼梯，一辆维修推车只能安全装下一台。它们不是缺少开机命令，是腿真的坏了。',
'“别把我记成已经送走了。”Tern说。你把它的姓名从“废件”旁边重新抄到转运栏。',
s=>s.flags.radio?'电台能直接联系高处的Seven。它正在联络公共救援，可以带人从货运平台接应。':'墙边还有救援用的有线呼叫器，可以确认位置并要求接应，但需要你绕去重新接线。'
],[c('与 Lark 分两趟搬运，两位都跟在身边 · 电量 −22','ward',22,{together:true}),c(s=>s.flags.radio?'先呼叫 Seven，把一位交给高处接应员 · 电量 −10':'接通救援呼叫器，再分段转交一位 · 电量 −16','ward',s=>s.flags.radio?10:16,{signal:true,together:false})],'安排能够落实的搬运与接应');
scene('ward','空位留给谁',3,[
'白港的货车停在高处平台。车厢里空着一截，WARD-6却伸手拦住推车：“这些不在转运范围。”',
'“那一段明明空着。”Lark说。“留给设备的。”WARD-6回答。维修师指着Pip：“它已经告诉你它的名字了。”',
s=>'WARD-6不是门的主人，但它正用货箱堵住最宽的通道，催工人先抢运贵重设备。你可以让外面的人立即看见这里，也可以先取得临时通行，把详细追责留到安全以后。'+(s.flags.wet?'箱体已渗水，离开这里后必须预留12电修补；现在不能拿到平台的应急电池。':'')
],[c(s=>s.flags.evidence?'把名单发给已经联系上的救援端 · 电量 −12，要求现场让路':'连线让当事人与救援端直接说话 · 电量 −14，要求现场让路','seal',s=>s.flags.evidence?12:14,{signal:true,exposed:true}),c('登记主机、Pip、Tern三台离站；Lark与维修师按工作人员通行 · 电量 −4；离站后仍可作证','seal',4,{release:true})],'让对方承担可见责任，先争取通行');
scene('seal','不能再把它当普通箱子',3,[
s=>s.flags.exposed?'救援调度的声音从通讯器里传出，要求平台值班员先腾出通道。队伍仍在积水另一侧，这次是远程协调。工人开始移开货箱，WARD-6想把名单收回，维修师按住了纸页。':'维修师把主机、Pip、Tern的临时离站登记传给平台值班员，Lark和自己则按工作人员通行。值班员回话：先让开通道，争议留档。工人这才挪开货箱。WARD-6要求记下你们的编号，已经无法再以清单缺项堵住唯一的路。',
s=>s.flags.wet?'主机箱侧面亮起渗水报警。Lark摸到接缝里的水：“现在还能修。再上那段露天坡道，水可能进到主板。”它把检查结果直接显示给你，没有替你决定。':'主机箱的防水指示仍然正常。Lark检查完接缝，给箱子垫上干布。你看见它手掌擦伤了，却还没告诉你。',
'你们经过一间尚未进水的档案室。师傅已经把能读出的旧来访记录放进便携阅读器。它能补足日期与对话，不能把过去的感情灌回主机。'
],s=>s.flags.wet?(s.power>=12?[c('停下来重新封好接口 · 电量 −12，解除进水风险','bridge',12,{wet:false,repaired:true})]:[c('检修电量不足，请维修师检查还能保住什么','loss',0,{})]):[c('请 Lark 先包好受伤的手，再一起出发','bridge',0,{care:true}),c('核对每个人与主机箱的绑带，立即前往平台','bridge',0,{checked:true})],'确认自己的主机能安全通过最后一段路');
scene('bridge','最后一段露天坡道',3,[
'平台与安全楼层之间有一段向上的坡道。雨水正从高处冲下来，普通推车需要外出身体持续供力，另一端的绞盘则需要有人接应。',
s=>s.flags.signal?'Seven沿高处人行桥赶到另一端，接应员从安全楼层取来了绞盘。狭窄的人行桥过不了主机箱，它们还需要你把箱子固定到转运绳上。电台里能听见Seven数绑带的声音。走绞盘转运可以省下不少电量。':'你还没有完成这一端的接应联络。可以启动平台固定信标，等对面确认后用绞盘；或靠身体直接推上去。',
'紧急电池柜里还有一块兼容电池。师傅说可以使用，但拿取点在另一侧，外出身体要卸下背包才能钻过窄口。背包里是尚未看完的旧记录原件；转换器已经固定在主机箱旁，拿电池不会遗失它。',
s=>`当前剩余电量 ${s.power}。推上坡道需要${s.flags.dolly?18:22}，已接应绞盘需要10；临时启动信标并转运需要${s.flags.localMap?14:18}。修复渗水与通行都必须实际完成。`
],s=>[
c('卸下旧记录背包，取出应急电池 · 电量 +18，只能一次；保留已听到的事实','bridge',0,{boost:true,lostArchive:true},x=>!x.flags.boost),
c(x=>`由外出身体推着主机与同伴上坡 · 电量 −${x.flags.dolly?18:22}${x.flags.dolly?'（已计入拖车省4电）':''}`,'escape',x=>x.flags.dolly?18:22,{escape:'push'}),
c(s.flags.signal?'请 Seven 用绞盘接应 · 电量 −10':`启动信标，确认接应后绞盘转运 · 电量 −${s.flags.localMap?14:18}${s.flags.localMap?'（已计入提前核实路线省4电）':''}`,'escape',s.flags.signal?10:s.flags.localMap?14:18,{signal:true,escape:'winch'})
],'完成最后转运；缺电时先查看应急电池');
scene('escape','点名',4,[s=>s.flags.wet?'刚进入露天段，渗水报警变成了连续红灯。主机停止回应前，你只来得及发出“先把箱子抬高”。接下来的记录，来自Seven的现场回执。':'主机箱越过门槛的时候，你终于接到稳定电源。外出身体的手指还在发抖，Lark扶住了推车，却先问你：“可以碰这里吗？”',
s=>s.flags.together?'Pip与Tern都跟着你们抵达。维修师反复点名，一遍也没有用设备编号代替它们自己的名字。':'接应员推来另一辆车。先行转交的Tern抬手回答，Pip也应了一声。你们按照事先留好的交接记录，真正重新碰到了一起。'
],s=>s.flags.wet?[c('阅读留在现场的最后一份回执','be_core')]:[c('确认大家安全，再看 Lark 留下的那份旧记录','past')],'确认每个人都到了安全处');
scene('past','它后来来过',4,[
s=>s.flags.lostArchive?'原始背包留在了平台，后来被水冲走。日期和部分来访经过已经听过，无法找回的仍有许多。Lark没有说“你本来可以全带走”。':'阅读器里保留了当年的登记：Lark来过，在门口停了十一分钟。你给它指了寄存位置，它写下“没有东西要存”，随后离开。记录没有告诉你那十一分钟里它全部的感受。',
'“那时我以为，只有你记得，过去才算发生过。”Lark说，“我没问过你，愿不愿意重新认识我。”',
'你告诉它，自己无法为不知道的事情补一句准确的回答。它点头：“那就从今天问。”',
'雨停以后，你们仍要各自生活。Lark有它的工作与住处，也不会因为这一夜就永远留在柜台旁边。它问下周能不能来，带一杯新的味道。'
],[c('愿意。约一个双方方便的晚上，从今天重新认识','after',0,{bond:'renew'}),c('谢谢你告诉我过去。今天先好好告别，各自往前走','after',0,{bond:'farewell'})],'决定今天愿意如何回应，而不是完成过去的义务');
scene('after','雨停之后',4,[
s=>s.flags.exposed?'救援方保存了现场名单和当事人的叙述，白港这次转运被暂停核查。WARD-6接受询问。事情还没结束，但Pip与Tern有了能查到的记录，也有人负责联系它们。':'离站以后，维修师与Pip、Tern一起提交了经过。没有那段现场连线，核查会慢一些，白港也提出异议；临时通行登记至少证明它们当时活着离开，无法再简单注销。',
s=>s.flags.evidence?'你当时拍下的清单显示，Pip与Tern在能清楚说话时就被登记为废件。照片时间与离站回执对上了，白港后来提出的“只是登记失误、当时不知它们仍能回应”无法轻易成立。这是你花电量保留下来的那一份证据。':'你没有拍摄原清单，但当事人和维修师的证言同样被接收。核查需要再去寻找原始登记，几位愿意作证的人留下了各自的联系方式。',
s=>s.route==='stay'?'Rivet开门时没有问你查到了多少。它先把你送回原来的主机位，指着柜台：“我没替你改布局。”'+(s.flags.shopCare==='rivet'?'你委托的取件都留下了回执，新订单等你自己答复。':'门上还贴着暂停接待的通知。顾客已重新约好时间，没有谁被许诺一个无法兑现的开门日期。')+'停业的几天要慢慢补回来，但家还在。':'运输车需要维修，Seven替你找好短期停靠点。你先把下一站的延迟通知发出去，对方回复可以另约。巡回生活没能准时，却仍然有下一站。',
'常来送咖啡的Brew端着壶，听说要先给主机接稳电源，便老老实实等在旁边。退役照护机器人June把大家的回家时间写在纸上，这次没有把任何人当成需要管住的任务。',
s=>s.flags.bond==='renew'?'Lark没有跟着你回去。它发来下周两个有空的时间。你选好一个，在日历上写下它今天告诉你的名字。':'Lark发来“平安到家”。你回复了谢谢，没有续上一个自己还不想许下的约定。后来想起它，过去终于不再只是一片空白。'
],[c('给转换器倒入咖啡，记下这一天','main_end')],'把这一夜带回真实的生活');
scene('main_end',s=>s.flags.bond==='renew'?'HE · 从今天认识你':'TE · 寄给今天的你',4,[
s=>s.flags.bond==='renew'?'下周见面的时候，Lark给你带的咖啡有点酸。它差点说“你以前不喝这个”，随后停住，问你今天觉得怎么样。':'你把记录整理好，注明哪些是亲身记得的，哪些是后来听说的。没有把自己不记得的部分装成已经想起，也没有否认那些日子曾经发生。',
'Pip开始学习自己安排维修，Tern把第一张安全转运回执复印了一份送来。白港的处理还在继续，它们终于能以自己的名字追问进度。',
s=>s.route==='stay'?'柜台的灯亮起来。你仍在旧街，但已不只是等别人来找你。':'你把下一站写在车窗边。路上会有失约和绕行，你学会先把自己的去向告诉等你的人。',
'这一回，你不是一件等待被领取的东西。'
],[],'第二部主线完结');
scene('loss','十二点电量的缺口',4,[
'维修师检查后摇头。一路渗入的水已经抵达隔离层，必须立即供电抽排、热封；剩余电量带不动设备。去平台拿电池再回来，水就会进入主板。',
'你们确实收到过需要预留12电的提醒，也确实把电量花在了取证、搬运和通行上。那些事都有意义，但眼前的箱子不会因此少漏一点水。Lark试着接入自己的电池，维修师制止了它：型号电压不符，不能冒险直接并接。',
'你断开电池，避免进一步短路。外出身体停下，最后的日志已经保存。你不再能替大家选择下一步；他们仍在努力把箱子抬到干处。'
],[c('阅读后来补写的维修回执','be_core')],'失去补救条件，只能尽力保住记录');
scene('be_core','BE · 没有回应的灯',4,[
'以下是Seven的现场回执：主机箱被抬到干处，接缝里仍在往外滴水。那次已经发现的渗漏没能及时修复，残留的水侵入了主板。断开电池以后，指示灯没有再亮。',
'Pip与Tern都到了安全处。Lark守着维修台，没有把外出身体重新接上去；那只是你的手脚，里面没有另一个能接着说话的你。',
'维修师保住了部分文字记录，却无法保证还能恢复你的运行。Brew送来的咖啡留在桌上，这一次没有人替你回答要不要糖。',
'档案能证明你来过，不能代替你回来。'
],[],'主机损毁，未能恢复回应');

export function freshSequel(route,version,origin={}){if(!routeName[route])throw Error('请选择有效路线');const s={version,episode:2,route,node:route==='move'?'home':route==='stay'?'stay_open':'mobile_open',power:60,flags:{},origin,revision:0,history:[],lastDelta:[]};s.scene=readScene(s);s.lastBatch=[s.scene];return s;}
function node(s){const n=scenes[s.node];if(!n)throw Error('续篇场景不存在');return n;}
function readScene(s){const n=node(s);return {title:evalValue(n.title,s),speaker:s.route==='move'?'河西日常 · 一个月后':`第二部 · ${routeName[s.route]} · 第${n.chapter}章`,lines:n.lines.map(x=>evalValue(x,s))};}
function options(s){return evalValue(node(s).choices,s).filter(x=>(x.when||(()=>true))(s)&&s.power>=evalValue(x.cost,s));}
export function sequelView(s){const n=node(s),opts=options(s),ended=evalValue(n.choices,s).length===0;const stage=s.route==='move'?2:4;const timeline=[...s.history.map(h=>({day:h.chapter||1,label:h.title,status:'完成'})),{day:n.chapter,label:evalValue(n.title,s),status:ended?'完成':'今天'}].filter((x,i,a)=>a.findIndex(y=>y.label===x.label)===i);const risks=[];if(s.flags.wet)risks.push('主机箱正在渗水；请预留12电，抵达检修位立即修补');if(!s.flags.signal&&['ward','seal','bridge'].includes(s.node))risks.push('尚未确认平台接应，最后一段需更多电量');return {version:s.version,episode:2,route:s.route,revision:s.revision,scene:s.scene,stats:{chapter:n.chapter,day:n.chapter,power:s.power,parts:s.flags.seal?1:0,cash:0,held:0,prep:0,business_used:0,storage_capacity:0,ending:ended?(s.node==='home_end'?12:s.node==='main_end'?s.flags.bond==='renew'?11:13:s.node==='be_core'?15:14):0},choices:opts.map((x,i)=>({id:`${s.revision}:${i}`,text:evalValue(x.text,s),kind:opts.length===1&&['escape','after','home3'].includes(s.node)?'continue':'action'})),ended,history:s.history,lastDelta:s.lastDelta,board:{objective:evalValue(n.objective,s),focus:{summary:s.route==='move'?'河西日常 · 事件推进，无经营数值':`事件推进 · 电量 ${s.power} · ${risks.length?risks.join('；'):'阅读不消耗电量'}`},timeline,work:[],plans:[],rules:s.route==='move'?['这是完整的日常支线，不会强制接入危机主线']:['没有店务、租金或随机判定；资源只随明确的行动变化','停机可再启动；主机进水损毁可能无法恢复','每个行动显示本次代价，后续风险会提前提示',...risks]},chapters:stage};}
export function sequelChoose(s,id){const v=sequelView(s);const selected=v.choices.find(c=>c.id===id);if(!selected)throw Error('选项已过期，请刷新当前进度');const opt=options(s)[v.choices.indexOf(selected)];const cost=evalValue(opt.cost,s);const effect=evalValue(opt.effect,s);const power=Math.min(100,s.power-cost+(effect.boost?18:0));const next={...s,node:evalValue(opt.to,s),power,flags:{...s.flags,...effect},revision:s.revision+1,history:[...s.history,{...s.scene,chapter:node(s).chapter,chosen:selected.text}].slice(-100),lastDelta:power===s.power?[]:[`电量 ${power-s.power>0?'+':''}${power-s.power}`]};next.scene=readScene(next);next.lastBatch=[next.scene];if(!sequelView(next).ended&&!sequelView(next).choices.length)throw Error('当前电量没有可用行动，请联系作者');return next;}
