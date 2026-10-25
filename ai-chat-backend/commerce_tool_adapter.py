"""Thin AI-facing adapter for the two independent commerce MCP domains.

The adapter carries only a merchant, structured brief, IDs and compact
candidate choices.  It never constructs a persona/context prompt and never
exposes the device gateway or raw app material.
"""
from __future__ import annotations
import json
from typing import Any, Mapping
from behavior_scheduler.base_tool import BaseTool, ToolResult, ToolStatus
from meituan_order_mcp.service import MeituanOrderService
_services: dict[str, Any] | None = None

def get_commerce_services() -> dict[str, Any]:
    global _services
    if _services is None:
        _services = {'meituan': MeituanOrderService()}
    return _services

def _service(merchant: str) -> Any:
    merchant = str(merchant or '').strip().lower()
    if merchant not in {'meituan'}:
        raise ValueError('merchant_must_be_meituan')
    return get_commerce_services()[merchant]

def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

class CommerceKTool(BaseTool):
    """One of the five stable commerce tool names (kept off AI's visible list)."""
    visible_to_pro = False
    operation = ''
    name = ''
    description = ''
    flash_description = ''
    parameters_schema: dict[str, Any] = {'type': 'object', 'properties': {}, 'required': []}

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            merchant = kwargs.get('merchant', '')
            service = _service(merchant)
            if self.operation == 'submit_request':
                result = service.submit_request({'brief': kwargs.get('brief', ''), 'max_payable': kwargs.get('max_payable'), 'address_label': kwargs.get('address_label', 'default'), 'constraints': kwargs.get('constraints') or {}})
            elif self.operation == 'get_proposal':
                result = service.get_proposal(str(kwargs.get('request_id') or ''))
            elif self.operation == 'confirm_candidate':
                result = service.confirm_candidate(str(kwargs.get('request_id') or ''), str(kwargs.get('candidate_id') or ''), repeated_principle=bool(kwargs.get('repeated_principle')), explicit_persistent=bool(kwargs.get('explicit_persistent')))
            elif self.operation == 'get_status':
                result = service.get_status(str(kwargs.get('request_id') or ''))
            elif self.operation == 'cancel_request':
                result = service.cancel_request(str(kwargs.get('request_id') or ''))
            else:
                raise ValueError('commerce_operation_invalid')
            if not isinstance(result, Mapping):
                return ToolResult(status=ToolStatus.ERROR, content='', error='commerce_result_invalid')
            succeeded = bool(result.get('success'))
            return ToolResult(status=ToolStatus.SUCCESS if succeeded else ToolStatus.FAILED, content=_json(result), error=None if succeeded else str(result.get('error') or result.get('status') or 'commerce_operation_failed'))
        except (TypeError, ValueError, KeyError) as exc:
            return ToolResult(status=ToolStatus.ERROR, content='', error=str(exc))
        except Exception as exc:
            return ToolResult(status=ToolStatus.ERROR, content='', error=type(exc).__name__)

class SubmitCommerceRequestTool(CommerceKTool):
    name = 'submit_request'
    operation = 'submit_request'
    description = '向美团域 Agent 提交结构化购物需求；必须给出本单 max_payable，不携带人格全文。'
    flash_description = '提交美团购物需求'
    parameters_schema = {'type': 'object', 'required': ['merchant', 'brief', 'max_payable'], 'properties': {'merchant': {'type': 'string', 'enum': ['meituan']}, 'brief': {'type': 'string', 'maxLength': 1000}, 'max_payable': {'type': ['number', 'string']}, 'address_label': {'type': 'string', 'enum': ['default', 'home', 'work']}, 'constraints': {'type': 'object'}}}

class GetCommerceProposalTool(CommerceKTool):
    name = 'get_proposal'
    operation = 'get_proposal'
    description = '获取域 Agent 的紧凑候选证据卡与限制说明。'
    flash_description = '查看购物候选提案'
    parameters_schema = {'type': 'object', 'required': ['merchant', 'request_id'], 'properties': {'merchant': {'type': 'string', 'enum': ['meituan']}, 'request_id': {'type': 'string'}}}

class ConfirmCommerceCandidateTool(CommerceKTool):
    name = 'confirm_candidate'
    operation = 'confirm_candidate'
    description = '确认美团候选；记录购物决策，不执行支付。'
    flash_description = '确认购物候选'
    pro_guide = '确认已查到的真实购物候选，只记录购物决策，不执行支付；状态以真实回执为准。'
    parameters_schema = {'type': 'object', 'required': ['merchant', 'request_id', 'candidate_id'], 'properties': {'merchant': {'type': 'string', 'enum': ['meituan']}, 'request_id': {'type': 'string'}, 'candidate_id': {'type': 'string'}, 'repeated_principle': {'type': 'boolean'}, 'explicit_persistent': {'type': 'boolean'}}}

class GetCommerceStatusTool(CommerceKTool):
    name = 'get_status'
    operation = 'get_status'
    description = '读取购物任务状态与真实执行能力边界。'
    flash_description = '查看购物任务状态'
    parameters_schema = {'type': 'object', 'required': ['merchant', 'request_id'], 'properties': {'merchant': {'type': 'string', 'enum': ['meituan']}, 'request_id': {'type': 'string'}}}

class CancelCommerceRequestTool(CommerceKTool):
    name = 'cancel_request'
    operation = 'cancel_request'
    description = '取消尚未完成的美团购物任务。'
    flash_description = '取消购物任务'
    parameters_schema = {'type': 'object', 'required': ['merchant', 'request_id'], 'properties': {'merchant': {'type': 'string', 'enum': ['meituan']}, 'request_id': {'type': 'string'}}}

def register_commerce_tools() -> dict[str, BaseTool]:
    return {'submit_request': SubmitCommerceRequestTool(), 'get_proposal': GetCommerceProposalTool(), 'confirm_candidate': ConfirmCommerceCandidateTool(), 'get_status': GetCommerceStatusTool(), 'cancel_request': CancelCommerceRequestTool()}
__all__ = ['CancelCommerceRequestTool', 'ConfirmCommerceCandidateTool', 'GetCommerceProposalTool', 'GetCommerceStatusTool', 'SubmitCommerceRequestTool', 'get_commerce_services', 'register_commerce_tools']
