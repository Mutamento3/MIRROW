"""
记账本 - 数据模型
"""
from pydantic import BaseModel
from typing import Optional, List


class LedgerEntry(BaseModel):
    """记账条目"""
    id: str
    content: str                    # 短句描述（如"答应早回家结果11点到家"）
    debt_type: str                  # promise_broken | hidden_truth | serious_flirt | (自定义)
    source_context: str = ""        # 触发场景简述
    wander_event_id: Optional[str] = None
    created: str                    # ISO timestamp
    status: str = "active"          # active | dormant
    comment: str = ""               # 用户备注（非空=有争议）
    session_id: str = ""            # 创建时的会话ID，用于上下文恢复
    last_active: str = ""           # 最后活跃时间（被touch时刷新），空=未初始化
    settled_at: str = ""            # 结算/归档时间，非空=已结算


class LedgerEntryWithContext(LedgerEntry):
    """带原始对话上下文的记账条目"""
    context_messages: list = []     # [{role, content, timestamp}]


class LedgerDetailResponse(BaseModel):
    """单条账目详情响应"""
    entry: LedgerEntryWithContext


class LedgerCreateRequest(BaseModel):
    """创建记账条目请求"""
    content: str
    debt_type: str = "promise_broken"
    source_context: str = ""


class LedgerUpdateCommentRequest(BaseModel):
    """更新备注请求"""
    comment: str = ""               # 备注内容（≤50字）


class LedgerStats(BaseModel):
    """记账统计"""
    debt_type: str
    count_7d: int
    count_total: int
    rank: int                       # 频次排名（1=最多）
    total_types: int                # 分类总数
    recent_entries: List[str] = []  # 最近5条内容摘要
