# 拆解 waku-agent 的可观测性与 Evals

> 研究对象：[ShenSeanChen/waku-agent](https://github.com/ShenSeanChen/waku-agent)（main @ `4b69b32`）
> 目的：不是搬运代码，而是拆出它做对的**结构决定**，再对照 AgentClub 现有评测体系，指出缺的那一半。
> 建议阅读顺序见 §7；只想看"我们能抄什么"直接跳 §5。

---

## 0. 先给结论

这个项目在 LLM-Ops 上真正值得学的，不是它用了 OpenTelemetry 或 DeepEval，而是七个很克制的结构决定：

1. **可观测性的单位是"回合"（turn），不是进程、不是请求、也不是 mission。** 一次用户输入到最终回复就是一个 turn，有 `turn_id`，所有记录围绕它对齐。
2. **trace 是最朴素的 append-only JSONL，一个依赖都不需要**；OTel 只是同一批事件的第二种出口，不是必需品。
3. **采集用 Observer 回调，agent 循环完全不认识 tracer**。`waku/ops/` 整个目录删掉，agent 照样能跑。
4. **存 tokens，不存价格。** 价格是读取时算出来的视图，所以改价会"追溯修正"所有历史账本。
5. **Evals 分三种问题、三套尺子**：0/1 的确定性检查、0–1 的 judge 评分、需要 Docker 的环境级检查。**永远不混**。
6. **最有价值的一个想法：turn checks（读时评分）。** 五个纯函数在读 trace 的时候现场算，不落库；改一条检查，历史所有回合立刻重新评分。
7. **judge 是要花钱、会漂移的模型调用**，所以按需触发、先报价、写一次、冷却 60 秒、把"谁判的"写进结论里，并且**judge 自己也有测试**。

一句话概括它的取舍：**可观测性负责"发生了什么"，Evals 负责"这算不算好"，两者共用同一份 trace，谁也不替谁下结论。**

---

## 1. 它把可观测性与 Evals 放在什么位置

README 把整个项目压成四根支柱：Harness · Loop · Memory · **Eval/LLM-Ops**。第四根支柱全部在 `waku/ops/` 里，而 `waku/ops/README.md` 第一段就写明了它和主循环的关系：

> Everything here answers one of two questions: **what did the agent just do?** (tracing, the dashboard) and **is it any good?** (arena, judge, scoring, the release gate). Nothing here is part of the agent loop — you can delete this whole directory and waku still runs. That's deliberate: **ops observes, it never participates.**

这个约束换来了两件事：

- 主循环 `waku/loop/agent.py` 只有 209 行，不掺任何埋点逻辑；
- 所有 ops 模块都是"普通 Python 入参 → 普通 dict 出参"，因此**不启 HTTP 服务也能测**，`evals/deterministic/` 可以直接调它们。

```
                        ┌──────────────────────────────────────────┐
   用户输入 ──▶ turn ──▶ │ loop: llm → tool → llm → … → reply       │
                        └──────────────┬───────────────────────────┘
                                       │ notify(kind, event)   ← Observer
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              tracer.event()      gateway 实时 UI    metered() 小模型调用
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
  traces/<date>.jsonl      usage.jsonl（永久账本）
        │                        │
        └────────┬───────────────┘
                 ▼
        observability.py（读）+ turn_evals.py（判）
                 │
     ┌───────────┼────────────┬─────────────┐
     ▼           ▼            ▼             ▼
  Turns 页    Spend 页    Evals 页     release_gate.py
                                            │
                                    eval_report.json / eval_runs.jsonl
```

三层职责分得很干净，值得直接照搬：

| 层 | 文件 | 只做一件事 |
|---|---|---|
| 写 | `tracing.py`（239 行） | 把事件落成 JSONL + 可选 OTel |
| 读 | `observability.py`（1114 行） | 把 JSONL 还原成 turn / steps / 花费 / 统计 |
| 判 | `turn_evals.py`（949 行）、`scoring.py`、`judge.py` | 对 turn 或对一次跑分打结论 |

---

## 2. 可观测性：一本回合账本

### 2.1 四个文件，一个 join key

它没有引入数据库、没有引入 trace 后端，全部落在 `~/.waku/`（开发时是仓库里的 `.waku/`）：

| 文件 | 内容 | 生命周期 |
|---|---|---|
| `traces/<YYYY-MM-DD>.jsonl` | 一天一个文件，一行一个事件：`turn_start` / `llm` / `tool` / `gate` / `consolidation` / `report` / `receipt` / `score` / `turn_end` | 可以为了 demo 清空 |
| `usage.jsonl` | 每一次模型调用的 tokens（`provider, model, kind, turn_id, in, out`） | **永不清理**，demo reset 也不动 |
| `eval_report.json` | 最近一次 `make gate` 的判定 | 覆盖写 |
| `eval_runs.jsonl` | 每次 gate 一行，构成评测历史 | 追加 |

把它们缝在一起的是 `turn_id`（`"t_" + secrets.token_hex(8)`）：

- trace 里每个事件都盖 `turn_id`；
- 每一次 LLM 调用在账本里带 `turn_id`；
- judge 或人类事后打的分数，是一条 `score` 事件，用 `turn_id` 指回回合；
- 收据（receipt）把这一回合的模型、工具、记忆、总价合成一条 `receipt` 事件。

这个设计的关键点：**同一份事实只写一次，其他视图都是推导出来的。** 收据不是"另一份真相"，它是 trace 事件的一个投影；Spend 页是 `usage.jsonl` 的投影；Evals 页是 trace 的投影。

### 2.2 trace 的 schema：真实长什么样

`evals/fixtures/turns/2026-10-05-rehearsal-zero-claim.jsonl` 是仓库里真实保存的一条 fixture（被测试直接当输入用），摘几行：

```json
{"type":"turn_start","turn_id":"t_5e1f0c2a9b7d4e36","user_message":"research with treg the competitors of mem0 and waku.one","ts":"2026-10-05T14:02:00.000+00:00"}
{"type":"gate","decision":"retrieve","reason":"Research on named companies","ts":"2026-10-05T14:02:00.800+00:00"}
{"type":"tool","tool":"treg_call","output":"{...\"cost_usd\": 0.0089}","duration_ms":1310,"v":2,
 "turn_id":"t_5e1f0c2a9b7d4e36","args":{"endpoint_id":"tomba.companies.similar","query":{"domain":"mem0.ai"}},
 "source":"treg","span":"tool","ok":true,"cost_usd":0.0089,"provider":"tomba",
 "endpoint_id":"tomba.companies.similar","gen_ai.operation.name":"execute_tool",
 "gen_ai.tool.type":"extension","gen_ai.tool.call.id":"tu_94"}
{"type":"llm","provider":"waku-platform","model":"claude-sonnet-5","iteration":2,"stop_reason":"tool_use",
 "usage":{"in":13724,"out":214},"turn_id":"t_5e1f0c2a9b7d4e36","cost_usd":0.044382,
 "span":"llm","gen_ai.operation.name":"chat","ts":"2026-10-05T14:02:08.000+00:00"}
```

几个刻意的细节，都能在 `tracing.py` 的模块 docstring 里找到理由：

- **`v: 2` 是 schema 版本号，不是装饰。** v2 相比 v1 加了 `turn_id`、完整 `output`（v1 会截断）、`duration_ms`、以及 `span` 种类。读取侧 `describe()` 同时兼容 v1/v2：老 trace 能读出多少算多少，读不出的字段就不假装有。
- **`span` 只有 7 种**：`llm / tool / retrieval / memory_write / gate / route / receipt`。它没有发明一套复杂的 span 树，而是给每一行贴一个"这一步是什么"的标签，够画瀑布图、够做聚合。
- **`args` 截到 500 字符并脱敏，`output` 整段保留。** 理由很实在：调试要看输出；参数里最容易藏密钥。
- **`gen_ai.*` 字段在 JSONL 里就写好了**（只写 Waku 自己没有等价名字的那几个），而不是等到导出 OTel 时才映射。这样 grep 一个文件就能按 semconv 名字过滤。

### 2.3 采集：Observer，而不是埋点

主循环只暴露一个回调：

```python
# waku/loop/agent.py
Observer = Callable[[str, LoopEvent], None]

def run_loop(..., observer: Observer | None = None, ...):
    notify = observer or (lambda kind, ev: None)
    ...
    notify("llm", {"iteration": iteration, "stop_reason": response.stop_reason,
                   "usage": {"in": ..., "out": ...}})
    ...
    notify("tool", {"tool": call.name, "args": call.input, "output": output,
                    "call_id": call.id, "duration_ms": ...})
```

`Tracer` 本身就实现了这个签名，所以"接上 tracer"这件事是 `Tracer.event`，而不是 import tracer：

```python
# waku/ops/tracing.py
def event(self, kind: str, event: dict) -> None:
    if kind == "text":
        return                      # 流式 token 是给 UI 的，不进 trace
    if kind == "llm":
        self._record_usage(event)   # ← 账本
        ...
    elif kind == "tool":
        event = observability.trace_record(event, turn_id=self.turn_id, servers=self._mcp_servers)
    self._write({"type": kind, **event})
```

再往上一层，用 `compose()` 把事件扇出给多个观察者（页面实时显示 + trace 落盘）：

```python
def compose(*observers) -> callable:
    active = [o for o in observers if o]
    def fanout(kind, event):
        for obs in active:
            obs(kind, event)
    return fanout
```

这套做法的收益：**新增一个消费者（比如把事件推到前端、推到 OTel、推到 WebSocket）不需要改循环。**

#### 一个容易被忽略的坑：副作用调用也要计费

主循环自己的 `llm` 调用有 `notify`，但围绕循环还有一堆小模型调用——检索门（gate）、记忆归并（consolidation）、报告分类（report）、分流（triage）、快捷回复（quick）。这些调用以前**不进账本，但 provider 照样收钱**。它的解法是一个很轻的装饰器：

```python
# waku/ops/tracing.py
class metered:
    """A model client whose `messages.create` also reports the call as an
    `llm` event with its `kind` and model."""
    def __init__(self, client, kind: str, notify) -> None:
        self.messages = _MeteredMessages(client, kind, notify)
```

于是账本里每一行都有 `kind`：`loop / gate / consolidation / report / triage / quick / final / subagent / judge`。**这一条直接决定了成本报表能不能信。**

### 2.4 成本：tokens 是事实，价格是视图

`pricing.py` 的 docstring 只有一句话，但这句话是整套设计的支点：

> It deliberately does NOT store a price. Prices change; tokens don't. So cost is derived HERE, at read time.

价格查询是三层，依次回退：

1. `_price_cache`：从 provider 的在线 catalog 学到的**按模型**精确价（OpenRouter 会返回价格）；
2. `MODEL_PRICING`：手工维护的按模型价（没有 catalog 的 endpoint，比如 anthropic wire）；
3. `PRICING`：按 provider 的粗估，UI 标注 "est"。

因为价格不入库，**改对一条费率，历史上所有比赛、所有花费图表会一起被修正**。这是"存事实、推视图"最漂亮的一个例子。

账本行长这样（`kind` 是灵魂）：

```json
{"ts":"2026-10-05T14:02:08.000+00:00","provider":"waku-platform","model":"claude-sonnet-5",
 "kind":"loop","turn_id":"t_5e1f...","in":13724,"out":214}
```

### 2.5 读取：group → build → payload

`observability.py` 的读取路径只有三个函数，值得记住：

1. `read_events(home)`：扫 `traces/*.jsonl`，逐行 `json.loads`，坏行跳过（不是崩掉）；
2. `group_turns(events)`：以 `turn_start` / `turn_end` 切分，`unfinished` 的回合也保留；
3. `build_turn(turn, ...)`：把事件折成 `steps`、时间轴、`hotspots`、`errors`、`story`、`scores`。

这里有一个很聪明的近似：**一行日志是在这一步结束时写下的**，所以"上一条时间和这一条时间的差"约等于这一步的耗时；如果工具事件自己带了 `duration_ms`（v2 有），就用精确值覆盖近似值：

```python
previous = start
for step in steps:
    at = _ts(step["ts"])
    step["at_ms"]   = int((at - start).total_seconds() * 1000) if at and start else None
    span = int((at - previous).total_seconds() * 1000) if at and previous else None
    if isinstance(step.get("duration_ms"), int):
        span = step["duration_ms"]
    step["span_ms"] = max(span, 0) if span is not None else None
    previous = at or previous
```

「不为了精确计时引入额外埋点，先用日志时间戳做一版，有更准的再覆盖」——这是很实用的工程判断。

`payload()` 是给页面的唯一入口，一次返回全部：`summary / spend_by_day / turns / tools / memory / spend / evals`。**没有 N+1 请求，没有前端拼装业务逻辑。** 注意 `turns` 被截到 `MAX_TURNS = 50`：可观测性页面读的是最近 50 个回合，不是全库。

### 2.6 OTel：一张映射表，别的地方都不知道 OTel

只有设置了 `OTEL_EXPORTER_OTLP_ENDPOINT` 才会尝试导入 opentelemetry（`pip install 'waku-agent[tracing]'`），导入失败就打一行提示继续跑，JSONL 不受影响。

映射集中在一个 dict 里：

```python
GENAI = {
    "llm":  {"provider": "gen_ai.provider.name", "model": "gen_ai.request.model",
             "usage.in": "gen_ai.usage.input_tokens", "usage.out": "gen_ai.usage.output_tokens",
             "stop_reason": "gen_ai.response.finish_reasons"},
    "tool": {"tool": "gen_ai.tool.name", "call_id": "gen_ai.tool.call.id",
             "query": "gen_ai.memory.query.text", "results": "gen_ai.memory.record.count"},
    "score": {"name": "gen_ai.evaluation.name", "value": "gen_ai.evaluation.score.value",
              "note": "gen_ai.evaluation.explanation"},
}
```

同时写 `openinference.span.kind`（`LLM` / `TOOL` / `AGENT` / `CHAIN`），这样 Phoenix 能直接画出漂亮的 span 树；Langfuse 走同一个 OTel 开关。**一个映射表 + 一个可选依赖 = 两家后端都支持**，没有为每个后端写适配器。

一个细节：每回合结束 `force_flush(timeout_millis=2000)`，理由是"进程被 kill 也要留下 trace"。这说明它的 trace 是**产品的一部分**，不是开发期的辅助。

### 2.7 隐私：两处 redact，一个白名单

- 写入时：`trace_record()` 对 `args` 调 `redact()` + 截断；
- 读取时：页面 `preview()` 再 redact 一次（防老数据、防未来新字段）。

`redact()` 的规则是"键名像密钥"和"值长得像密钥"两条都查：

```python
_SECRET_KEY = re.compile(r"(authorization|apikey|token|secret|password|passwd|cookie|"
                         r"credential|privatekey|accesskey|xapikey|bearer)", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(Bearer\s+[A-Za-z0-9._~+/=-]{8,}|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}|...")
```

而收据（`receipt.py`）用的是相反策略——**白名单复制**，不做黑名单过滤：

```python
KEYS = ("turn_id", "model", "tools", "memory", "total_usd", "credits")
# 只从事件里"抄"这 6 个键，其余一律不带，所以参数、输出、prompt、回复、记忆正文
# 根本进不了收据。
```

这条对 AgentClub 尤其重要：你们要把回合数据展示给用户、写进报告、发给第三方做 judge，**"复制白名单"比"过滤黑名单"安全一个量级**。

---

## 3. Evals：三种问题，三套尺子

### 3.1 分层纪律

`docs/evals.md` 开篇就立规矩：

> *"Did it create the right calendar event?"* is a unit test: 0 or 1, and no model judges it. *"Was the reply helpful?"* is a judged score with a threshold. **Conflating the two is the most common eval mistake**, so here they are separate suites you can diff.

落到命令上：

| 命令 | 问题 | 判定 | 何时跑 |
|---|---|---|---|
| `make eval` | 该触发的工具触发了吗？参数对吗？ | 0/1（pytest） | 每个 PR，CI 里跑，无 API key |
| `make eval-judge` | 回答有帮助吗？用了记忆吗？ | 0–1 + 阈值（DeepEval `GEval`） | 需要 key，本地跑 |
| `make gate` | 能不能发版？ | 确定性必须 100%，judge 必须过阈值 | 发版前 |
| `pytest evals/hosted_docker` | 部署隔离、备份、时区…… | 0/1，但需要 Docker daemon | 独立的 CI job |

第三层（Docker）特别值得注意：它**同样是 0/1、同样离线**，但因为需要 daemon，被单独分出去，**不进 `make gate`**，没有 daemon 时整个目录 skip 并说明原因。这是"按运行环境分层的 eval"，不是按重要性分层。

### 3.2 离线层与在线层：`ScriptedClient`

`evals/deterministic/test_tool_trigger.py` 的 docstring 把两层的分工写得很清楚：

> **offline** — scripted model, always runs, tests OUR code (loop, tools, wiring)
> **live** — real model, runs when the active provider has a key, tests the MODEL+PROMPT behavior on `evals/dataset.jsonl`

离线层的核心是一个 20 行的假模型：

```python
class ScriptedClient:
    """Plays back a fixed list of responses — the 'model' for offline tests."""
    def __init__(self, script: list):
        self._script = list(script)
        self.messages = SimpleNamespace(create=self._create)
    def _create(self, **kwargs):
        return self._script.pop(0)
```

于是一个"循环三次、第二次调工具、第三次回答"的回合，可以用三个 canned response 精确重放。**测试的是自己的编排代码，不是模型的智商。**

在线层则是数据驱动的，一行一个 case：

```json
{"id":"schedule-basic","input":"Schedule a coffee with Alex next Tuesday at 9am",
 "expect_tool":"create_event","expect_in_args":{"title":"alex","start":"T09:00"}}
```

用 `@pytest.mark.parametrize` 展开，断言的是**工具调用**而不是自然语言：

```python
fired = [c["tool"] for c in result.tool_calls]
assert case["expect_tool"] in fired
args = next(c["args"] for c in result.tool_calls if c["tool"] == case["expect_tool"])
for key, needle in case.get("expect_in_args", {}).items():
    assert needle.lower() in str(args.get(key, "")).lower()
```

这就是 tau-bench / SWE-bench 的思路落到本地：**看最终状态，不看措辞。**

### 3.3 一个 scorer，两个出口

`scoring.py` 的 docstring 只有一句重点：

> Both `scripts/shootout.py` (the CLI table) and the Compare arena (the live dashboard scoreboard) score a model's run the same way … Keeping that judgment here means **the terminal number and the on-screen number can never drift apart.**

`check_case(case, tool_calls) -> (bool, why)` 是唯一实现。命令行跑分和网页跑分共用它，不可能出现"网页 82%、终端 79%"这种事故。

### 3.4 回合检查：把 eval 从"发版前跑一次"变成"每一轮都评分"

这是整个仓库最值得抄的一个设计（`waku/ops/turn_evals.py`，spec 015）。

**背景**：`evals/deterministic/` 评的是 Waku 这个**代码库**；但用户自己跑的那些回合，没人评。于是它加了五个检查，直接读用户 trace 里的**每个历史回合**：

| 检查 | 通过条件 |
|---|---|
| `spend_claim` | 回复里说的钱，和 trace 里 treg 调用实际花的钱一致（误差 > $0.01 且 > 20% 才算错） |
| `one_report` | 一次研究回合恰好存了一份报告（说了"别存"却存了 → fail） |
| `grounded_numbers` | 回复里 90% 的金额/百分比/日期/两位数出现在工具输出、记忆或用户消息里 |
| `errors_handled` | 每个失败的工具调用要么重试过，要么回复里提到了它失败 |
| `under_budget` | 本回合花费 ≤ `WAKU_TURN_BUDGET_USD`（默认 $1） |

三个设计决定，每一个都值得单独抄：

**(1) 读时计算，不落库。**

```python
def build_turn(turn, ...):
    ...
    "scores": turn_evals.run_checks(turn, usd=..., skills=skills, servers=servers)
              + _stored_scores(turn, scores or []),
```

`run_checks` 在页面每次读 trace 时跑。所以：

- 新增一条检查，**历史所有回合立刻有了分**；
- 修好一个检查的 bug，**历史所有回合立刻重新评分**；
- 不写磁盘、不需要迁移、不占空间。

代价是每次读要重算，但五个纯函数在 50 个回合上毫无压力。**"可重算"比"已存储"更适合快速演进的检查。**

**(2) `n/a` 是一等公民。**

```python
PASS, FAIL, NA = 1, 0, None
```

看 `grounded_numbers` 的注释，`n/a` 的条件写得很较真：

- 回复里数字少于 3 个 → n/a（样本不足）；
- 老 trace（没有 turn_id）→ n/a（输出被截断，无法判断）；
- 整个回合只调了 Waku Memory → **n/a 而不是 fail**，因为回复可能引用了聊天历史里的上一轮内容，而 trace 不保存聊天历史。

文档里有一句原则性的话：

> A check fails only when a reader of the trace would agree; when the trace cannot settle it, the check says n/a and its note says why.

这条极其重要。**告警系统死于误报**：一个频繁误报 fail 的检查，两周后所有人都会忽略它。宁可 n/a，也不要"猜着 fail"。

**(3) 只评分，不干预。**

> A check grades a finished turn. It never stops, rewrites or delays a reply.

检查跑在回合结束之后，不影响回复、不阻塞用户、失败也不显示在聊天里（只在 Observability 页面显示 chip）。**评测是旁路，不是守门员**——守门员是 release gate，那是另一个东西。

聚合层 `your_turns()` 按窗口（today / 7d / all）给出每个检查的 passed / failed / n/a / pass_rate / 最近一次失败，命令行 `waku evals turns --window 7d` 输出同一张表。**同一份数据，两个出口**（又是这个模式）。

### 3.5 judge 的工程化：一个会花钱、会漂移的模型调用

"Judge this turn" 是页面上的一个按钮，点它才跑。围绕这一次调用，它做了八件事：

1. **先报价**：`judge_offer()` 用 `len(prompt)/4` 估输入 token、加 300 输出 token，按当前价目表算出 "about $0.002 with claude-haiku-4-5"，显示在按钮上；
2. **用当前 provider 的小模型**：不给用户增加第二个 key；
3. **限制输入**：回复截 4000 字符，每个工具输出截 800、合计 6000，提示词里明写"每个输出截断到 N 字符"；
4. **结构化输出 + 双维度**：要求返回 `{"addresses_question": 0-10, "grounded": 0-10, "reason": "..."}`，取**两者较低分** / 10 作为最终分（保守）；
5. **只写一次**：结果作为一条 `score` 事件 append 到 trace（`source: "judge"`），带 `judge` 模型名和 `cost_usd`；不覆盖、不改写；
6. **60 秒冷却**：同一回合一分钟内不能重复判（进程内 `_RECENT` + 已落盘 score 的双重检查）；
7. **判定模型写进结论**：`note` 里带 `(judged by claude-haiku-4-5, $0.002)`，并在文档里承认"judge 常常和 agent 同家族，会偏袒自己的答案，所以必须写明是谁判的"；
8. **judge 的调用本身也进账本**：`_record_usage(..., kind="judge")`，所以 Spend 页能算出"评判花了多少钱"。

还有一条**方法论**上的规矩，写在 `judge.py` 里（这是跑分竞技场用的裁判）：

> The referee must be a model that ISN'T racing — otherwise it grades itself … you can't test K3 with K3 as the judge.

于是裁判默认是一个**不适合当选手但适合当裁判**的模型，并且可配置。

### 3.6 release gate 与评测历史

`release_gate.py` 只有 92 行，但把"发版决策"做成了可审计的：

```
make gate
 ├─ pytest evals/deterministic  → 退出码非 0 → 写 {deterministic: fail, judge: not run} → exit 1
 ├─ 有没有 key？
 │    ├─ 没有 → 写 {deterministic: pass, judge: skipped}
 │    └─ 有   → pytest evals/judge → 非 0 → 写 {pass, fail} → exit 1
 └─ 写 {pass, pass} → GATE OPEN
```

两个文件：`eval_report.json`（最新一次，覆盖写）和 `eval_runs.jsonl`（每次一行，追加）。Evals 页读这两个文件，展示"最近一次 gate 每个 suite 过了多少 / 失败多少 / 判定是什么"，以及一张**评测历史表**（每次 gate 一行）。

注意它**不数"有多少个测试函数"当作结果**，而是记录"这次运行 passed 多少 / failed 多少"。`last_run()` 的注释写得很明确：

> The page shows these, never a count of test functions, as the run's result.

再往上，CI 只跑确定性层（judge 需要 key），并把 junit 计数写成一条 GitHub check annotation：

```yaml
- name: Deterministic evals (offline tier — no API key in CI)
  run: python -m pytest -q evals/deterministic --junitxml=/tmp/deterministic.xml
- name: Report the deterministic counts
  run: python scripts/ci_test_counts.py "Deterministic evals" /tmp/deterministic.xml
```

`ci_test_counts.py` 打印 `::notice title=Deterministic evals::3094 passed, 0 failed, 2 skipped`。然后部署脚本在部署时**通过 GitHub 公开 API 把这条 annotation 读回来**，写进 release record，容器里再读到它，Evals 页就能显示：

> 你正在跑的这版代码 = commit abc1234，CI 上 3094 通过 / 0 失败 / 2 跳过，这些是 required checks。

**这就把"评测结果"和"线上到底跑的是哪一版"焊死在一起了。** 大多数团队的可观测性页面缺的正是这一块：不知道眼前的数据是哪版代码产生的。

### 3.7 评测系统自己也要被测

四个层次，缺一不可：

1. **judge 的管道**（`evals/deterministic/test_judge.py`）：stub 掉 client，只测解析、0–10 夹取、从前后废话里抠 JSON、空回复不判、坏 JSON 降级为 `None`。**"裁判打喷嚏不能弄崩比赛"**。
2. **judge 的品味**（`evals/judge/test_turn_judge.py`）：用真实 fixture trace，把"说 $0.00 但实际花了 $0.0089"的回复喂进去，断言分数 < 0.7；把有依据的版本喂进去，断言 ≥ 0.7。**这测的是"裁判能不能分辨对错"，而不是"裁判喜不喜欢"**。
3. **检查的语义**：`evals/fixtures/turns/` 里存了一批手工构造的回合（`over-budget.jsonl`、`silent-failure.jsonl`、`two-reports.jsonl`、`no-report-asked.jsonl`……），每一个都对应一条检查的边界。**fixture 即规格。**
4. **schema 契约**：`receipt.py` 的 `KEYS` / `MODEL_KEYS` / `TOOL_KEYS` 被 `test_turn_receipt.py` 钉死；加字段是一次"刻意的破坏性变更"，必须同时改测试。

---

## 4. 展示层：四个 tab 讲完一个故事

Observability 页面（`/api/observability`，一个 JSON 全给）分四块：

| Tab | 回答的问题 | 数据来源 |
|---|---|---|
| **Turns** | 这一轮到底做了什么？ | 每个回合一行一个 chip（"5 of 5 checks" 或第一个失败），点开是瀑布图 |
| **Spend** | 钱花在哪了？ | `usage.jsonl` + 收据，按模型 / 按天 / 按类目（model / treg / memory / other） |
| **Tools** | 工具都调了谁？ | 按来源分组（treg 各 endpoint 及花费、Waku Memory、本地、MCP） |
| **Evals** | 这东西现在好不好？ | 你的回合的五个检查 + 代码套件 + gate 历史 + release 记录 |

瀑布图上的每一行都带 `span` 标签和 `span_ms`，失败的行带 `error`，内存调用带 `query / results / retrieval_trace_id`。**没有花哨的图，但每一行都能追到一条日志。**

一个产品层面的取舍：`MAX_TURNS = 50`。可观测性页面不是数据库查询界面，**它是"最近发生了什么"的视图**。要看更早的，去看文件（trace 本来就是纯文本）。

---

## 5. 迁移到 AgentClub：你们缺的是"运行时的另一半"

### 5.1 对照表

先说结论：**你们的评测在"实验设计"这一侧比 waku 强，缺的是"运行时"这一侧。**

| 能力 | waku-agent | AgentClub 现状 | 判断 |
|---|---|---|---|
| 实验设计（A/B/C/D、配对、bootstrap、CI、sign test、Pareto） | 无 | **有**（`backend/app/evaluation/metrics.py`、`report.py`） | 别动，这是你们的优势 |
| 任务集与 rubric（多 kind 检查、权重、阈值） | 有（`dataset.jsonl` + 固定断言） | **有**（`eval/tasks.json`，10 种 check kind） | 你们更完整 |
| 产物级验证（跑测试、看文件） | 有（`coding_eval.py` 的 `verify` 退出码） | **有**（`pr-replay-v1/checks/artifact_contract.py`） | 持平 |
| 打包/脱离上下文复现 | 弱 | **有**（PR 回放的固定 base + 参考补丁） | 你们更强 |
| **每回合 trace（生产路径也写）** | **有**（JSONL + turn_id，工具输出完整） | 有事件级 trace（`events` 表 + `turn_id`），但**工具输出只存 summary**、无法按回合取出与判定 | **半缺** |
| **读时评分（turn checks）** | **有**（5 个纯函数 + n/a 语义） | 无：评分只在评测 runner 里对 trial 算 | **缺** |
| **成本账本 + 报价** | **有**（tokens 落库、价格推导、judge 报价） | 部分：`cost_usd` 只有 provider 返回时才有 | **缺** |
| **release gate + 评测历史** | **有**（`eval_report.json` / `eval_runs.jsonl`） | 无（有 `RELEASE_CHECKLIST.md`，但是人肉） | **缺** |
| **评测系统自身的测试** | **有**（fixture trace + 测 judge） | 部分（`backend/tests` 覆盖 runner，但没有"检查的语义"fixture） | 可补 |
| 可视化 | 有（4 个 tab） | 有（任务页、事件流、讨论页） | 各有侧重 |

### 5.2 诊断：七个缺口，两个根因

先修正一句我上一版说过头的话：**你们并不缺 trace。** `backend/app/orchestration/events.py` 已经在往 `events` 表写带 `turn_id / agent_id / provider_id` 的事件（`turn.started`、`turn.completed`、`turn.blocked`、`turn.usage`、`a2a.invoke / skipped / reply`、`tool.call / tool.result / tool.error`、`mcp.receipt`、`artifact`），`mission_detail.py` 还会按 agent / provider / turn 聚合用量。这是很多团队都没有的基础。

真正的缺口更窄，也更关键：**这份 trace 是为"页面展示"和"批次实验"建的，不是为"判定一个回合好不好"建的。** 七个具体缺口：

---

**G1｜工具输出不落盘，导致大多数"忠于执行"的检查在事实上无法判定**（根因）

证据：

```python
# orchestration/events.py — record_tool_result 只写 summary
repo.add_event(mission_id, "tool.result", {
    "tool_name": tool_name, "summary": summary, "artifact_id": artifact_id, ...})

# record_tool_call 的入参：截断 200 字符；write_artifact 的 content 直接丢弃
if tool_name == "write_artifact": sanitized.pop("content", None)
for key ...: if isinstance(value, str) and len(value) > 200: value[:200] + "...[truncated]"
```

后果：你能回答"调了哪个工具"，无法回答"工具返回了什么、回复有没有歪曲它"。比如 agent 说"Tomba 返回了 3 家相似公司"，而工具实际返回 0 条——trace 里没有那段输出，任何检查都判不了。waku 的选择相反：`trace_record()` 保留整段 `output`，只把 `args` 脱敏截断，正是因为**下游检查需要原文**。

**G2｜评分与"预定义任务集"耦合，真实回合没有评分入口**（根因）

证据：

```python
# evaluation/scoring.py
def score_answer(answer: str, rubric: Rubric) -> ScoreResult: ...

# evaluation/metrics.py
def build_trial_result(*, task: EvalTask, arm: str, repeat: int, ...) -> TrialResult:
```

rubric 来自 `eval/tasks.json` 的 12 个预定义任务。所以整条评分链路的前提是"你事先知道这是哪道题"。一个真实用户任务（"帮我看下 PR #7 为什么丢插话"）跑完，**没有任何一行代码会去问"这一轮做得对吗"**——它既没有 `task`，也没有 `rubric`。

这是和 waku 最本质的差别：waku 的 turn checks 不依赖任务集，它只依赖 trace 的**结构**（说了钱就查钱、说了存报告就查报告、失败了就查有没有提）。也就是说：**评价对象从"这道题答得对不对"变成"这一轮有没有自相矛盾"**，后者对任意输入都成立。

**G3｜没有"读时评分"，所以检查改不动、历史追不回**

证据：分数只写进 `trials.jsonl` / `eval.sqlite3`，一次运行一份。

后果：你今天想到"应该检查 agent 有没有谎称已委派"，过去三个月的数据帮不上忙——因为分数是当时算的，不是现在算的。waku 的 `build_turn()` 每次读 trace 都现场跑五个纯函数，**新增或修好一条检查，历史所有回合立刻重新评分**。对你们这种检查项还在快速演进的阶段，这一点比"省 CPU"重要得多。

**G4｜成本只有 provider 自报，覆盖不全、无法估价、无法追溯**

证据：

```python
# api/mission_detail.py — _sum()
cost_parts = [b["total_cost_usd"] for b in bucket if b["total_cost_usd"] is not None]
out["total_cost_usd"] = sum(cost_parts) if cost_parts else None
```

三个问题：① provider 不返回 `total_cost_usd` 时永远是 `None`（比如很多 CLI 只报 tokens）；② 三次调用只有两次报了价时，`sum` 会把**部分和**当成总额，页面上看不出缺了哪一次；③ 没有价格表，所以无法"先报价"（judge、新功能、预算守卫都做不了），也无法在调价后修正历史。waku 的做法是 tokens 入账、价格读时算，三个问题一次解决。

**G5｜没有 gate，也没有评测历史**

证据：`docs/RELEASE_CHECKLIST.md` 是人肉勾选；CI 只跑 `backend/tests`。

后果：CI 绿只说明"代码没坏"，不说明"agent 行为没退化"。改了 prompt、换了 CLI 版本、调了 `max_a2a` 之后，没有任何机器判定能拦住发版；也没有一张表能回答"上次全绿是什么时候、这次是哪个 suite 掉的"。waku 用 92 行 + 两个文件（`eval_report.json` / `eval_runs.jsonl`）就解决了，成本极低。

**G6｜检查项没有 fixture 规格，检查自己坏了没人知道**

证据：`backend/tests` 覆盖 runner / adapter / decision 的行为，但没有"这条检查在什么输入下必须 fail / 必须 n/a"的样本集。

后果：一条检查写错了（比如把合法的"我不会委派"判成谎称委派），你不会知道，直到它污染了一批结论。waku 把 `evals/fixtures/turns/*.jsonl` 当规格用：`over-budget.jsonl`、`silent-failure.jsonl`、`two-reports.jsonl`、`no-report-asked.jsonl`——每个边界一个 fixture，检查改了先跑 fixture。

**G7｜脱敏只做了"入参截断"，没做密钥识别，也没做输出侧**

证据：

```python
# 只做了两件事：write_artifact 丢 content、长字符串截断到 200
if tool_name == "write_artifact": sanitized.pop("content", None)
if isinstance(value, str) and len(value) > 200: sanitized[key] = value[:200] + "...[truncated]"
```

后果：`{"api_key": "sk-live-..."}` 这种 200 字符以内的密钥会被**原样落库**，然后被页面、报告、导出、未来的 judge 一路带出去。waku 是"键名/值形状双正则 + 写入时脱敏 + 读取时再脱敏 + 收据白名单复制"，三层。

---

#### 同一个回合，两边各能回答什么

用户说："帮我修 PR #7 的插话丢失问题，让冯宝宝复核一下。"
agent 回复："已委派冯宝宝复核，并在 `artifacts/fix.md` 写入了修复方案；Tomba 查到 3 家相似项目。"

| 问题 | AgentClub 今天 | waku 今天 |
|---|---|---|
| 这一轮谁说的、用了哪个 provider | ✅ `message.provider_id` / `turn.started` | ✅ |
| 调了哪些工具 | ✅ `tool.call` | ✅ |
| 工具**返回了什么** | ❌ 只有 `summary` | ✅ 整段 `output` |
| 说的"3 家相似项目"对不对 | ❌ 无从判定 | ✅ `grounded_numbers` |
| 真的委派了吗 | ⚠️ 能查 `a2a.invoke`，但没有人查 | ✅ `claims_match_events`（自相矛盾即 fail） |
| 委派结果被采用了吗 | ❌ 需要人读 `a2a.reply` + 最终消息 | ⚠️ 需要一条检查（waku 也没有，但它的结构支持你加） |
| 这一轮花了多少钱 | ⚠️ provider 报了才有，且可能是部分和 | ✅ tokens × 价目表，标注 est |
| 这一轮"好不好" | ❌ 只有批次实验里的 trial 有分 | ✅ 5 个检查，读时算 |
| 这个文件真的写了吗 | ⚠️ `artifact` 事件有记录，但内容没进 trace | ⚠️ 同类问题（waku 靠 `one_report` 补） |

#### 根因与放大器

- **根因是 G1 + G2**：没有工具输出 → 检查无事可做；没有"与任务集解耦的评分入口" → 真实回合永远不被评。
- **G3–G7 是放大器**：让已有的检查难以演进（G3）、让成本数字不可信（G4）、让退化拦不住（G5）、让检查自己失修（G6）、让数据不能安全外发（G7）。

只补 G3–G7 而不动 G1/G2，会得到一套"很规范但没有输入"的评测；只补 G1/G2，会得到"能评分但改一次就重跑一遍历史"的评测。**两个根因要一起动，因为它们是同一个决定的两面：把回合当成可判定的对象。**

#### 为什么多 Agent 产品比单 Agent 更需要这一层

单 Agent 的失败长得像"答得不好"；多 Agent 的失败经常长得像**"关于协作的虚假叙述"**：

- "我已经让冯宝宝看过了" —— 实际 `a2a.skipped`；
- "冯宝宝确认没有问题" —— 实际 `reply_failed: true`；
- "综合了三个人的意见" —— 实际只有一个人发言；
- "已写入产物" —— 实际 `write_artifact` 失败。

这类失败**不会降低 rubric 分数**（因为话术本身很完整），但会摧毁用户对"团队协作"这个产品承诺的信任。它们恰好是 turn checks 最擅长抓的：**不需要知道任务是什么，只需要把回复里的声明和事件流对一遍。**

#### 三个自查（不用写代码，用现有数据就能验证）

1. 随机抽 20 个已完成 mission，逐个人工回答："这一轮 agent 说的哪句话，能被事件流证实/证伪？"——大概率超过一半的实质声明无法证实，这就是 G1。
2. 打开 `trials.jsonl`，看 `failed_check_ids` 的分布：如果绝大多数失败都来自"格式/长度"类检查，说明 rubric 抓不到真正的问题——这就是 G2 的直接症状。
3. 随便挑一个 `turn.usage` 事件，看 `total_cost_usd` 是否为 `None`；再看一个多 provider 的 mission，`mission_total.total_cost_usd` 是否等于各 turn 之和。不等，就是 G4。

### 5.3 四步落地（每步都能独立交付）

#### Step 1：让"回合"成为可读取、可判定的对象

你们的事件已经带 `turn_id`，缺的不是写，而是**读**：一个 `build_turn(repo, turn_id)` 把散在 `events` + `messages` + `missions` 里的东西折成一个对象。这是后面所有检查的前提，也是最省力的一步。

同时补上 G1 的那个洞：`tool.result` 增加完整 `output`（脱敏 + 截断），`write_artifact` 至少记录 `path + sha256 + 字数`，而不是把 content 丢掉。

```python
# backend/app/observability/turn.py
"""一个回合 = 事件流 + 该回合的消息 + 归属判定。

和 mission 级分析的区别：这里只回答"这一轮"，不回答"这个任务"。
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Step:
    kind: str            # llm / tool / a2a / route / artifact
    ts: str
    tool: str = ""
    args: dict | None = None
    output: str = ""     # 新增：完整输出（已脱敏）
    ok: bool = True
    error: str | None = None
    duration_ms: int | None = None
    agent_id: str = ""
    provider_id: str = ""

@dataclass(frozen=True)
class Turn:
    turn_id: str
    mission_id: str
    agent_id: str
    provider_id: str
    status: str                      # completed / blocked / failed
    user_message: str                # 触发这一轮的人话（或上游 a2a 的 purpose）
    reply: str                       # 该回合 agent 的最终消息
    steps: list[Step]
    usage: dict | None               # in/out/cached/cost（provider 自报）
    a2a: list[dict]                  # invoke / skipped / reply / cancelled
    artifacts: list[dict]
    reason: str | None = None        # blocked/failed 的原因


def build_turn(repo, turn_id: str) -> Turn | None:
    """按 turn_id 折出一个回合；找不到返回 None（trace 不完整是常态，不抛）。"""
    ...


def iter_turns(repo, mission_id: str) -> list[Turn]:
    """按 turn.started 的顺序切分一个 mission；为了跨 mission 统计留的口子。"""
    ...
```

有了它，G2/G3 才有落点：检查的入参从 `(answer, rubric)` 变成 `(Turn,)`，于是**不需要预定义任务也能跑**。

写入口只要一处：`repository.add_event` 的外层包一个 `record_*` 统一出口，在 `record_tool_result` / `record_tool_call` 里调 redact + truncate。**不要在每个 adapter 里各写一遍。**

#### Step 2：五个领域检查（读时算，不落库）

每个检查的签名统一是 `(turn: Turn) -> {"name", "value", "note"}`，`value ∈ {1, 0, None}`。不要传 `task`，也不要传 rubric。

```python
# backend/app/observability/checks.py
PASS, FAIL, NA = 1, 0, None

CHECKS = {
    # 对应 waku 的 errors_handled + spend_claim：说了就等于做了吗？
    "claims_match_events": _claims_match_events,
    # 你们的独有痛点：A2A 到底该不该发生、结果有没有被采用
    "a2a_justified": _a2a_justified,
    # 对应 under_budget，但用 token 而不是美元（你们更可信的计量）
    "tokens_under_budget": _tokens_under_budget,
    # 对应 one_report：声称写了产物，就必须有 artifact 记录
    "artifact_claimed": _artifact_claimed,
    # 阻塞/失败必须解释原因，不能只显示"失败"
    "blocked_explained": _blocked_explained,
}
```

`claims_match_events` 是最值钱的一个，因为你们的 Agent 会"声称"：

> 回复里出现"已委派 / 已让 X 处理 / 已写入 / 已运行测试 / 已提交"，但本回合 events 里没有对应的 `a2a.request` / `artifact` / `tool` 记录 → **fail**。

这直接对应你们 README 里承诺的"看清 Agent 到底完成了什么"，也是多 Agent 产品最容易出现的不诚信失败模式（比"回答不好"严重得多）。

`a2a_justified` 可以把你们评测里已有的 `redundancy` / `coverage_loss` 思路搬到一个回合上：

- 发生了 A2A：必须有 `purpose`，且最终消息**引用/采用了**接力结果（否则 fail，note 写"委派结果未被采用"）；
- 没发生 A2A 但任务被判为复杂：**n/a**，不 fail（该不该委派是策略问题，不是对错问题）。

每条检查都要有 `n/a` 分支和一句 `note`，且**先在历史 trace 上跑一遍统计**：如果某个检查 80% 都是 fail，那是检查写错了，不是 agent 坏了。

#### Step 3：账本与报价

你们比 waku 更容易做对这件事，因为 token 统计已经在 `metrics.py` 里了。要做的是把"事后统计"变成"每次调用就记一行"：

```json
{"ts":"...","provider":"codex","model":"gpt-6.1-sol","kind":"loop",
 "turn_id":"t_...","mission_id":"...","in":12043,"out":512}
```

`kind` 至少分：`loop / a2a / check / judge / fallback`。然后：

- 价格表放一个 `pricing.py`（三层回退：catalog → 按模型 → 按 provider 粗估），**不要把美元写进账本**；
- 页面上的成本 = `sum(tokens × price)`，并永远标注 "est"；
- judge 按钮先报价（`len(prompt)/4` 估输入 + 固定输出上限），跑完把 `cost_usd` 写进 score 事件。

#### Step 4：gate + 历史 + 页面

```bash
.venv/bin/python -m scripts.eval.gate
# 1) pytest backend/tests -q           → 必须 100%
# 2) run_eval.py --mode fixture        → 链路自检必须通过
# 3) 可选：--with-live 跑真实小样本
# 4) 写 data/eval_report.json + append data/eval_runs.jsonl
# 5) exit 0 / 1
```

然后在现有的任务/事件页面上加一个 chip：`5 of 5 checks` 或 `claims: fail`，点开显示每一行的 note。**不要新做一个"评测平台"**——waku 的经验是，评测就该长在用户已经在看的那个页面上。

### 5.4 原型结果（2026-10-20，`scripts/audit_turns.py`）

写了一个只读脚本把 `data/yirenhub.sqlite3` 里的回合折出来，把回复里的"声明"和事件流里的"事实"对一遍。全部 139 个 mission / 309 个回合：

| 指标 | 结果 | 说明 |
|---|---|---|
| 可核对声明 | 18 句 | 启发式抽取，只取"过去/完成体 + 行动动词"，排除建议、假设和明确的否定 |
| 证实 | 7（39%） | |
| 证伪 | **0** | 不是"没有说谎"，而是"没有能力判断"——见下 |
| 无法判定 | 11（61%） | |
| `tool.result` | 644 条，summary 中位数 **54 字符**，27% ≤40 字符 | G1 |
| 有 `usage` 的回合 | 208 | |
| 其中带 `total_cost_usd` | 93（**45%**） | G4 |
| 可见原生工具的回合 | **11/309（3.6%）** | 新发现，见下 |

#### 新发现：原生工具调用基本不可见（G0）

脚本一跑就暴露了一个比 G1 更靠前的问题：

| provider | 回合数 | 有 `provider.activity` 的回合 |
|---|---:|---:|
| codex | 125 | **0** |
| claude | 105 | **0** |
| antigravity | 63 | 11（17%） |
| gemini / fake | 16 | 0 |

原因在 `adapters/mcp_subprocess.py` 的落库条件：

```python
if ev.activity_id and ev.tool_name and ev.tool_name != "call_mcp_tool":
    repository.add_event(mission_id, "provider.activity", {...})
```

而 `stream_parsers/codex.py` 和 `stream_parsers/claude.py` 产出的 `TOOL_ATTEMPT` **没有 `activity_id`**（antigravity 的 parser 有）。于是两个主力 provider 的原生工具调用在 parse 阶段被识别、在落库阶段被丢弃。

后果：`chulan`（codex）和 `baobao`（claude）的回合里，只剩 MCP 层的 `log_thought / publish_message`。一个声称"我核对了代码"的回合，事件流看起来像"这轮只发了个言"。

**修复成本很低**（给 codex/claude 的 parser 补 `activity_id`，用 item id / block index 即可），但它决定了后面所有检查的分母。

#### 三个可以直接读出来的结论

1. **「0 证伪」不能当成好消息。** 在工具输出只有 54 字符中位数的情况下，任何"回复是否忠于工具输出"的判断都做不了——`grounded_numbers` 这类检查在你们这里连输入都不存在。
2. **成本覆盖 45%，且缺失不是随机的。** codex 和 antigravity 的 `total_cost_usd` 几乎全为 `None`，claude 才有。这意味着按 provider 比较成本时，缺失的那一半会被"部分和"静默拉低。
3. **讨论型回合（roundtable）的声明大多是观点，不是行动。** 对这 8 个 roundtable mission 做 claims 检查，命中率低且噪声大。所以 `claims_match_events` 应该**只对 task 模式启用**，roundtable 用别的检查（比如"引用的文件/行号是否真实存在"，那要靠文件系统而不是 trace）。

#### 脚本

```bash
.venv/bin/python scripts/audit_turns.py --limit 20            # 最近 20 个 mission
.venv/bin/python scripts/audit_turns.py --mode task --verbose # 只看任务型，逐句打印
.venv/bin/python scripts/audit_turns.py --mission m_xxx -v    # 单个 mission
.venv/bin/python scripts/audit_turns.py --json > /tmp/a.json  # 机读
```

只读（`mode=ro`）、不调用模型、不改任何生产代码。它现在是一个**注意力排序器**，不是 gate：`?` 的意思是"人去看一眼"，不是"agent 有问题"。

> **2026-10-20 后续**：G0（codex/claude 的 `activity_id`）与 G1（`tool.result` 输出）已修复，
> 并由 `GET /api/observability` + 右侧栏「观测」面板替代了原来的追踪弹窗。
> 实现与边界见 [回合观测](../OBSERVABILITY.md)。

### 5.5 两个项目应该互相借的东西

| waku 该借你们的 | 你们该借 waku 的 |
|---|---|
| 配对实验、bootstrap CI、sign test、Pareto | 回合级 trace + `turn_id` |
| 多 kind 的 rubric（`contains_all` / `json_fields` / `max_chars`…） | 读时评分 + `n/a` 语义 |
| 产物级验证（跑测试而不是看措辞） | tokens 入账 / 价格推导 |
| 固定 base 的 PR 回放（可复现性） | release gate + 评测历史 |
| 预登记对照（B−A / C−B / D−C） | fixture trace 作为检查的规格 |

---

## 6. 反模式清单（它刻意**没有**做的事）

1. **没有把 judge 放进单元测试。** 0/1 的套件里一旦出现模型判定，CI 就开始随机红。judge 单独一个 suite、单独一个命令、需要 key 才跑。
2. **没有把分数落库。** 读时算，所以检查可以随便改。落库的分数会变成"历史包袱"：改了检查，老数据永远对不上。
3. **没有让检查阻断回复。** 检查是旁路。阻断是 gate 的职责，两者别混。
4. **没有在 trace 里存价格。** 存 tokens。
5. **没有为了可观测性引入数据库/消息队列/agent 框架。** 一个 `open(..., "a")` 加 `json.dumps` 就够了；需要 span 树时再加 OTel。
6. **没有让 ops 反向依赖主循环。** `waku/ops/` 可以整目录删除。
7. **没有用参赛模型当裁判。**
8. **没有假装 n/a 是 fail。** 宁可少判，不可错判。
9. **没有把"测试函数数量"当成评测结果。** 结果 = 这次运行 passed / failed 多少。
10. **没有隐藏不确定性。** `docs/benchmarks.md` 开头就是 "The honest framing"，跑分脚本的 docstring 直接写 "don't trust the table, reproduce it"。**评测文档的可信度，来自它主动写清楚自己不能证明什么。**

---

## 7. 建议的阅读顺序

按这个顺序读，两个小时能读完核心：

| # | 文件 | 行数 | 读什么 |
|---|---|---|---|
| 1 | `docs/evals.md` | 120 | 全套心智模型，先读这个 |
| 2 | `waku/ops/tracing.py` | 239 | 整篇。Observer + JSONL + 账本 + OTel |
| 3 | `evals/fixtures/turns/*.jsonl` | — | 真实 trace 长什么样（挑两个看） |
| 4 | `waku/ops/observability.py` | 1114 | 只读 `GENAI` / `redact` / `trace_record` / `build_turn` / `payload`，其余是聚合统计，可跳 |
| 5 | `waku/ops/turn_evals.py` | 949 | 读模块 docstring + `run_checks` + `check_grounded_numbers` + `judge_turn`，其余是正则细节 |
| 6 | `waku/ops/release_gate.py` | 92 | 整篇，很短 |
| 7 | `evals/deterministic/test_tool_trigger.py` | 131 | 离线/在线两层怎么写 |
| 8 | `evals/judge/test_turn_judge.py` + `evals/deterministic/test_judge.py` | 49+68 | judge 怎么被测试 |
| 9 | `scripts/shootout.py` | 206 | "同一套任务，不同大脑"的跑分器 |
| 10 | `docs/benchmarks.md` | — | 评测的诚实框架（可以只看 §0、§1） |

配套命令（仓库根目录）：

```bash
make eval          # 0/1 层
make eval-judge    # 0–1 层（需要 key）
make gate          # 发布门
make dashboard     # localhost:7777，看 Observability / Evals 页
```

---

## 8. 一页速查

```
可观测性
  单位             turn（turn_id = "t_" + 16 hex）
  存储             ~/.waku/traces/<date>.jsonl，append-only，一天一个文件
  采集             Observer 回调 notify(kind, event)；ops 不参与循环
  账本             usage.jsonl：tokens + provider/model/kind/turn_id，永不清理
  成本             tokens 落库，价格读时算（catalog → per-model → per-provider）
  收据             receipt 事件：白名单复制 6 个键，永不粘文本
  导出             OTEL_EXPORTER_OTLP_ENDPOINT 存在才导；GENAI 一张映射表
  隐私             写入 redact 一次，读取 preview 再 redact 一次

Evals
  0/1              evals/deterministic（pytest，CI，无 key）
  0–1              evals/judge（DeepEval GEval，阈值，需要 key）
  环境级           evals/hosted_docker（0/1，需要 daemon，单独 CI job）
  回合级           turn checks：5 个纯函数，读时算，PASS/FAIL/NA，不落库
   judge           按需、先报价、写一次、60s 冷却、记录模型与花费
  跑分             scoring.py 一个实现，CLI 和 UI 共用
  发版             make gate：确定性 100% + judge 阈值；写 report + history
  自测             fixture trace 当规格；judge 的解析/夹取/降级有单测

Organization
  waku/ops/ 可整目录删除 —— 这条约束保证了 ops 永远不会污染 agent
```

---

### 附：本文引用的主要文件（GitHub）

- [docs/evals.md](https://github.com/ShenSeanChen/waku-agent/blob/main/docs/evals.md)
- [waku/ops/tracing.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/tracing.py)
- [waku/ops/observability.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/observability.py)
- [waku/ops/turn_evals.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/turn_evals.py)
- [waku/ops/release_gate.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/release_gate.py)
- [waku/ops/receipt.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/receipt.py)
- [waku/ops/pricing.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/ops/pricing.py)
- [waku/loop/agent.py](https://github.com/ShenSeanChen/waku-agent/blob/main/waku/loop/agent.py)
- [evals/deterministic/test_tool_trigger.py](https://github.com/ShenSeanChen/waku-agent/blob/main/evals/deterministic/test_tool_trigger.py)
- [scripts/shootout.py](https://github.com/ShenSeanChen/waku-agent/blob/main/scripts/shootout.py)
