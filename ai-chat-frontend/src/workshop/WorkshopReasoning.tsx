export type ReasoningOptions = Record<string, {supported:string[]; default:string|null}>;
export const effortLabels:Record<string,string> = {low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'Ultra'};

export default function WorkshopReasoning({label, model, value, options, onChange}: {
  label:string; model:string; value:string; options?:ReasoningOptions; onChange:(value:string)=>void;
}) {
  const supported=options?.[model]?.supported || [];
  const choices=supported.length ? Array.from(new Set([...supported,value])) : Object.keys(effortLabels);
  return <label>{label}<select value={value} onChange={event=>onChange(event.target.value)}>
    {choices.map(effort=><option key={effort} value={effort} disabled={supported.length>0 && !supported.includes(effort)}>{effortLabels[effort] || effort}{supported.length>0 && !supported.includes(effort)?' · 当前模型不支持':''}</option>)}
  </select>{!supported.length && <small>模型目录暂未确认，执行器将校验所选强度。</small>}</label>;
}
