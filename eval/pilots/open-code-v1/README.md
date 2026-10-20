# LiveCodeBench 小规模编码题集

> 后续业务主评测转向[自有 PR 回放题集](../pr-replay-v1/README.md)。本题集保留为基础编码对照；下文尚未执行的 24 次编码实验计划不会自动启动。

状态：6 道题与评分器已经准备并离线验证；尚未运行新的模型编码能力对比。

此前手写题过于简单，本版采用 [LiveCodeBench 官方数据](https://huggingface.co/datasets/livecodebench/code_generation_lite)中的原始题面、公开样例与私有测试。固定 `v6` 增量文件 `test6.jsonl`，不是累计的 `release_v6` 全量集。该文件包含 2025 年 1–4 月的 175 道题，本版从中选择 6 道不同比赛的 AtCoder 题。

源仓库 revision：`0fe84c3912ea0c4d4a78037083943e8f0c4dd505`。文件共 134,303,240 字节，SHA-256 为 `bb4c364f71921c4495a6ad15abe1a927350b720009f4933e2e71f8af0f6fd1f5`，已与[官方文件页](https://huggingface.co/datasets/livecodebench/code_generation_lite/blob/main/test6.jsonl)公布值核对。本机直连失败后通过 `hf-mirror.com` 获取相同文件；下载渠道不充当来源证明。

## 题目与分区

| 分区 | 原题 | 上游难度 | 选择理由 |
| --- | --- | --- | --- |
| 开发 | [abc387_c — Snake Numbers](https://atcoder.jp/contests/abc387/tasks/abc387_c) | medium | 大范围计数、严格不等式、区间边界 |
| 开发 | [abc391_e — Hierarchical Majority Vote](https://atcoder.jp/contests/abc391/tasks/abc391_e) | hard | 分层递归、局部选择与全局结果 |
| 锁定 | [abc400_b — Sum of Geometric Series](https://atcoder.jp/contests/abc400/tasks/abc400_b) | easy | 简单负对照，检查边界与不必要协作 |
| 锁定 | [abc394_d — Colorful Bracket Sequence](https://atcoder.jp/contests/abc394/tasks/abc394_d) | medium | 类型、嵌套和无效前缀同时约束 |
| 锁定 | [abc396_d — Minimum XOR Path](https://atcoder.jp/contests/abc396/tasks/abc396_d) | medium | 简单路径限制，容易套用不适用的贪心 |
| 锁定 | [abc395_e — Flip Edge](https://atcoder.jp/contests/abc395/tasks/abc395_e) | hard | 全局状态、不同代价、大整数 |

这些“选择理由”和算法家族标签只用于宿主分析，不进入模型输入。没有根据模型答错、意见分歧或团队获胜来挑题。难度沿用上游标签，不能当成这两款模型实测难度。

开发题用于调试公共提示、交付格式和难度诊断。锁定题在模型编码调用前固定；只允许做参考解和评分器校验，不能根据锁定成绩临时换题或改策略。

所有条件获得相同原题与样例。最终只评分一个完整 Python 3.12 stdin/stdout 程序；不从中间消息挑最佳代码。私有测试、参考实现和分析标签均不进入模型工作目录或对话。

## 生成与评分

选择清单保存在 [selection.json](selection.json)。下载源文件可使用其中的固定官方 URL；可用镜像替换主机名，但导入必须通过固定大小与 SHA-256 检查。导入不执行 Hugging Face loading script；压缩私有测试使用禁止 Python 全局对象引用的解码器。

```bash
# 默认读取已经下载并核验的 output/eval/source-cache/lcb-test6.jsonl。
# 输出目录已有 manifest 时拒绝覆盖；重建请另给 --output。
.venv/bin/python scripts/prepare_open_code_pilot.py

# 检查作者编写的参考实现，既跑公开样例，也跑全部私有测试。
.venv/bin/python scripts/validate_open_code_pilot.py

# 只检验一份最终代码，不调用模型。
.venv/bin/python scripts/grade_open_code.py \
  --task-id abc395_e --candidate /absolute/path/solution.py
```

生成目录 `output/eval/open-code-v1/`：

- `public/`：模型可见的原题、样例和统一交付格式；不含 family/focus 提示。
- `private/`：宿主使用的测试数据；每题保留全部 40 个上游私有测试。
- `manifest.json`：来源、分区、样本数和文件哈希。
- `validation.json`：参考解验证和错误解诊断。

评分要求退出正常，并且输出按空白切分后的 token 与期望完全一致、大小写一致。本版只选择唯一整数/字符串答案的 stdin 题，不涉及浮点容差或多解判定。每例超时 4 秒，均在新进程中执行；公开、私有成绩分别报告，不能用私有测试反馈迭代。

当前执行后端仅支持本机 macOS `sandbox-exec`：拒绝网络、用户目录和其他临时任务目录读取，清理环境变量，限制写入及执行时间。运行环境不支持时拒绝执行，不降级为宿主裸执行。Linux 复跑需先增加容器等执行后端；本结果也不能直接等同于官方榜单的运行配置。

## 已验证的内容

六份作者编写的参考实现通过 19 个公开样例与 240 个私有测试，共 259/259；它们用于校验接入，不是模型成绩。每道题都能拒绝明显错误的程序。

在开发题 abc391_e 上，一个忽略递归结构、只计算整体多数的错误实现通过 2/2 个公开样例，但仅通过 16/40 个私有测试。这说明不能再用“公开样例通过”或关键词匹配作为能力结论；该错误实现没有提供给任何协作组。

## 下一轮能力实验边界

先用两道开发题完成调用与评分接入，再冻结策略，对四道锁定题运行六个条件：A-Flash、A-GPT、B-Flash、B-GPT、C-mixed、D-on-demand，各一次，共 24 次探索运行。24 次运行只有 4 道独立锁定题，不做显著性或稳定排名声明。

D 必须使用只预排 lead、由实际工具请求产生受邀者回合的按需流程；不能直接调用旧 `run_cost_pilot.py` 并把固定四步 D 当成这一条件。真实协议探针使用的按需流程已验证，编码能力批次的 Runner/评分集成和完整批次预算仍待接入，尚未开始付费运行。

求助探针中的信息缺口只用于检验工具选择，不能搬到能力实验中给 D 单独持有资料。编码题所有组的信息相同，D 允许不邀请；要记录请求、实际执行、回复采用和成本，不为出现 A2A 强制邀请。若全部不邀请，应报告该策略的使用结果，不能宣称检验了实际委派收益。

不同模型的训练截止时间未独立证实，2025 年题目可能已经见过。因此不声称这套题无污染。它检验算法编码，不代表仓库修复或完整软件项目协作。缺少同构团队和等计算量消融时，也不把成绩差异单独归因于异构模型互补。

真实求助验证和费用见[本次执行报告](../../../docs/plans/2026-10-19-a2a-verification-and-open-code-pilot.md)。
