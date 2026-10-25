/** WebView relay adapter. A native receipt and its originating socket stay together. */
type RelaySocket = Pick<WebSocket, 'readyState' | 'send'>;
type MobileToolPlugin = {
  execute(options: {tool: string; params: string}): Promise<unknown>;
};
type RelayRequest = {request_id: string; tool: string; params?: Record<string, unknown>};
type NativeReceipt = Record<string, unknown> & {success: boolean};

function failure(error: string): NativeReceipt {
  return {success: false, error};
}

function unknownReceipt(error: string, original?: Record<string, unknown>): NativeReceipt {
  // The plugin was called: neither completion nor safe re-execution is proven.
  return {...original, success: false, execution_status: 'unknown', error};
}

function normalizeReceipt(raw: unknown): NativeReceipt {
  const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return unknownReceipt('手机端返回了无效执行回执');
  }
  const receipt = parsed as Record<string, unknown>;
  if (receipt.execution_status === 'unknown') {
    return unknownReceipt(typeof receipt.error === 'string' ? receipt.error : '手机执行结果未确认', receipt);
  }
  if (typeof receipt.success !== 'boolean') {
    return unknownReceipt('手机端未返回明确执行状态', receipt);
  }
  return receipt as NativeReceipt;
}

/** Returns whether the original socket accepted the packet, not whether AI's action completed. */
export async function relayMobileToolRequest(
  request: RelayRequest,
  socket: RelaySocket,
  plugin?: MobileToolPlugin,
): Promise<boolean> {
  if (typeof request.request_id !== 'string' || !request.request_id || socket.readyState !== 1) {
    return false;
  }
  let result: NativeReceipt;
  if (typeof request.tool !== 'string' || !request.tool || (request.params != null
      && (typeof request.params !== 'object' || Array.isArray(request.params)))) {
    result = failure('手机工具请求格式无效');
  } else if (typeof plugin?.execute !== 'function') {
    result = failure('Capacitor 插件未安装');
  } else {
    // Serialize before execution so malformed parameters cannot cause a partial action.
    let params: string;
    try {
      params = JSON.stringify(request.params ?? {});
    } catch {
      result = failure('手机工具参数无法序列化');
      return sendReceipt(request.request_id, socket, result);
    }
    try {
      result = normalizeReceipt(await plugin.execute({tool: request.tool, params}));
    } catch {
      result = unknownReceipt('手机工具执行结果未确认');
    }
  }
  return sendReceipt(request.request_id, socket, result);
}

function sendReceipt(requestId: string, socket: RelaySocket, result: NativeReceipt): boolean {
  if (socket.readyState !== 1) return false;
  const error = result.success ? null
    : (typeof result.error === 'string' && result.error
      || typeof result.content === 'string' && result.content || '手机端执行失败');
  try {
    socket.send(JSON.stringify({
      type: 'mobile_tool_result', request_id: requestId,
      success: result.success, result, error,
    }));
    return true;
  } catch {
    // A failed send cannot establish whether the native action took effect.
    return false;
  }
}
