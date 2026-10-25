export type SettingsTab = 'general' | 'api'  | 'interaction' | 'permission' | 'truncation' | 'user-status' | 'commerce' | 'rift';

const groups:{label:string;items:{key:SettingsTab;title:string;hint:string;icon:string}[]}[] = [
  {label:'日常',items:[
    {key:'general',title:'常规与对话',hint:'启动、声音、约定与数据整理',icon:'⌂'},
    {key:'user-status',title:'人类伙伴的状态',hint:'状态来源与手动声明',icon:'◌'},
  ]},
  {label:'AI 的身体',items:[
    {key:'api',title:'API 配置',hint:'中转站、任务顺位与主大脑',icon:'↗'},
    {key:'interaction',title:'主动性与感知',hint:'漫想、刷手机与环境感知',icon:'✦'},
    {key:'permission',title:'设备能力',hint:'手机、辅助服务与执行权限',icon:'⌘'},
  ]},
  {label:'连续性',items:[
    {key:'truncation',title:'上下文边界',hint:'记忆、工具与生成长度',icon:'◫'},
  ]},
  {label:'外部世界',items:[
    {key:'commerce',title:'购物与委托',hint:'外部任务的真实执行边界',icon:'◇'},
    {key:'rift',title:'裂隙档案',hint:'虚构世界与现实事实隔离',icon:'△'},
  ]},
];

export default function SettingsNavigation({active,onChange}:{active:SettingsTab;onChange:(tab:SettingsTab)=>void}) {
  return <nav className="settings-navigation" aria-label="设置分类">
    {groups.map(group=><section className="settings-nav-group" key={group.label}>
      <h4>{group.label}</h4>
      {group.items.map(item=><button type="button" key={item.key} className={`settings-nav-item${active===item.key?' active':''}`} aria-current={active===item.key?'page':undefined} onClick={()=>onChange(item.key)}>
        <span className="settings-nav-icon" aria-hidden="true">{item.icon}</span>
        <span><strong>{item.title}</strong><small>{item.hint}</small></span>
      </button>)}
    </section>)}
  </nav>;
}
