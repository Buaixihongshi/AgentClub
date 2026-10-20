# 多 Agent 可评测性：A/B/C/D 对比实验

> 业务评测方向：已从自有 PR 初筛并构建两道仓库修复开发题，通过修复前失败、参考补丁后通过和保留行为检查；当前没有锁定题，也没有启动模型修复批次。见[自有 PR 评测设计与结果](plans/2026-10-19-pr-derived-evaluation-spec.md)及[题集说明](../eval/pilots/pr-replay-v1/README.md)。下文为原闭卷 MVP，不代表仓库级运行器已经完成。

> 2026-10-19 进展：首轮两题冒烟没有发生 A2A，见[首轮复盘](plans/2026-10-19-evaluation-retrospective-and-a2a-v2.md)。后续已修复工具契约并通过双向真实求助、自主求助和无需帮助对照，另已构建六道 LiveCodeBench 原题及执行评分器；新的编码能力对比尚未运行，旧四步 D 不代表按需组队。见[最新执行结果](plans/2026-10-19-a2a-verification-and-open-code-pilot.md)。

> 目标不是证明“Multi-Agent 一定更好”，而是回答两个可证伪的问题：
> 1. 在哪些任务上，Multi-Agent 比 Single Agent 更好？
> 2. 在哪些任务上，Multi-Agent 反而更差，差在质量、成本还是延迟？

这套评测把“编排策略”从人格和 Provider 中单独拆出来，用同一批任务、同一个 lead、同一套评分规则做配对比较。默认的 `fixture` 模式不调用模型，用于验证评测链路本身；真正的能力结论必须用 `real` 模式跑出来。

> 运行时（真实回合）的观测与检查见[回合观测](OBSERVABILITY.md)；本文是离线批次实验。

## 1. 四个实验组

| 组 | 名称 | 实际执行 | 目的 |
| --- | --- | --- | --- |
| A | Single Agent | 1 个 lead，1 个回合 | 基线 |
| B | Single Agent + Self Reflection | 同一 lead：起草 → 检查一 → 检查二 → 修订，共 4 个回合 | 隔离“多花算力做两次检查”的收益 |
| C | Multi-Agent Fixed Workflow | P1 起草 → P2 检查一 → P3 检查二 → P1 修订，共 4 个回合；禁用 A2A | 隔离“换执行身份做同样检查”的收益与协调成本 |
| D | Multi-Agent + Dynamic A2A | 与 C 相同的 4 回合骨架，允许插入最多 2 个动态 A2A 回合 | 隔离“动态路由”相对固定协作的收益与风险 |

关键控制变量：

- **同一个 lead**：四组都由 `--lead`（默认 `chulan`）主导，A/B 只给 lead 一个人。
- **同一套回合骨架**：B 和 C 都有 4 个宿主回合，阶段目的完全相同；C 只是把两个检查回合换成了 P2/P3。这样 B−A 和 C−B 的差异更容易归因。
- **同一个 Provider**：`real` 模式建议用 `--provider codex` 把所有角色钉在同一个模型上；不钉 Provider 时，C/D 会混入模型差异，报告会给出警告。
- **中性人格**：默认 `--persona-mode neutral`，保留 agent id 以维持路由，但去掉角色口吻和人格指令；人格实验应另跑 `--persona-mode project`。
- **同一批任务、同一套 rubric**：任务和评分与编排策略无关。
- **独立会话**：每个 trial 新建 conversation / mission / adapter，不复用上下文。
- **配对运行**：同一 task/repeat 下四个 arm 都跑；每个 task/repeat 的 arm 顺序按 seed 随机，避免时间漂移。

C 和 D 的差别是刻意设计的：两者的基础骨架相同，C 永远按 P1→P2→P3→P1 执行；D 允许在骨架中插入最多 2 个动态 A2A 回合。这样 D 的“不必要委派”“该委派却没委派”“委派结果是否被最终交付采用”都会直接体现在质量、成本和 trace 上。

## 2. 任务集

`eval/tasks.json` 当前有 12 个闭卷任务，覆盖 6 类：

| 类别 | 任务数 | 考察点 | 预期 |
| --- | ---: | --- | --- |
| `simple_factual` | 2 | 单点事实、算术、格式压缩 | 多 Agent 容易因寒暄/总结而变差 |
| `multi_constraint` | 2 | 多约束同时满足 | 多 Agent 的交叉检查可能补漏 |
| `tradeoff_analysis` | 2 | 权衡、条件化建议 | 多 Agent 的视角覆盖可能更好 |
| `debugging` | 2 | 根因与最小修复 | 多人可能交叉验证，也可能引入错误建议 |
| `long_context` | 2 | 长文冲突、时间线归因 | 多 Agent 可能丢中间信息，或总结丢细节 |
| `ambiguous_requirement` | 2 | 信息不足时的澄清与取舍 | 多 Agent 容易过度讨论，也可能问得更全 |

每个任务都有可自动检查的 rubric，检查项包括：

- `contains_all` / `contains_any` / `not_contains`
- `regex` / `not_regex` / `min_matches`
- `max_chars` / `min_chars`
- `json_fields`
- `equals`

rubric 是**可审计的**：报告会列出每个 trial 失败的检查项，而不是只给一个黑盒分数。

## 3. 指标

每个 trial 从持久化的 messages / events / adapter 计量中提取：

| 指标 | 含义 |
| --- | --- |
| `score` | rubric 加权通过率，0–1 |
| `passed` | `score >= threshold`（默认 0.8）且 mission 已 completed；失败/阻塞保留诊断分但不算成功 |
| `total_tokens` | Provider `turn.usage` 优先；没有 usage 时按 `chars/3` 估算 |
| `provider_calls` | 实际 provider 调用次数（含 fallback） |
| `latency_ms` | turn.completed / blocked / failed 的累计耗时 |
| `turns_started` | 实际发言回合数 |
| `a2a_invokes` / `a2a_skipped` | 动态路由次数与被吸收的重复邀请 |
| `redundancy` | 多个 agent 消息之间的 3-gram Jaccard 相似度均值 |
| `coverage_loss` | 最终消息得分低于“最好的中间消息”时损失了多少 |
| `failure_rate` | mission 未 completed 的比例 |
| `tokens_per_success` | 总 tokens / 成功 trial 数；无成功时为 `None` |
| `cost_usd` | 只有 Provider 返回 cost 时才有；否则为 `None` |

统计口径：

- **配对 Δ**：同一 task/repeat 下 `arm_score - A_score`，先配对再平均，避免任务难度差异。
- **95% CI**：对配对 Δ 做 percentile bootstrap（默认 2000 次）。
- **W/T/L**：相对 A 的胜/平/负任务数。
- **sign test p**：只看胜负的精确二项检验，平局不计。
- **Pareto**：在“平均分 vs 平均 tokens”平面上标记被支配的 arm。
- **预登记对照**：报告同时给出 B−A（额外检查）、C−B（同样 4 回合下换执行身份）、D−C（同骨架下动态路由）；A 基线表继续用于“多 Agent 何时更差”的退化分析。

## 4. 运行

### 4.1 链路自检（不调用模型）

```bash
.venv/bin/python scripts/run_eval.py --mode fixture
```

`fixture` 模式回放 `eval/fixtures/scripted_answers.json` 中**人工编写**的回答，只用于验证：

- 四个 arm 的回合数是否符合预期；
- A2A 是否真的被触发；
- 评分、配对统计、报告生成是否正常。

**它不能作为模型能力证据。** 报告顶部会明确写出这条警告。

### 4.2 真实对比（会产生模型调用和费用）

```bash
# 第一次建议小范围：两类任务、1 次重复
.venv/bin/python scripts/run_eval.py \
  --mode real --provider codex \
  --categories simple_factual,multi_constraint \
  --repeats 1

# 正式对比：全部任务、3 次重复
.venv/bin/python scripts/run_eval.py \
  --mode real --provider codex \
  --repeats 3 \
  --output output/eval/codex-2026-10-18
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--arms A,B,C,D` | 只跑部分 arm |
| `--categories` | 按类别过滤 |
| `--task-ids` | 按任务 id 过滤 |
| `--lead` | 四组共用的 lead agent |
| `--max-a2a` | D 组的动态 A2A 预算；最多插入 2 个宿主回合 |
| `--persona-mode` | `neutral`（默认，去掉人格口吻）或 `project`（使用 `config/agents.json`） |
| `--repeats` | 每个 task/arm 的重复次数 |
| `--seed` | 随机顺序与 bootstrap 的种子 |
| `--resume` | 跳过 `trials.jsonl` 中已存在的 task/arm/repeat；仅在同一 output、任务集和配置下使用 |
| `--quiet` | 不打印逐 trial 进度 |

`real` 模式会为每个 trial 新建 `ProviderRouterAdapter`。需要对应 CLI 已安装、已登录；评测会临时把目标 Provider 标记为 enabled，但不会替你完成认证。

## 5. 输出

每次运行写入 `--output` 目录：

```text
report.md        # 人读报告
summary.json     # 机读汇总：overall / by_category / pairwise / contrasts / degradation / pareto
trials.jsonl     # 每个 trial 一行，含答案、分数、指标、失败检查项
trials.csv       # 表格版，便于 pandas / Excel
eval.sqlite3     # 本次运行的完整 mission / message / event 记录
artifacts/       # 本次运行的产物
```

报告包含：

1. **Takeaways**：自动给出最高分、最低 tokens/success、三组关键对照和类别级回退。
2. **Headline**：每个 arm 的平均分、成功率、tokens、tokens/success、调用数、延迟、turns、冗余、失败率。
3. **Paired comparisons against A**：B/C/D 相对 A 的 Δ、95% CI、W/T/L、sign test。
4. **Key contrasts**：B−A（额外检查）、C−B（同骨架下换执行身份）、D−C（同骨架下动态路由）。
5. **Score by task category** 与 **Paired contrasts by category**：按类别看谁强谁弱、差异来自哪一组对照。
6. **Coordination cost by category**：同类别下的 token 成本。
7. **Score by task**：逐任务分数与 D−A。
8. **When is multi-agent worse?**：自动标出类别级/任务级质量回退，以及“≥2× tokens 且没有质量收益”的 overhead。
9. **Cost–quality frontier**：Pareto 支配关系。
10. **Intermediate-message coverage loss**：最终消息丢掉了中间消息里的有效信息。
11. **Appendix: trials**：每个 trial 的分数、tokens、调用数、A2A 次数、失败检查项。

## 6. 如何解读“Multi-Agent 更差”

不要只看总平均分。先看这三类信号：

- **类别级质量回退**：例如 `simple_factual` 上 C/D 低于 A。常见原因是检查/修订回合把简单答案扩写成解释，或引入了未验证的补充。
- **成本回退**：B/C/D 的 tokens 可能达到 A 的 2–6 倍，但分数没有提高。这属于“协调/反思税”，不是能力提升。
- **覆盖损失**：中间某个 agent 已经说对了，但最终修订消息丢了它。`coverage_loss > 0.1` 的 trial 值得逐条看 `trials.jsonl` 的 `answer` 和 `checks`。

再看 **D vs C**：

- D 与 C 的宿主回合数相同，说明 D 没有实际使用动态 A2A；此时 D−C 主要反映同一骨架下的个体差异，而不是动态路由收益。
- D 的宿主回合数高于 C，且分数没有提高，说明“不必要委派”或“委派结果没被最终交付采用”。
- D 的 A2A 命中率高、且 `coverage_loss` 低时，动态路由才真正占优。
- D 的 A2A 回合很多但最终答案没有引用接力结果，应优先看 `a2a_invokes` 与 `coverage_loss`，不要只看分数。

## 7. 有效性边界

这套评测目前是 **rubric-based**，不是人类评审的替代品：

- 关键词/格式检查可以被“堆词”命中；报告应配合人工抽查 `trials.jsonl` 中的答案。
- 12 个任务、1–3 次重复只能给出方向性结论；正式结论建议每类至少 3 个任务、每 task/arm 至少 3–5 次重复。
- `real` 模式必须固定 Provider；否则 C/D 的差异混入了模型差异。`--provider` 只钉住 CLI，不保证模型版本、推理强度或 CLI 内部子 Agent 完全一致；正式实验需在环境里记录这些版本信息。
- 当前没有禁用 CLI 自带的隐藏 sub-agent / delegation 能力；如果某个 Provider 会在内部自行委派，A/B 就不是严格的单 Agent 对照。
- 默认 `neutral` 人格只去掉口吻和人格指令，不改变 agent id、工具或 Provider；如果要研究人格本身，用 `--persona-mode project` 另跑，不要把两种结论混在一起。当前 project 模式还没有做角色排列平衡，只能作为产品配置探索。
- Prompt 脚手架本身是 treatment 的一部分：A/B 用单 Agent prompt，C/D 用多 Agent prompt。B 和 C 的 4 个阶段目的一致，但执行身份不同；解释结论时要说明。
- `fixture` 模式是评测链路的单元测试，不是模型实验。
- token 估算在 Provider 不返回 usage 时使用 `chars/3`，只适合做相对比较。
- 当前评分对象是最终 agent 消息，不是文件/产物快照；需要验证代码或交付文件时，应增加 artifact 检查器。
- 当前没有硬 token 预算守卫；`real` 模式是事后计量，不能声称“严格等预算”。需要硬预算结论时，应先把 provider 的硬限额与完整用量接入。
- 任务集是 12 个闭卷 pilot，不是 48 个锁定任务；不要把一次小样本结果当成最终路由结论。任务题型较常见，无法排除预训练污染；正式结论需要新的锁定任务或私有数据。

## 8. 扩展任务

在 `eval/tasks.json` 里加一个对象即可：

```json
{
  "id": "my-01",
  "category": "my_category",
  "title": "任务标题",
  "goal": "给模型的完整用户请求",
  "difficulty": "medium",
  "rubric": {
    "threshold": 0.8,
    "checks": [
      {"id": "must", "kind": "contains_all", "values": ["A", "B"], "weight": 2},
      {"id": "short", "kind": "max_chars", "value": 300, "weight": 1}
    ]
  }
}
```

如果是 `fixture` 模式，还要在 `eval/fixtures/scripted_answers.json` 里补对应内容：

```json
{
  "my-01": {
    "A": {"final": "..."},
    "B": {"draft": "...", "checks": ["检查一...", "检查二..."], "final": "..."},
    "C": {"draft": "...", "checks": ["检查一...", "检查二..."], "final": "..."},
    "D": {
      "draft": "...",
      "checks": ["检查一...", "检查二..."],
      "final": "...",
      "dynamic": ["接力回合的回复"],
      "delegations": [
        {"when": "检查二", "target_agent_id": "baobao", "purpose": "复核", "mode": "fire_and_forget"}
      ]
    }
  }
}
```

`when` 取 `起草` / `检查一` / `检查二` / `修订`；`dynamic` 按实际发生的动态回合顺序回放。测试会检查每个任务和 arm 的 final/draft/checks 覆盖。

## 9. 人格探索（“不同人物”）

如果问题里的“人物”指的是张楚岚 / 冯宝宝 / 王也这三个人格，不要把人格和架构混在同一个主实验里：

```bash
for lead in chulan baobao wangye; do
  .venv/bin/python scripts/run_eval.py \
    --mode real --provider codex --persona-mode project \
    --lead "$lead" --repeats 3 \
    --output "output/eval/persona-$lead"
done
```

- 每次只改变 lead 人格，任务、Provider、回合骨架保持一致。
- A/B 由该 lead 执行；C/D 仍使用完整参与者列表，便于观察“谁开场/收尾”的影响。
- 当前没有做六种角色排列的平衡，也没有把人格差异和模型差异分开；这些结果只能作为探索性证据。正式人格结论应使用新锁定任务，并按角色排列做平衡。

## 10. 后续方向

- 盲评 LLM judge：对 A/B/C/D 的最终答案做匿名成对比较，作为 rubric 的补充。
- 工具使用任务：在隔离 workspace 中验证文件产物、测试通过率和命令执行结果。
- 预算匹配与消融：B/C 已经是同样的 4 回合；下一步加入 B6/C6（最终修订前再多两次检查），区分“更多检查”与“换执行身份”的贡献；D 的收益还需与同样计算量的 C 对照。
- 统计模型：任务难度分层、混合效应模型、按类别估计交互效应。
- 评审 UI：在页面里直接查看某次评测的 trial 对话和失败检查项。
