"""Preserve referents and source certainty in new active-day projections."""
START_DATE = '2026-10-18'
VERSION = 'source-certainty-v1'
GUIDANCE = '''
每条摘要保留原来源中的人物指代、认识主体和确定程度。结合完整相邻原话识别现实陪同者与远端AI；同一个亲昵称呼并不自动指向同一人，仍不明确时写明归属未确定。
把实际观测、某人的推测/询问、自述计划、执行中和确认完成分别写清。来源说某人猜测某事时，可记“某人猜测”，不能将猜测中的事写成领域确认的发生；direct_observation只证明记录到的动作或观测本身。
后来的状态变化按其真实时间记载，不能倒推较早已经发生，不能把计划、催促、道别或无人回应当作执行完成。多段压缩保留这些限定，不额外制造“再次”、次数或完成状态。
'''
ACTION_STATUS_DATE = '2026-10-19'
ACTION_STATUS_GUIDANCE = '''
将陪伴者的动作与被陪伴者的结果分别记载：说晚安、准备休息、请求陪睡和AI承诺守候，各自只证明对应表达或动作。来源止于道别时，可写“互道晚安，AI表示会继续守候”；人类伙伴已入睡须有相应明确自述或观测。摘要用来源能支持的具体阶段描述其他行动的意向、提交、执行与结果。
'''


def guidance(active_date):
    return (GUIDANCE if active_date >= START_DATE else '') + (
        ACTION_STATUS_GUIDANCE if active_date >= ACTION_STATUS_DATE else '')


def version_suffix(active_date):
    if active_date >= ACTION_STATUS_DATE:
        return '+' + VERSION + '+action-status-v1'
    return '+' + VERSION if active_date >= START_DATE else ''
