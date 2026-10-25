export type Collaboration = {
  id:string; state:string; phase:string; reason:string; summary:string;
  active:boolean; chat_available:boolean; job_id:string;
};

export default function WorkshopCollaboration({value}:{value:Collaboration}) {
  return <div className="workshop-collaboration" data-phase={value.phase}>
    <small>同一次合作 · {value.active?'进行中':value.state==='completed'?'已收尾':'现场保留'}</small>
    <strong>{value.summary}</strong>
    {value.reason && <p>{value.reason}</p>}
    <span>{value.active?(value.chat_available?'等待期间可以正常聊天；新方案与成品会自动接回。':'AI 正在处理这一段，结束后立即释放聊天。'):'工单和作品历史都在；可从原单继续，不必重新开单。'}</span>
  </div>;
}
