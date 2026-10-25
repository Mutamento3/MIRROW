"""Flash transport adapter for isolated completed-day settlement runs."""

from __future__ import annotations

from typing import Any


class DaySettlementProviderError(RuntimeError):
    """The experimental provider transport did not return usable content."""

    def __init__(self, status: str, error: str = "") -> None:
        self.safe_error_code = (
            f"provider_{status}:{error or 'unknown'}"
        )
        super().__init__(self.safe_error_code)


class FlashDaySettlementModelAdapter:
    """Keep provider content in-memory and expose only bounded usage diagnostics."""

    def __init__(self, *, usage_tag: str = "memory_v2_day_settlement_experiment", authority_source=None) -> None:
        self.usage_tag = usage_tag
        self.authority_source = authority_source
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.review_call_count = 0

    async def review_continuations(self, store, plan, judgments, *, allow_call=True):
        from .continuation_review import review
        from .conversation_source import ConversationSource
        if self.authority_source is None:
            from .online_ingest import configuration_from_environment
            self.authority_source = ConversationSource(configuration_from_environment().authority_db_path,
                                                       canonical_sessions_only=True)
        def record(usage, *, started=False):
            self.review_call_count += int(started)
            self.prompt_tokens += int(usage.get('prompt_tokens') or 0)
            self.completion_tokens += int(usage.get('completion_tokens') or 0)
            self.total_tokens += int(usage.get('total_tokens') or 0)
        return await review(store, plan, judgments, source=self.authority_source,
                            allow_call=allow_call, usage=record,
                            max_calls=getattr(self, 'review_call_allowance', 1))

    def prepare_continuation_request(self, kwargs):
        from .continuation_evidence import SourceBoundContinuationModel
        from .conversation_source import ConversationSource
        if self.authority_source is None:
            from .online_ingest import configuration_from_environment
            self.authority_source = ConversationSource(configuration_from_environment().authority_db_path,
                                                       canonical_sessions_only=True)
        return SourceBoundContinuationModel(self.authority_source, self._call).prepare(kwargs)

    async def __call__(self, **kwargs: Any) -> str:
        if kwargs.get('schema_name') == 'memory_v2_continuation_page':
            from .continuation_evidence import SourceBoundContinuationModel
            from .conversation_source import ConversationSource
            if self.authority_source is None:
                from .online_ingest import configuration_from_environment
                self.authority_source = ConversationSource(configuration_from_environment().authority_db_path,
                                                           canonical_sessions_only=True)
            return await SourceBoundContinuationModel(self.authority_source, self._call,
                capture_returned=getattr(self, 'capture_continuation_return', None))(**kwargs)
        return await self._call(**kwargs)

    async def _call(self, **kwargs: Any) -> str:
        from .experiment import _call_flash_once

        provider = await _call_flash_once(
            kwargs["messages"],
            max_tokens=int(kwargs["max_tokens"]),
            schema_name=str(kwargs["schema_name"]),
            schema=dict(kwargs["schema"]),
            usage_tag=self.usage_tag,
        )
        self.last_call_usage = dict(provider.usage or {})
        self.call_count += 1
        self.prompt_tokens += int(provider.usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(provider.usage.get("completion_tokens") or 0)
        self.total_tokens += int(provider.usage.get("total_tokens") or 0)
        if provider.status != "ok":
            error = DaySettlementProviderError(provider.status, provider.error)
            error.request_dispatched = provider.request_dispatched
            if provider.raw_content:
                error.response = {'content': provider.raw_content, 'finish_reason': provider.finish_reason}
                error.usage = provider.usage
            raise error
        return provider.raw_content

    def safe_usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "review_call_count": self.review_call_count,
        }


__all__ = [
    "DaySettlementProviderError",
    "FlashDaySettlementModelAdapter",
]
