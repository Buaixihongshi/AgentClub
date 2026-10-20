"""Allowlisted public diagnostics; provider output stays private until release."""

MESSAGES = {
    'managed_preferences': 'CLI 无法读取 macOS 管理偏好，初始化失败。',
    'filesystem_permission': 'CLI 所需文件或临时目录访问被隔离规则拒绝。',
    'invalid_arguments': 'CLI 启动参数不兼容，尚未进入讨论。',
    'authentication': 'Provider 身份验证失败，请检查登录状态。',
    'network_policy': 'CLI 网络策略初始化失败。',
    'timeout': 'CLI 超时，未完成有效提交。',
    'stopped': '本回合已停止。',
    'missing_submission': 'CLI 已退出，但没有完成有效的结构化提交。',
    'execution_failed': 'CLI 运行失败，未完成有效提交。',
}


def failure_code(text, *, exit_code=None):
    value = str(text).lower()
    # Prefer the fatal startup error over secondary network warnings.
    if 'failed to synchronize managed preferences' in value:
        return 'managed_preferences'
    if 'operation not permitted' in value or 'permission denied' in value or 'eperm' in value:
        return 'filesystem_permission'
    if 'intended prompt' in value or 'unexpected argument' in value or 'unknown option' in value:
        return 'invalid_arguments'
    if '401' in value or 'not logged in' in value or 'authentication' in value:
        return 'authentication'
    if 'application network policy is unavailable' in value:
        return 'network_policy'
    if 'timed out' in value:
        return 'timeout'
    if 'discussion stopped' in value:
        return 'stopped'
    return 'missing_submission' if exit_code == 0 else 'execution_failed'


def private_error_tail(stderr):
    """Keep the fatal suffix, never a prefix that hides the final error."""
    return stderr.strip()[-1000:]
