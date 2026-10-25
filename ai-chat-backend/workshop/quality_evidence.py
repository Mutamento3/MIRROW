"""Concise conclusions derived from executed checks, separate from Luna's plan."""


def validate_plan(actions, mood_enabled):
    if not mood_enabled:
        return
    states=[]
    for index,row in enumerate(actions):
        if row['action']!='mood':
            continue
        end=next((j for j in range(index+1,len(actions)) if actions[j]['action']=='mood'),len(actions))
        if not any(step['action']=='expect_text' for step in actions[index+1:end]):
            raise ValueError('心情桥每次状态注入后需要实际文本断言')
        states.append('off' if row['value']=='unavailable' else 'on')
    if not any(states[i]=='off' and 'on' in states[:i] and 'on' in states[i+1:] for i in range(len(states))):
        raise ValueError('心情桥测试需要可用、不可用、恢复三段及各自断言')


def describe(body):
    image=body.get('image_review')
    if image and image.get('mode')=='actual_image':
        verdict={'passed':'初检通过','failed':'发现问题','needs_review':'需要进一步判断'}.get(image.get('status'),'待核对')
        return {'execution_summary':f"Luna 已读取真实图片像素（{image['width']}×{image['height']}），视觉{verdict}。",
                'review_phase':'visual_review','assertions_passed':0,'assertions_failed':0,
                'coverage_note':'结论覆盖图片中可观察的内容；审美与最终验收由 AI 判断。'+(' 本轮仅检查动画首帧。' if image.get('animated') else '')}
    assertions=[row for row in body.get('checks',[]) if row.get('action')=='expect_text']
    passed=sum(row.get('status')=='passed' for row in assertions)
    failed=sum(row.get('status')=='failed' for row in assertions)
    fixtures=sum(row.get('action')=='mood' and row.get('status')=='performed' for row in body.get('checks',[]))
    errors=len(body.get('page_errors',[]))+len(body.get('console_errors',[]))
    states=[row.get('value')!='unavailable' for row in body.get('checks',[]) if row.get('action')=='mood' and row.get('status')=='performed']
    recovery=any(not states[i] and any(states[:i]) and any(states[i+1:]) for i in range(len(states)))
    status=body.get('browser_status','not_tested')
    summary=(f'浏览器实际执行：{passed} 条断言通过，{failed} 条失败；页面/控制台错误 {errors} 条。'
             if assertions else '浏览器仅预览，尚无交互断言。' if status=='preview_only' else '尚无完成的交互断言。')
    if fixtures:
        summary+=f' 已注入 {fixtures} 次虚构心情。'
    return {'execution_summary':summary,'assertions_passed':passed,'assertions_failed':failed,
            'mood_fixtures':fixtures,'mood_recovery_exercised':recovery,'review_phase':'planned_before_browser',
            'coverage_note':('已验证虚构心情下的页面响应；真实共振数据经作品架传到作品的完整通路尚未由本报告验证。'
                +(' 本报告未覆盖不可用后恢复。' if not recovery else '')
                if fixtures else '结论只覆盖列出的实际操作与断言；未列出的功能仍待验证。')}
