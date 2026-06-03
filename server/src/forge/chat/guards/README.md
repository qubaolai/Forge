# `agent_platform.chat.guards` — ReAct 循环的软引导兜底

四道独立防线, 在 LLM 失控 / 资源吃紧 / 卡死时**软引导**模型自己收尾, **用户全程无感知**.

## 为什么不是硬限制

之前 `max_steps=5` 是硬上限, 一旦达到就抛 `AgentMaxStepsError`. 用户感知 = "失败了". 但任务可能真的需要更多步.

Claude Code / Cursor 等成熟 agent 的做法: **退出靠 LLM 自己判断 (它输出文本不调工具 → 自然退出)**. 死循环极少发生; 即便发生, 也是通过给 LLM 注入引导让它自己收尾, 而不是直接抛错.

把 `max_steps` 从 **5 → 50**, 配合本目录的 4 个 LoopGuard 做软兜底.

## LoopGuard 通用契约

```python
class LoopGuard(ABC):
    async def before_step(self, state: LoopState) -> Guidance | None: ...
```

`Guidance`:
```python
@dataclass(frozen=True)
class Guidance:
    content: str                        # 注入到 messages 的 system 文本
    severity: Literal["hint", "warning", "force_stop"]
```

`LoopState` 已包含 guards 可能用到的全部字段:

| 字段 | 来源 | 用途 |
|---|---|---|
| `step_index / max_steps` | Runner | StepSafetyNet |
| `last_tool_name / last_tool_args_hash` | Runner 从上一步 ReActAgent 抽取 | StuckDetector |
| `accumulated_tokens` | ReActAgent 透传 `total_usage["total_tokens"]` | TokenBudgetGuard |
| `elapsed_seconds` | Runner 从 turn 起始计时 | WallClockGuard |

严重程度对终态的影响:

| severity | 行为 |
|---|---|
| `hint` | 仅注入 system 文本, LLM 自主决定 |
| `warning` | 同 hint, 但文本更强硬 ("⚠️ 请立刻收尾") |
| `force_stop` | 注入文本 **+** 下一步 `tool_choice="none"`, LLM 必须输出文本不能调工具 |

多 guard 同时返回: 文本全部累积; 任一是 force_stop → 整步 force_text_only=True.

## 四个内置 guards

### 1. StepSafetyNet — 步数兜底

```
剩余步数 (max - step_index)        →   行为
>3                                 →   不引导
3, 2                               →   hint "你还剩 N 步预算"
1                                  →   force_stop "这是最后一步, 必须输出最终答复"
0 (理论不会)                        →   force_stop 兜底
```

这是替代旧 `max_steps=5` 硬限制的核心. 即便真到 50 步还没结束, 也是 task_partial 而不是 error, 用户能继续.

### 2. StuckDetector — 死循环检测

**关键设计点 (避免误伤)**:
- 检测键 = `(tool_name, hash(canonical_json(args)))`. **不同 args 视为不同调用**.
- 只看 "连续 N 次完全相同"; 中间任何不同调用都重置计数.

```
连续 N 次相同 (name + args 都同)   →   行为
<3                                 →   不引导
3                                  →   warning "你已连续 3 次以相同参数调用 X"
5                                  →   force_stop "明显陷入循环, 立刻基于已有信息收尾"
```

误伤场景对照:

| 场景 | 结果 |
|---|---|
| `read_file("a.py")` → `read_file("b.py")` → `read_file("c.py")` | ✅ 不触发 (args 不同) |
| `read_file("a.py")` → `edit_file(...)` → `read_file("a.py")` | ✅ 不触发 (中间有 edit 打断) |
| `read_file("a.py")` × 3 连续 | ⚠️ warning |
| `read_file("a.py")` × 5 连续 | 🛑 force_stop |

### 3. TokenBudgetGuard — 成本上限

```
累计 total_tokens / budget         →   行为
< 70%                              →   不引导
≥ 70%                              →   hint
≥ 90%                              →   warning
≥ 95%                              →   force_stop
```

默认 budget = 100,000 tokens. 给一个普通 turn 10-15 步的余量, 同时防止极端循环消耗.

### 4. WallClockGuard — 延迟上限

```
elapsed_seconds                    →   行为
< 120s                             →   不引导
≥ 120s                             →   hint
≥ 180s                             →   warning
≥ 240s                             →   force_stop
```

注意配合 HTTP 客户端 / nginx 反代的 timeout: force_stop 触发后 LLM 还要输出最后文本, 总耗时可能 ~250-260s, 反代 timeout 应大于 270s.

## 状态隔离: 每 turn 新实例

```python
def _default_guard_factories() -> list[GuardFactory]:
    return [
        lambda max_steps: StepSafetyNet(),
        lambda max_steps: StuckDetector(),    # 有 deque 状态
        lambda max_steps: TokenBudgetGuard(),
        lambda max_steps: WallClockGuard(),
    ]
```

ReActRunner.run() 每次执行都用 factories 新建一组 guards. **跨 turn 不共享状态**: 上一轮的 StuckDetector deque 不会影响下一轮.

## 跟 ReActAgent 的桥接

ReActRunner 把多 guard 聚合成 ReActAgent 期望的 `before_step` 回调:

```python
# chat/runner.py
def _make_before_step_bridge(self, guards, run_started_at):
    async def bridge(step_ctx: StepContext) -> StepDecision:
        state = LoopState(
            step_index=step_ctx.step_index,
            max_steps=step_ctx.max_steps,
            last_tool_name=...,         # 从 step_ctx.last_step_tool_calls 抽
            last_tool_args_hash=...,    # 同上
            accumulated_tokens=step_ctx.accumulated_usage.get("total_tokens", 0),
            elapsed_seconds=time.monotonic() - run_started_at,
        )
        inject = []
        force_stop = False
        for g in guards:
            try:
                guidance = await g.before_step(state)
            except Exception:
                logger.exception(...)   # 一个 guard 崩了, 其他照常
                continue
            if guidance is None:
                continue
            inject.append(guidance.content)
            if guidance.severity == "force_stop":
                force_stop = True
        return StepDecision(
            inject_system_messages=inject,
            force_text_only=force_stop,
        )
    return bridge
```

**ReActAgent 完全不知道 LoopGuard / chat 层存在**.

## 异常隔离

任一 guard 在 `before_step` 抛异常:
- ReActRunner.bridge 内 try/except + `logger.exception` 记录
- 该 guard 本步跳过, 其他 guards 照常
- 不影响 ReActAgent 流程

测试覆盖: `tests/unit/chat/test_runner_guards_bridge.py:test_crash_isolated`.

## 扩展点

### 新增一个 Guard

1. 在 `chat/guards/` 加 `your_guard.py`, 继承 `LoopGuard` ABC
2. 在 `chat/guards/__init__.py` re-export
3. 在 `chat/runner.py:_default_guard_factories` 加一行

例子: 假设要加一个 "禁止某些 tool 在前 3 步被调" 的 PolicyGuard:

```python
# chat/guards/early_tool_block.py
class EarlyToolBlockGuard:
    def __init__(self, banned: set[str], until_step: int = 3):
        self._banned = banned
        self._until = until_step

    async def before_step(self, state: LoopState) -> Guidance | None:
        if state.step_index >= self._until:
            return None
        if state.last_tool_name in self._banned:
            return Guidance(
                content=f"⚠️ 前 {self._until} 步不应调用 {state.last_tool_name}; 改用其他工具",
                severity="warning",
            )
        return None
```

注册到默认 factories 或者只给特定 Runner 用 (构造 ReActRunner 时传 `guard_factories=[...]`).

### 配置化 Guards (未来)

目前 guard 阈值是构造参数. 后续可:
- 让 配置文件 加 `guard_config` 字段
- ReActRunner 从 chat profile 加载，按 profile 使用不同阈值
- "客服 Agent" 用更严格的限制, "代码助手 Agent" 用更宽松的

## 相关文档

- chat 编排总览: [../README.md](../README.md)
- ReActAgent before_step 钩子: [agents/react/agent.py](../../agents/react/agent.py) (搜 `StepContext` / `StepDecision`)
