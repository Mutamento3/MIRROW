"""Known action rejections are tool replies; unexpected code failures still raise."""
MESSAGES = {
    'explicit_completion_disposition_required':'看过当前成品后，用 finish 的 disposition 明确选择 share 或 keep；需要改进用 message。',
    'published_work_cannot_be_retracted':'这份工单已经公开。keep 表示保留当前可见性并收进收藏，不能撤回已经披露的内容。',
    'delivery_review_required':'当前交付组还有检查未完成，先查看当前报告；failed/interrupted 可用 review 重试，作品需修改则 message 返工，inspect 查看不会重启质检。',
    'wrong_work_order':'作品属于另一份工单；读取作品对应的原房间后再决定操作。',
    'revision_required_or_stale':'工单版本已变化或未提供；重新读取房间中的当前 revision。',
    'stale_artifact':'这份作品不属于当前待操作版本；重新读取当前房间与交付记录。',
    'current_plan_confirmation_required':'当前方案尚未确认；读取当前方案后由你决定是否确认。',
    'current_artifact_review_required':'当前工单不处于待验收阶段，或未指定作品；读取工单当前状态与实际交付。',
    'current_decision_required':'当前没有这组待答问题；重新读取房间中的待答问题。',
    'recovery_thread_required':'当前没有待核对的线程；读取房间中的最新状态。',
    'previous_execution_not_confirmed_stopped':'尚未确认原回合停止，保留现场等待技术核对；普通补充与重复确认不会解除这个卡点。',
}


async def execute(service, action):
    from .browser import BrowserUnavailable
    from .runner import AppServerRpcError, AppServerRequestTimeout
    try:
        return await service.action(action)
    except ValueError as exc:
        reason = str(exc)
        if reason not in MESSAGES:
            raise
        feedback = {'status':'rejected','reason':reason,'message':MESSAGES[reason]}
        if reason == 'current_artifact_review_required':
            job = service.store.job(action['job_id'])
            feedback['job_state'] = job['state']
            if job['state'] == 'accepted':
                feedback['message'] = '这份工单已由AI验收并归档，此次没有新增验收；作品仍可查看，具体升级用原单补充需求。'
        if reason == 'wrong_work_order':
            feedback['artifact_job_id'] = service.artifact(action['artifact_id'])['job_id']
    except BrowserUnavailable as exc:
        feedback = {'status':'unavailable','reason':exc.category,
                    'message':'浏览器本次未能完成检查，没有可用截图或测试结果；可稍后再次查看，不能据此判断作品坏了。'}
    except (AppServerRpcError, AppServerRequestTimeout) as exc:
        if action.get('action') != 'reconcile':
            raise
        feedback = {'status':'unavailable', 'reason':exc.category,
                    'message':'官方线程本次未能完成恢复核对，工单仍保留在技术受阻状态；没有重新开工，也没有改变你的需求或确认。'}
    return {'action_feedback':feedback}
