"""分层上下文压缩（Phase 1）。

设计要点：
- 这是一个「决策前压缩」：每次模型决策前，把历史消息里「陈旧的工具交互轮次」用 LLM
  摘要成几条紧凑消息，**模型只看到 摘要 + 最近 N 轮原文**，不再反复把几百行的工具输出
  重新喂回上下文。这是 layer2（layer1 是 tools.py 里单次 observation 的 max_obs 截断）。
- 为什么不改 state：图的 messages 用 add_messages（追加式）reducer，若在节点里整体替换会
  和追加语义冲突。改为「压缩喂给模型的输入」，state 仍按原样增长，但模型实际接收的 token
  显著下降——这正是 eval 里要量化的指标。
- 压缩单元 = 一个完整轮次 [AIMessage(tool_calls) + 它产生的 ToolMessage(s)]，折叠成一条
  无 tool_calls 的摘要 AIMessage，避免破坏 function-calling 的配对约束。

只在 token 预算超阈值且轮次足够多时才触发，短任务零开销。
"""

from __future__ import annotations

import re

from langchain_core.messages import (
    BaseMessage,
    AIMessage,
    HumanMessage,
    ToolMessage,
    SystemMessage,
)


def _add_usage(stats, msg: AIMessage) -> None:
    """从 AIMessage 抽取 token 用量（与 agent.py 同逻辑，避免循环依赖在此复制一份）。"""
    if stats is None:
        return
    stats.llm_calls += 1
    um = getattr(msg, "usage_metadata", None)
    if um:
        stats.prompt_tokens += int(um.get("input_tokens", 0) or 0)
        stats.completion_tokens += int(um.get("output_tokens", 0) or 0)
        return
    tu = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
    if tu:
        stats.prompt_tokens += int(tu.get("prompt_tokens", 0) or 0)
        stats.completion_tokens += int(tu.get("completion_tokens", 0) or 0)


# CJK 统一表意文字 + 常用中日韩标点/全角符号
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def count_tokens(text: str) -> int:
    """按字符构成估算 token 数：CJK 字符约 1 token/字，其余约 4 字符/token。

    为什么不能用统一的 `len // 4`（2026-10-26 修正）：
    本项目 prompt、注释、任务描述都以中文为主，而 `len // 4` 是按英文密度估的，
    对中文**系统性低估约 1.7 倍**。低估的后果不是"估算不准"这种小事，而是
    **压缩阈值永远达不到、功能静默不触发**（实测 t26：估算 3921 vs 实际 6780/次，
    12 次调用 0 次压缩）。这比压了没效果更难发现。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    return cjk + (len(text) - cjk) // 4


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """估算一批消息的 token 数（按 CJK 加权，仅用于触发阈值判断）。"""
    total = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            total += count_tokens(c)
        elif isinstance(c, list):  # multimodal content parts
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += count_tokens(part["text"])
    return total


def _content_text(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c)


def _group_rounds(messages: list[BaseMessage]) -> tuple[list[BaseMessage], list[list[BaseMessage]]]:
    """拆分消息为 (前置消息, 轮次列表)。

    前置消息 = 开头的 HumanMessage / 最终总结 AIMessage（无 tool_calls）等。
    轮次 = [AIMessage(tool_calls), *ToolMessage] 的连续段。
    """
    leading: list[BaseMessage] = []
    rounds: list[list[BaseMessage]] = []
    cur: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            if cur:
                rounds.append(cur)
            cur = [m]
        elif isinstance(m, ToolMessage):
            if cur:
                cur.append(m)
            else:
                leading.append(m)  # 孤立 ToolMessage（理论不出现）
        else:
            # 无 tool_calls 的 AIMessage（最终总结）/ HumanMessage：归入前置
            if cur:
                rounds.append(cur)
                cur = []
            leading.append(m)
    if cur:
        rounds.append(cur)
    return leading, rounds


_SUMMARY_PROMPT = SystemMessage(
    content=(
        "你是一个上下文压缩器。下面是一段 Agent 与工具交互的历史（含它调用的工具与工具返回）。"
        "请压缩成一段简洁的中文摘要。**必须逐字保留**：1) 执行过的精确命令与参数；2) 完整报错信息/"
        "堆栈（含错误类型与行号）；3) 文件路径、函数名、变量名、符号名；4) 后续行动依赖的关键数值结论"
        "（如行数、聚合结果）。只丢弃：重复的确认回执、与结论无关的冗长原文、纯装饰性输出。"
        "不要改写代码语义，不要臆造数据。保持紧凑，控制在 200 字以内。"
    )
)

# 这类工具返回的是「模型后续要靠精确字节去行动」的内容（文件内容 / 代码 / 数据），
# 一旦摘要就会丢失细节导致模型重读或试错——因此对 coding agent 而言它们「不可压缩」，
# 必须永远保留原文。压缩只针对 Bash/搜索/调试等「可丢」轮次。
PRESERVE_TOOLS = {
    "ReadFile", "ViewFile", "EditFile", "WriteFile", "ReadLines", "GrepFile",
}


def _round_tool_names(round_msgs: list[BaseMessage]) -> set[str]:
    names: set[str] = set()
    for m in round_msgs:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                names.add(tc.get("name", ""))
    return names


def _is_preserve_round(round_msgs: list[BaseMessage]) -> bool:
    """该轮是否包含「模型需按精确内容行动」的文件操作 → 不可压缩。"""
    return bool(_round_tool_names(round_msgs) & PRESERVE_TOOLS)


class Compressor:
    """决策前上下文压缩器。"""

    def __init__(
        self,
        llm,
        threshold: int = 4000,   # 估算 token 超此值才触发
        keep_recent: int = 2,    # 最近 N 轮保持原文
        batch: int = 4,          # 每多少轮合成一条摘要
        min_batch_chars: int = 1500,  # 单批可压缩文本低于此值则跳过（避免无效摘要调用）
        safety: float = 1.5,     # 无悔系数：收益须 > 成本 × safety 才压
    ) -> None:
        self.llm = llm
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.batch = batch
        self.min_batch_chars = min_batch_chars
        self.safety = safety

    def compress(self, messages: list[BaseMessage], stats=None, extra_tokens: int = 0,
                 remaining_turns: int = 4) -> list[BaseMessage]:
        # extra_tokens 用于把「系统提示（如 plan 注入）」也计入压缩预算，
        # 否则 plan 模式下系统提示被撑大却不被压缩器感知（这正是之前 plan+compress 反而更贵的根因）。
        if estimate_tokens(messages) + extra_tokens <= self.threshold or len(messages) < 8:
            return messages
        leading, rounds = _group_rounds(messages)
        if len(rounds) <= self.keep_recent + 1:
            return messages

        # 拆分：文件操作轮次（精确内容不可丢）永远保留；其余「可压缩」。
        round_preserve = [_is_preserve_round(r) for r in rounds]
        compressible_idx = [i for i, p in enumerate(round_preserve) if not p]
        if len(compressible_idx) <= self.keep_recent + 1:
            return messages  # 没有足够的「可丢」轮次可压，原样返回
        recent_compressible = set(compressible_idx[-self.keep_recent:])
        to_compress_idx = [i for i in compressible_idx if i not in recent_compressible]
        to_compress_rounds = [rounds[i] for i in to_compress_idx]

        # ---- 无悔闸门：只在「可摊销」时才压，否则零成本返回 ----
        old_tokens = sum(count_tokens(_content_text(m))
                         for r in to_compress_rounds for m in r)
        real_batches = 0
        for j in range(0, len(to_compress_rounds), self.batch):
            blob = self._render(to_compress_rounds[j : j + self.batch])
            if len(blob) >= self.min_batch_chars:
                real_batches += 1
        if real_batches == 0:
            return messages  # 全是小批，压了只增不减，停手
        n_batches = max(1, (len(to_compress_rounds) + self.batch - 1) // self.batch)
        cost_tokens = real_batches * (old_tokens // n_batches + 200)
        savings_per_turn = old_tokens - 50
        if savings_per_turn <= 0:
            return messages
        # 总收益须在本任务「剩余轮数」里摊回一次性摘要成本（压制临尾亏本压缩）
        if remaining_turns * savings_per_turn < cost_tokens * self.safety:
            return messages

        # ---- 真正压缩：按批摘要「可压缩且非最近」的轮次 ----
        repl_per_round: list[list[BaseMessage]] = []
        for j in range(0, len(to_compress_rounds), self.batch):
            chunk = to_compress_rounds[j : j + self.batch]
            blob = self._render(chunk)
            if len(blob) < self.min_batch_chars:
                for r in chunk:
                    repl_per_round.append(list(r))  # 小批保留原文
                continue
            try:
                resp = self.llm.invoke([_SUMMARY_PROMPT, HumanMessage(content=blob)])
                _add_usage(stats, resp)
                text = _content_text(resp)
                sum_msg = AIMessage(
                    content=f"[上下文压缩摘要 #{j // self.batch + 1}] {text}"
                )
                for _ in chunk:
                    repl_per_round.append([sum_msg])
            except Exception:
                for r in chunk:
                    repl_per_round.append(list(r))  # 压缩失败退回原文，绝不影响主流程

        repl_map = dict(zip(to_compress_idx, repl_per_round))
        out: list[BaseMessage] = list(leading)
        for i, r in enumerate(rounds):
            if round_preserve[i]:
                out.extend(r)          # 文件操作：精确内容永远保留
            elif i in repl_map:
                out.extend(repl_map[i])  # 被压缩轮次 → 摘要（或小批原文）
            else:
                out.extend(r)          # 最近的可压缩轮次：保留原文
        return out

    @staticmethod
    def _render(rounds: list[list[BaseMessage]]) -> str:
        parts: list[str] = []
        for r in rounds:
            for m in r:
                if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                    for tc in m.tool_calls:
                        args = tc.get("args", {})
                        parts.append(f"[Agent 调用] {tc['name']}({args})")
                elif isinstance(m, ToolMessage):
                    c = _content_text(m)
                    if len(c) > 1500:
                        c = c[:1500] + " …(截断)"
                    parts.append(f"[工具返回] {c}")
        return "\n".join(parts)
