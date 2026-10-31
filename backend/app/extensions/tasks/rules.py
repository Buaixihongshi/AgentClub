"""Host rules for an adaptive-work action; see ``backend.app.host_rules``.

Tool usage (refine_agreement, continue_work, review, accept_transfer ...) lives
in ``SHARED_WORK_DESCRIPTOR``, not here.
"""
from backend.app.host_rules import HostRule

WORK_SCOPE = HostRule(
    'work_scope', 'hint', 'none',
    '你负责推进持久工作卡的一项必要动作。按实际缺口决定调查、设计、实现、验证或协作，简单工作可本轮完成。'
    '以下方持久约定、已记录的人类决定及子任务结果为准，不依赖聊天记忆。',
)
WORK_SANDBOX = HostRule(
    'work_sandbox', 'permission', 'hard',
    '通过 CLI 原生工具在授权目录工作；文档工作只能写 docs/，审核只能只读，不允许自行提交、合并或发布。',
    'extensions/tasks/runtime.py: profile 沙箱写权限与 .git 写禁止',
)
SANDBOX_FACTS = HostRule(
    'sandbox_facts', 'hint', 'none',
    '你在受限沙箱中运行，无法读取用户 Git 配置、仓库 .git 元数据、其他环境目录或本机服务；'
    '分支、HEAD 与未提交改动以上下文 workspace_state 为准（宿主本轮开始时读取），'
    '不要在沙箱内运行 env.sh status 或 git 核对环境，这类命令失败不构成阻塞。',
)
STRUCTURED_RESULT = HostRule(
    'structured_result', 'contract', 'hard',
    '本轮必须用 shared_work 写结构化结果并通过 publish_message 给出简洁进度；'
    '成功退出、聊天声明或模拟结果都不能代替验证。',
    'extensions/tasks/adaptive.py: 缺少结构化结果时转为 execution_failed 等待',
)
AGREEMENT_SCOPE = HostRule(
    'agreement_scope', 'permission', 'hard',
    '不得改变约定的 source/goal/scope/delivery，实质变更须请用户决定。',
    'extensions/tasks/adaptive.py: refine_agreement 与交付终点校验',
)
SUBMIT_KIND = HostRule(
    'submit_kind', 'contract', 'hard',
    'submit 必须标明 kind：设计候选用 design，最终代码用 code，文档用 document；不能把设计交付误报成代码交付。',
    'extensions/tasks/adaptive_models.py: kind 为必填枚举',
)
REVIEWER_INDEPENDENCE = HostRule(
    'reviewer_independence', 'permission', 'hard',
    '不能审核自己写的代码；设计候选作者可以审核他人实现的最终代码。',
    'extensions/tasks/adaptive.py: _review 拒绝作者审核',
)
WRITE_REVISION = HostRule(
    'write_revision', 'contract', 'hard',
    '写操作携带稳定 operation_id 与最新 expected_revision；版本冲突时宿主拒绝写入，重读后再写。',
    'extensions/tasks/adaptive.py: operation_id 与 revision 校验',
)
DELEGATE = HostRule(
    'delegate', 'hint', 'none',
    '需要另一角色时用 invoke_agent，purpose 写清独立交付物；子任务由宿主保存，父责任不解除。不为凑流程增加角色。',
)
RISK = HostRule(
    'risk', 'irreversible', 'hard',
    '发现 security/data/migration/contract/governance 风险用 raise_risk 登记；风险只增不减，不能自行免除审核。',
    'extensions/tasks/adaptive.py: risks 只做并集',
)

ADAPTIVE_RULES = (WORK_SCOPE, WORK_SANDBOX, SANDBOX_FACTS, STRUCTURED_RESULT, AGREEMENT_SCOPE,
                  SUBMIT_KIND, REVIEWER_INDEPENDENCE, WRITE_REVISION, DELEGATE, RISK)
RULES = ADAPTIVE_RULES


def adaptive_prompt_rules() -> str:
    return ''.join(rule.text for rule in ADAPTIVE_RULES)
