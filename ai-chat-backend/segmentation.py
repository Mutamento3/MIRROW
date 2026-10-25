"""
消息分段模块
基于语义相似度和时间间隔将长对话分割成多个片段
"""
import asyncio
import numpy as np
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from vector_embedder import get_global_embedder, VectorEmbedder


@dataclass
class SegmentationConfig:
    """分段配置"""
    similarity_threshold: float = 0.5    # 语义相似度阈值（低于此值视为主题切换）
    min_segment_size: int = 3            # 最小片段消息数
    max_segment_size: int = 50           # 最大片段消息数
    timeout_minutes: float = 30.0        # 时间间隔阈值（分钟）
    enable_time_factor: bool = True      # 是否考虑时间因素
    window_size: int = 3                 # 相似度计算窗口大小


@dataclass
class MessageSegment:
    """消息分段"""
    start_index: int                     # 起始消息索引
    end_index: int                       # 结束消息索引（包含）
    messages: List                        # 分段内的消息
    break_reason: str = ""               # 分段原因: "semantic", "timeout", "max_size", "end"


class MessageSegmenter:
    """消息分段器

    基于语义相似度和时间间隔将长对话分割成多个主题片段。
    用于分段摘要生成，解决长对话难以准确概括的问题。
    """

    def __init__(self, config: SegmentationConfig = None):
        self.config = config or SegmentationConfig()
        self.embedder = get_global_embedder()

    async def segment(self, messages: List) -> List[MessageSegment]:
        """将消息分段

        Args:
            messages: 消息列表（Message对象）

        Returns:
            分段列表
        """
        # 消息数量太少，不分段
        if len(messages) <= self.config.min_segment_size:
            return [MessageSegment(
                start_index=0,
                end_index=len(messages) - 1,
                messages=messages,
                break_reason="single"
            )]

        # 生成所有消息的嵌入向量
        texts = [f"{msg.role}: {msg.content}" for msg in messages]
        embeddings = await self.embedder.embed_texts_async(texts)

        # 找到分段点
        break_points = self._find_break_points(messages, embeddings)

        # 根据分段点构建片段
        segments = self._build_segments(messages, break_points)

        return segments

    def _find_break_points(
        self,
        messages: List,
        embeddings: np.ndarray
    ) -> List[Tuple[int, str]]:
        """找到分段点

        Args:
            messages: 消息列表
            embeddings: 嵌入向量数组

        Returns:
            分段点列表，每个元素为 (索引, 原因)
        """
        break_points = []

        for i in range(len(embeddings) - 1):
            # 计算窗口平均相似度（更稳定）
            window_start = max(0, i - self.config.window_size // 2)
            window_end = min(len(embeddings), i + self.config.window_size // 2 + 2)

            # 左侧窗口的平均嵌入
            left_emb = np.mean(embeddings[window_start:i+1], axis=0)
            # 右侧窗口的平均嵌入
            right_emb = np.mean(embeddings[i+1:window_end], axis=0)

            # 计算相似度
            similarity = VectorEmbedder.cosine_similarity(left_emb, right_emb)

            # 语义相似度低于阈值 -> 主题切换
            if similarity < self.config.similarity_threshold:
                break_points.append((i, "semantic"))
                continue

            # 时间间隔超过阈值
            if self.config.enable_time_factor:
                time_gap = self._calculate_time_gap(messages[i], messages[i+1])
                if time_gap > self.config.timeout_minutes:
                    break_points.append((i, "timeout"))

        return break_points

    def _build_segments(
        self,
        messages: List,
        break_points: List[Tuple[int, str]]
    ) -> List[MessageSegment]:
        """根据分段点构建片段，遵守大小约束

        Args:
            messages: 消息列表
            break_points: 分段点列表

        Returns:
            片段列表
        """
        segments = []
        current_start = 0
        break_points = sorted(break_points, key=lambda x: x[0])

        for bp_idx, bp_reason in break_points:
            segment_size = bp_idx - current_start + 1

            # 片段太小，跳过此分段点
            if segment_size < self.config.min_segment_size:
                continue

            # 片段太大，强制分割
            if segment_size > self.config.max_segment_size:
                for split_idx in range(current_start, bp_idx + 1, self.config.max_segment_size):
                    end_idx = min(split_idx + self.config.max_segment_size - 1, bp_idx)
                    segments.append(MessageSegment(
                        start_index=split_idx,
                        end_index=end_idx,
                        messages=messages[split_idx:end_idx + 1],
                        break_reason="max_size"
                    ))
                current_start = end_idx + 1
            else:
                segments.append(MessageSegment(
                    start_index=current_start,
                    end_index=bp_idx,
                    messages=messages[current_start:bp_idx + 1],
                    break_reason=bp_reason
                ))
                current_start = bp_idx + 1

        # 处理剩余消息
        if current_start < len(messages):
            remaining = len(messages) - current_start
            if remaining < self.config.min_segment_size and segments:
                last_segment_size = segments[-1].end_index - segments[-1].start_index + 1
                # 仅在合并后不会超过 max_segment_size 时才合并
                if last_segment_size + remaining <= self.config.max_segment_size:
                    # 合并到最后一个片段
                    segments[-1].end_index = len(messages) - 1
                    segments[-1].messages = messages[segments[-1].start_index:]
                else:
                    # 超过上限，单独成段
                    segments.append(MessageSegment(
                        start_index=current_start,
                        end_index=len(messages) - 1,
                        messages=messages[current_start:],
                        break_reason="end"
                    ))
            else:
                segments.append(MessageSegment(
                    start_index=current_start,
                    end_index=len(messages) - 1,
                    messages=messages[current_start:],
                    break_reason="end"
                ))

        return segments

    def _calculate_time_gap(self, msg1, msg2) -> float:
        """计算两条消息之间的时间间隔（分钟）

        Args:
            msg1: 第一条消息
            msg2: 第二条消息

        Returns:
            时间间隔（分钟）
        """
        try:
            t1_str = msg1.timestamp
            t2_str = msg2.timestamp

            # 处理不同时间格式
            if 'Z' in t1_str:
                t1 = datetime.fromisoformat(t1_str.replace('Z', '+00:00'))
            else:
                t1 = datetime.fromisoformat(t1_str)

            if 'Z' in t2_str:
                t2 = datetime.fromisoformat(t2_str.replace('Z', '+00:00'))
            else:
                t2 = datetime.fromisoformat(t2_str)

            return abs((t2 - t1).total_seconds() / 60)
        except Exception:
            return 0


# 全局分段器实例
_global_segmenter = None


def get_global_segmenter(config: SegmentationConfig = None) -> MessageSegmenter:
    """获取全局分段器实例"""
    global _global_segmenter
    if _global_segmenter is None:
        _global_segmenter = MessageSegmenter(config)
    return _global_segmenter