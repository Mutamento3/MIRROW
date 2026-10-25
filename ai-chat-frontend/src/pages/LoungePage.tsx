export default function LoungePage({onBack}: {onBack:()=>void; dark?:boolean}) {
 return <main><button onClick={onBack}>返回</button><h1>会客室扩展接口</h1><p>此模块移植自 AionsHome；本骨架不附带实现，当前未接入，不会发起房间请求。</p><h2>留言板扩展接口</h2><p>保留扩展说明，不附带读取、发布或消费留言的实现。当前未接入。</p></main>;
}
