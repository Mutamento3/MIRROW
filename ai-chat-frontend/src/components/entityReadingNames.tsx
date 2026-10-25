export type ReadingEntity = { id:string; name:string; aliases?:string[]; preferred_name?:string };

export function entityLabel(entity?:ReadingEntity):string {
  if (!entity) return '';
  const preferred = entity.preferred_name?.trim();
  return preferred && preferred !== entity.name ? `${preferred}（${entity.name}）` : entity.name;
}

export function readingTitle(original:string, entity?:ReadingEntity):string {
  if (!entity?.preferred_name) return original;
  const normalize = (value:string)=>value.normalize('NFKC').trim().toLocaleLowerCase();
  return [entity.name,...(entity.aliases || [])].some(name=>normalize(name) === normalize(original))
    ? entity.preferred_name : original;
}

export function EntityIdentityDetails({entity, originalTitle}:{entity?:ReadingEntity; originalTitle:string}) {
  if (!entity) return null;
  return <div className="entry-identity" aria-label="人物身份对应">
    <p>主名称：{entity.name}{entity.preferred_name && <> · 常用称呼：{entity.preferred_name}</>}</p>
    {!!entity.aliases?.length && <p>人物别名：{entity.aliases.join('、')}</p>}
    {readingTitle(originalTitle,entity) !== originalTitle && <p>原词条标题：{originalTitle}</p>}
    <small>称呼资料来自同一实体档案；正文保留原文，旧称呼未作替换。</small>
  </div>;
}
