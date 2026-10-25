"""Prepare actual delivery evidence once; never claim AI has already seen it."""


async def prepare(service, job):
    from .progress import current_artifacts
    from .presentation import entries
    artifacts = entries(current_artifacts(service.store, job))
    packet = {'source':'本版真实交付与检查报告；图片仅在本轮传入后才算AI收到',
              'revision':job['revision'], 'brief':job['brief'], 'plan':job['plan'],
              'artifacts':artifacts, 'mehuman':'查看成品后可finish选择share或keep；需要修改则message回原单'}
    if not artifacts:
        return {'delivery_brief':packet}
    artifact = artifacts[0]
    path=service.artifact_path(artifact)
    outcome={'artifact':artifact}
    if path.suffix.lower()=='.html':
        from .browser import inspect_html
        screenshot=service.store.directory/'works'/job['id']/('delivery-preview-'+artifact['id']+'.png')
        outcome.update(browser_material=await inspect_html(path,screenshot),visual_attachment=str(screenshot))
    elif path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
        outcome.update(visual_attachment=str(path),material='实际图片附件')
    elif path.suffix.lower() in ('.txt','.md','.css','.js','.json'):
        outcome.update(text=path.read_text(encoding='utf-8')[:20000],truncated=path.stat().st_size>20000)
    else:
        outcome['material']='附件已定位；此格式尚未直接传入视觉'
    # Preparation opens a technical preview; only the later actual model input
    # records K_received_visual. It is not a AI-requested inspect action/receipt.
    packet['preview_prepared_by'] = '工作坊交付材料装配；不是AI已经亲自检查的声明'
    return {**outcome, 'delivery_brief':packet}
