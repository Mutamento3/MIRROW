"""
上下文调度器MCP工具定义
"""
import os
import sys
import asyncio
import json
from typing import Any, List, Optional, Dict
from dataclasses import asdict

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context_scheduler_mcp.context_scheduler import (
    ContextScheduler,
    TriggerResult,
    QueryPlan,
    MemoryItem,
    ContextResult,
    get_global_scheduler
)

# 全局调度器实例
_scheduler = None

async def get_scheduler() -> ContextScheduler:
    """获取调度器实例（延迟初始化）"""
    global _scheduler
    if _scheduler is None:
        # 尝试从环境变量获取配置路径
        config_path = os.getenv("CONTEXT_SCHEDULER_CONFIG", None)
        _scheduler = await get_global_scheduler(config_path)
    return _scheduler

async def detect_trigger(user_message: str) -> dict:
    """
    触发判断工具

    Args:
        user_message: 用户消息

    Returns:
        触发判断结果
    """
    scheduler = await get_scheduler()
    trigger_result = await scheduler.detect_trigger(user_message)
    return asdict(trigger_result)

async def plan_query(user_message: str, force_trigger: bool = False) -> dict:
    """
    查询规划工具

    Args:
        user_message: 用户消息
        force_trigger: 是否强制触发（跳过触发判断）

    Returns:
        查询规划结果
    """
    scheduler = await get_scheduler()

    # 执行触发判断（除非强制触发）
    trigger_result = TriggerResult(
        triggered=force_trigger,
        confidence=1.0 if force_trigger else 0.0,
        reason="强制触发" if force_trigger else "未评估"
    )

    if not force_trigger:
        trigger_result = await scheduler.detect_trigger(user_message)

    # 执行查询规划
    query_plan = await scheduler.plan_query(user_message, trigger_result)

    result = query_plan.public_dict()
    result["trigger_result"] = asdict(trigger_result)

    return result

async def retrieve_memories(
    user_message: str,
    target_buckets: Optional[List[str]] = None,
    retrieval_strength: float = 0.8,
    max_results: int = 10
) -> dict:
    """
    记忆检索工具

    Args:
        user_message: 用户消息
        target_buckets: 已弃用；兼容旧调用，V2 不使用该过滤项
        retrieval_strength: 已弃用；兼容旧调用，不改变 V2 检索
        max_results: 最大结果数

    Returns:
        记忆检索结果
    """
    scheduler = await get_scheduler()

    query_plan = QueryPlan(
        original_query=user_message,
        enhanced_query=user_message,
        target_buckets=target_buckets or [],  # Echo legacy input; ignored by V2
        retrieval_strength=retrieval_strength,
        retrieval_method="memory_v2",
        max_results=max_results,
        similarity_threshold=0.0,
        intent="direct_retrieval",
        reasoning="直接通过 Memory V2 检索；旧桶/强度参数不生效"
    )

    # 执行检索
    memories = await scheduler.retrieve_memories(query_plan)

    # 转换为可序列化的字典
    memory_dicts = []
    for memory in memories:
        mem_dict = asdict(memory)
        # 确保metadata可序列化
        if "metadata" in mem_dict and not isinstance(mem_dict["metadata"], dict):
            mem_dict["metadata"] = {}
        memory_dicts.append(mem_dict)

    return {
        "query": user_message,
        "memories": memory_dicts,
        "count": len(memory_dicts),
        "query_plan": query_plan.public_dict()
    }

async def build_context(
    user_message: str,
    memories: Optional[List[dict]] = None,
    persona: str = "",
    user_profile: str = ""
) -> dict:
    """
    上下文构建工具

    Args:
        user_message: 用户消息
        memories: 记忆列表（字典格式）
        persona: 人设提示词
        user_profile: 用户资料

    Returns:
        上下文构建结果
    """
    scheduler = await get_scheduler()

    # 转换记忆字典为MemoryItem对象
    memory_items = []
    if memories:
        for mem_dict in memories:
            # 处理metadata字段
            metadata = mem_dict.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {"raw": metadata}

            memory_item = MemoryItem(
                id=mem_dict.get("id", ""),
                content=mem_dict.get("content", ""),
                similarity=mem_dict.get("similarity", 0.0),
                importance=mem_dict.get("importance", 0.5),
                bucket=mem_dict.get("bucket", "unknown"),
                source=mem_dict.get("source", "unknown"),
                metadata=metadata
            )
            memory_items.append(memory_item)

    # 执行上下文构建
    context_result = await scheduler.build_context(
        user_message=user_message,
        memories=memory_items,
        persona=persona,
        user_profile=user_profile
    )

    result = asdict(context_result)

    # 转换MemoryItem对象为字典
    result["memories"] = []
    for memory in context_result.memories:
        mem_dict = asdict(memory)
        # 确保metadata可序列化
        if "metadata" in mem_dict and not isinstance(mem_dict["metadata"], dict):
            mem_dict["metadata"] = {}
        result["memories"].append(mem_dict)

    return result

async def schedule_context(
    user_message: str,
    persona: str = "",
    user_profile: str = "",
    force_retrieve: bool = False,
    config_overrides: Optional[Dict[str, Any]] = None
) -> dict:
    """
    一站式上下文调度工具

    Args:
        user_message: 用户消息
        persona: 人设提示词
        user_profile: 用户资料
        force_retrieve: 是否强制检索
        config_overrides: 配置覆盖项

    Returns:
        完整的上下文调度结果
    """
    scheduler = await get_scheduler()

    # 应用配置覆盖（如果有）
    if config_overrides:
        # 注意：这里简化处理，实际应该实现配置热更新
        pass

    # 执行完整调度流程
    context_result = await scheduler.schedule_context(
        user_message=user_message,
        persona=persona,
        user_profile=user_profile,
        force_retrieve=force_retrieve
    )

    result = asdict(context_result)

    # 转换MemoryItem对象为字典
    result["memories"] = []
    for memory in context_result.memories:
        mem_dict = asdict(memory)
        # 确保metadata可序列化
        if "metadata" in mem_dict and not isinstance(mem_dict["metadata"], dict):
            mem_dict["metadata"] = {}
        result["memories"].append(mem_dict)

    return result

async def get_config() -> dict:
    """
    获取当前配置

    Returns:
        当前配置信息
    """
    scheduler = await get_scheduler()
    return scheduler.config

async def update_config(key: str, value: Any) -> dict:
    """
    更新配置项

    Args:
        key: 配置键（支持点分隔，如 "trigger.similarity_threshold"）
        value: 配置值

    Returns:
        更新结果
    """
    scheduler = await get_scheduler()

    if key in {"query_planning.default_retrieval_strength", "query_planning.bucket_priority"} or key == "retrieval" or key.startswith("retrieval."):
        return {
            "success": False,
            "applied": False,
            "deprecated": True,
            "key": key,
            "message": "旧检索配置不再由 Memory V2 消费，未应用此修改",
        }

    # 简单的配置更新（实际应该实现更安全的更新机制）
    try:
        keys = key.split(".")
        config_dict = scheduler.config

        # 导航到目标位置
        for k in keys[:-1]:
            if k not in config_dict:
                config_dict[k] = {}
            config_dict = config_dict[k]

        # 设置值
        last_key = keys[-1]
        old_value = config_dict.get(last_key)
        config_dict[last_key] = value

        return {
            "success": True,
            "key": key,
            "old_value": old_value,
            "new_value": value,
            "message": f"配置项 {key} 已更新"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"更新配置项 {key} 失败"
        }

async def get_stats() -> dict:
    """
    获取调度器统计信息

    Returns:
        统计信息
    """
    scheduler = await get_scheduler()

    # 这里可以收集更多运行时统计信息
    # 目前返回基本状态
    return {
        "initialized": scheduler.initialized,
        "config_loaded": bool(scheduler.config),
        "embedding_model_loaded": scheduler.embedding_model is not None,
        "http_client_active": scheduler.http_client is not None,
        "summary_index_available": False  # 已废弃，统一走 OB_Rev
    }

# MCP工具定义
TOOLS = [
    {
        "name": "detect_trigger",
        "description": "检测用户输入是否需要记忆检索",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "用户消息"
                }
            },
            "required": ["user_message"]
        }
    },
    {
        "name": "plan_query",
        "description": "规划 Memory V2 查询、条数及日期范围；旧桶/强度/阈值字段仅兼容保留",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "用户消息"
                },
                "force_trigger": {
                    "type": "boolean",
                    "description": "是否强制触发（跳过触发判断）",
                    "default": False
                }
            },
            "required": ["user_message"]
        }
    },
    {
        "name": "retrieve_memories",
        "description": "通过 Memory V2 共享读取检索；仅查询与 max_results 生效，旧桶/强度入参已弃用",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "用户消息或查询"
                },
                "target_buckets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "已弃用：接受旧入参但不按桶过滤，统一由 Memory V2 检索",
                    "deprecated": True
                },
                "retrieval_strength": {
                    "type": "number",
                    "description": "已弃用：接受旧入参但不调整 Memory V2 检索强度",
                    "minimum": 0,
                    "maximum": 1,
                    "deprecated": True
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10
                }
            },
            "required": ["user_message"]
        }
    },
    {
        "name": "build_context",
        "description": "上下文构建：组合人设、记忆和用户输入",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "用户消息"
                },
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "similarity": {"type": "number"},
                            "importance": {"type": "number"},
                            "bucket": {"type": "string"},
                            "source": {"type": "string"},
                            "metadata": {"type": "object"}
                        }
                    },
                    "description": "记忆列表",
                    "default": []
                },
                "persona": {
                    "type": "string",
                    "description": "人设提示词",
                    "default": ""
                },
                "user_profile": {
                    "type": "string",
                    "description": "用户资料",
                    "default": ""
                }
            },
            "required": ["user_message"]
        }
    },
    {
        "name": "schedule_context",
        "description": "一站式上下文调度：完整的调度流程",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "用户消息"
                },
                "persona": {
                    "type": "string",
                    "description": "人设提示词",
                    "default": ""
                },
                "user_profile": {
                    "type": "string",
                    "description": "用户资料",
                    "default": ""
                },
                "force_retrieve": {
                    "type": "boolean",
                    "description": "是否强制检索（忽略触发判断）",
                    "default": False
                },
                "config_overrides": {
                    "type": "object",
                    "description": "配置覆盖项",
                    "default": {}
                }
            },
            "required": ["user_message"]
        }
    },
    {
        "name": "get_config",
        "description": "获取当前配置",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "update_config",
        "description": "更新配置项",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "配置键（支持点分隔，如 'trigger.similarity_threshold'）"
                },
                "value": {
                    "type": "any",
                    "description": "配置值"
                }
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "get_stats",
        "description": "获取调度器统计信息",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

# 工具名称到函数的映射
TOOL_HANDLERS = {
    "detect_trigger": detect_trigger,
    "plan_query": plan_query,
    "retrieve_memories": retrieve_memories,
    "build_context": build_context,
    "schedule_context": schedule_context,
    "get_config": get_config,
    "update_config": update_config,
    "get_stats": get_stats
}