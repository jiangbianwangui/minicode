"""ReAct 主循环（LangGraph 版）：用 langgraph.graph.StateGraph 编排。

和 agent_handwritten.py 是**同一个 Agent**，区别只在于编排层：
- 原来手写的 while 循环 + messages 维护，现在交给 LangGraph 的 StateGraph；
- 工具层（tools.py 的参数校验 / 沙箱 / 黑名单）完全复用，没有重写。

这正是「先自研搞懂原理，再上框架提效」的实例：框架只替换了编排层，
工具执行、参数校验、安全边界这些「工程化关键点」仍然是你自己写的。
LLM 层用 langchain-openai 的 ChatOpenAI（DeepSeek / 硅基流动等 OpenAI 兼容端点直连）。

Phase 0：RunStats 可观测性 —— 每次运行累计 LLM 调用数、token、工具步数、耗时。
Phase 1：
- Plan 模式（--plan-mode / --adaptive）：新增 plan 工具，Agent 先列步骤、逐步标记完成。
  系统提示**只增量注入当前步骤 + 进度**（完整计划在历史 plan 调用中已可见），避免每轮把整张
  计划重发进上下文——这正是 Phase 1 初版 plan 模式 token 膨胀的根因。纯 ReAct 之外的
  「Plan-Execute」能力，减少盲目重试。
- 分层上下文压缩（--compress）：决策前把陈旧工具轮次摘要化，模型只看 摘要+最近N轮，
  直接砍掉长任务里反复喂回的大段 observation（layer1=单次截断，layer2=轮次摘要）。
两者都**可插拔、可消融**（关掉即回到 v0 行为），方便 eval 量化增量。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

from stall import STALL_HINT, StallDetector
from langgraph.errors import GraphRecursionError
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI

from llm_env import load_env
from prompts import SYSTEM_PROMPT
from tools import TOOL_SCHEMAS, call
from context import Compressor, count_tokens, estimate_tokens
from skill_router import default_router, summarize_hits


def _trace_line(name: str, args: dict, obs: str, ok: bool, step: int) -> str:
    """把一次工具调用压成一行 jsonl：名字 / 参数 / 结果摘要 / 成败。

    只截断正文、不省略结构——排查时要看的是「它到底敲了什么命令」，
    而不是模型说了什么。
    """
    return json.dumps(
        {
            "step": step,
            "tool": name,
            "args": {k: str(v)[:300] for k, v in (args or {}).items()},
            "ok": bool(ok),
            "obs": str(obs)[:300],
        },
        ensure_ascii=False,
    ) + "\n"


PLAN_INSTRUCTION = (
    "\n\n[Plan 模式已启用] 对复杂任务，第一步调用 plan 列出全部步骤（只需一次）；"
    "之后直接按步骤执行，无需每步重复调用 plan，仅在需要修改计划时再调用。"
    "每完成一步，可再次调用 plan 把该步索引进 done 以同步进度。plan 工具不修改任何文件。"
)

# ---- Skill 路由（Phase 2） ----

LOAD_SKILL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "LoadSkill",
        "description": (
            "读取一篇技能文档的全文。技能文档记录的是**仓库之外的知识**"
            "（外部 API 约定、团队规范、历史事故禁忌），Grep/ReadFile 都搜不到。"
            "系统提示里给出的是摘要，需要细节时用本工具拉全文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "技能名（见系统提示里的摘要列表）",
                },
            },
            "required": ["name"],
        },
    },
}

PLAN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": (
            "规划 / 更新任务步骤。复杂任务请先调用本工具列出全部步骤；执行中逐步把已完成步骤的"
            "索引加入 done。修改计划时请传入完整 steps（含未完成的）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "完整的步骤列表（更新时请包含全部步骤）",
                },
                "done": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "已完成步骤的索引（从 0 开始）",
                },
            },
            "required": ["steps"],
        },
    },
}


@dataclass
class RunStats:
    """一次 run() 的可观测指标，供 eval 基线量化。"""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_steps: int = 0
    total_seconds: float = 0.0
    status: str = ""        # "done" | "max_iter" | "error" | "stalled"
    error: str = ""
    # 每个工具调用明细（name, ok），便于后续分析
    tool_calls: list[tuple[str, bool]] = field(default_factory=list)
    # 每次 LLM 请求的往返秒数（含网络）。用来区分「任务难」和「基础设施慢」：
    # 同样是 20 分钟跑完，p95 延迟 2s 是任务重，p95 延迟 100s 是 API 在限流。
    llm_latencies: list[float] = field(default_factory=list)
    # 停滞检测（stall detector）：连续多少轮「既无新信息也无文件写入」。
    # 空转是 token 双峰（66k vs 410k）的来源，必须先量化才能治理。
    stall_rounds: int = 0
    stall_warned: bool = False
    stall_stopped: bool = False
    stall_warn_at: int | None = None
    stall_idle_max: int = 0
    # 每次成功的读操作读了哪个文件（按顺序）。用于量化「重读成本」——
    # 压缩会丢细节，agent 就得重读；这一项是压缩代价的直接度量。
    read_paths: list[str] = field(default_factory=list)
    # 「有没有交付」——成功的 WriteFile + EditFile 次数。
    #
    # 为什么单独记：失败有两种，在通过率上长得一模一样，但归因完全相反——
    #   ① 零交付（writes_ok == 0）：agent 从没进入"改代码"这一步，
    #      全程在探索/找文件/试工具。**这是流程崩溃，通常是我的任务或 prompt 有缺陷**，
    #      不是它能力不够。（t27 第一次跑就是这样：8 轮全在 `find /`，一个字没改）
    #   ② 有交付但错（writes_ok > 0）：改了，但改得不对。
    #      **这才是"知识/判断不够"，才是优化该瞄准的地方。**
    # 分不清这两者，就会把"我的 bug"算成"它不行"，A/B 结论直接反。
    writes_ok: int = 0
    # Skill 路由：命中哪些（检索阶段）、真读了哪些全文（LoadSkill 阶段）。
    # 分开记很关键——「召回了」不等于「用了」，和 tool_steps 与 tool_calls 的区别同理。
    skill_hits: list[str] = field(default_factory=list)
    skill_loaded: list[str] = field(default_factory=list)


def _add_usage(stats: RunStats, msg: AIMessage, latency: float = 0.0) -> None:
    """从 AIMessage 抽取 token 用量（langchain 不同版本字段名不同，做兜底）。"""
    stats.llm_calls += 1
    if latency:
        stats.llm_latencies.append(round(latency, 2))
    um = getattr(msg, "usage_metadata", None)
    if um:
        stats.prompt_tokens += int(um.get("input_tokens", 0) or 0)
        stats.completion_tokens += int(um.get("output_tokens", 0) or 0)
        return
    tu = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
    if tu:
        stats.prompt_tokens += int(tu.get("prompt_tokens", 0) or 0)
        stats.completion_tokens += int(tu.get("completion_tokens", 0) or 0)


_COMPLEX_KEYWORDS = [
    "多文件", "跨文件", "重命名", "重构", "聚合", "统计", "大文件", "包", "模块",
    "分步", "多个文件", "数据库", "接口", "refactor", "multiple files", "package",
    "module", "rename", "extract", "aggregate", "pytest",
]


def is_complex_task(task: str) -> bool:
    """轻量复杂度门控（规则式，零额外 LLM 调用）。

    仅用结构性关键词判定是否需要 Plan：多文件 / 跨文件 / 重构 / 大文件 / 包 /
    聚合 等。简单单文件任务（hello / add / fizzbuzz / 单 bug 修复）不开 plan，
    避免 plan 模式在简单任务上的 token 惩罚（见 eval/ABLATION.md）。
    未来可升级为一次廉价 LLM router 调用，但规则式对消融更干净、可复现。
    """
    t = task.lower()
    return any(k.lower() in t for k in _COMPLEX_KEYWORDS)


class _State(TypedDict):
    """图的共享状态：消息列表（add_messages 负责追加合并）。"""

    messages: Annotated[list[BaseMessage], add_messages]


def _build_llm() -> ChatOpenAI:
    """从环境变量构造 OpenAI 兼容的 ChatOpenAI（DeepSeek / 硅基流动等都行）。"""
    load_env()
    import os

    return ChatOpenAI(
        model=os.environ.get("LLM_MODEL_ID", "deepseek-chat"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        temperature=0,
    )


class CodingAgent:
    def __init__(
        self,
        workdir: str | Path,
        llm: "ChatOpenAI | None" = None,
        max_iter: int = 20,
        max_obs: int = 2000,
        verbose: bool = True,
        plan_mode: bool = False,
        adaptive_plan: bool = False,
        compress: bool = False,
        stall: bool = False,
        stall_warn_after: int = 6,
        stall_stop_after: int = 10,
        skills: bool = False,
        skill_k: int = 3,
        trace_path: "str | Path | None" = None,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.max_iter = max_iter
        self.max_obs = max_obs
        self.verbose = verbose
        # 轨迹落盘：每次工具调用写一行 jsonl（名字 / 参数 / 结果前 300 字 / 成败）。
        # 只存工具名不够——t34 那次「19 次连续 Bash」只能看出它在打转，
        # 看不出它在 `ls ..`，根因是「拒绝文案在邀请重试」这件事只能从命令文本读到。
        self.trace_path = Path(trace_path) if trace_path else None
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            self.trace_path.write_text("", encoding="utf-8")
        # plan_mode: 硬编码始终开 plan；adaptive_plan: 仅对「复杂任务」开 plan（运行时判定）
        self.adaptive_plan = adaptive_plan
        self.plan_mode = plan_mode or adaptive_plan
        self.llm = llm or _build_llm()
        self.stats = RunStats()

        # Plan 工具并入工具集（两种 plan 模式都加；具体是否注入提示由 _use_plan 决定）
        self._tool_schemas = list(TOOL_SCHEMAS)
        if self.plan_mode:
            self._tool_schemas.append(PLAN_SCHEMA)

        # Skill 分层路由（Phase 2）：把「仓库外的知识」按需送进上下文。
        # 默认关（skills=False），保证历史实验结论可复现。
        #
        # 与 Plan / 压缩的区别：那两个是「让它更会想」，这个是「让它知道得更多」。
        # 前者在 26 个任务上已被证明净亏或边界内无收益，因为**这些任务的答案都在仓库里**，
        # 再会想也变不出新信息。Skill 补的是仓库里没有的信息（外部 API 契约、
        # 团队规范），所以它的价值不该用"通过率有没有涨"衡量，而该看
        # 「本来答不对的题，现在能答对了」——t27 就是这样的靶子。
        self.skill_k = skill_k
        self.router = default_router() if skills else None
        self._skill_hits: list = []
        if self.router is not None:
            self._tool_schemas.append(LOAD_SKILL_SCHEMA)
        self._plan: dict = {"steps": [], "done": []}
        # 是否真正启用 plan 注入/路由；adaptive 模式下在 run() 按任务复杂度决定
        self._use_plan: bool = plan_mode  # 硬编码 plan_mode 默认开；adaptive 在 run 重算

        # 分层上下文压缩器（可插拔）
        # 工具 schema 是每次请求的固定开销（6 个工具的 JSON），必须计入压缩预算，
        # 否则估算永远偏低、阈值永不触发（见 _node_agent 的说明）。
        self._tool_tokens = count_tokens(json.dumps(self._tool_schemas, ensure_ascii=False))
        # 触发水位可通过环境变量扫（COMPRESS_THRESHOLD）。
        #
        # ⚠️ 默认关闭（compress=False）是**有意的决策**，不是"还没做完"：
        #
        #   压缩净收益 = 每轮省下的重发成本 × 剩余轮数  −  遗忘导致的重读/返工成本
        #                └──── 线性累积 ────┘              └──── 一次就阶跃 ────┘
        #
        #   本项目的任务集中在 8–25 次调用，处于"攒不够就撞上阶跃"的区间；
        #   而"会不会重读"取决于 agent 后面怎么走，**事前不可知**，
        #   所以无悔闸门只能算清摘要成本、算不清遗忘风险。
        #   结论：只对「一次性勘察型」（读完即动手、不回看）的任务有正收益，
        #   适用形态写进 README「机制结论」一节，这里保守默认关。
        #
        # 另一个已修的真 bug：估算水位长期漏算 system + 工具 schema（698 token/次），
        # 导致阈值从未被跨过——两周内压缩一次都没真正执行过。见 tests/test_compress_budget.py。
        _th = int(os.getenv("COMPRESS_THRESHOLD", "4000"))
        self.compressor = Compressor(self.llm, threshold=_th) if compress else None

        # 停滞检测（可插拔）：识别「反复探索但无产出」的空转。
        # 只做软干预（system prompt 注入一次警告）+ 兜底终止，不改变正常路径。
        self.stall: StallDetector | None = (
            StallDetector(warn_after=stall_warn_after, stop_after=stall_stop_after)
            if stall else None
        )
        self._stall_hint: str = ""

        # 编排层：agent(决策) <-> tools(执行) 的状态机
        g = StateGraph(_State)
        g.add_node("agent", self._node_agent)
        g.add_node("tools", self._node_tools)
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", self._should_continue, {"tools": "tools", END: END})
        g.add_edge("tools", "agent")
        self.graph = g.compile()

    def _trace(self, name: str, args: dict, obs: str, ok: bool) -> None:
        """轨迹落盘（未开启则零开销）。写失败绝不能影响主流程。"""
        if self.trace_path is None:
            return
        try:
            with self.trace_path.open("a", encoding="utf-8") as f:
                f.write(_trace_line(name, args, obs, ok, len(self.stats.tool_calls)))
        except OSError:
            self.trace_path = None  # 落盘失败就关掉，别让诊断功能拖垮评测

    # ---- 节点 ----
    def _node_agent(self, state: _State) -> dict:
        """决策节点：把（压缩后的）系统提示 + 历史消息交给「绑定了工具的 LLM」。"""
        self._iter += 1
        msgs = state["messages"]
        sys_msg = self._build_system()
        if self.compressor is not None:
            # 🔴 关键修复（2026-10-23）：固定开销必须计入压缩预算。
            # 原实现只在 plan 模式下计入 system prompt，且从不计入工具 schema，
            # 结果估算值系统性偏低约一半 → 阈值（4000）永远差一点点、永不触发。
            # 实测 t26：messages 估算爬到 3921 就到顶，而真实每次请求 7,478 token，
            # 12 次 compress() 调用 **0 次真正压缩**。
            extra = estimate_tokens([sys_msg]) + self._tool_tokens
            # 把「剩余可摊销轮数」告诉压缩器，做 no-regret 闸门（临尾不亏本压缩）。
            # 注意：不能用 max_iter(20) 估算——真实任务只走 5~6 步，用它会把 remaining
            # 虚估成 17，导致闸门误判「还能摊很多轮」而放行亏本压缩。
            # 用「已走轮数对称估算剩余」并封顶 6，更符合本任务集的实际轨迹长度。
            remaining = max(1, min(self._iter, 6))
            msgs = self.compressor.compress(
                msgs, self.stats, extra_tokens=extra, remaining_turns=remaining
            )
        _t0 = time.perf_counter()
        resp: AIMessage = self.llm.bind_tools(self._tool_schemas).invoke([sys_msg] + msgs)
        _add_usage(self.stats, resp, latency=time.perf_counter() - _t0)
        return {"messages": [resp]}

    def _node_tools(self, state: _State) -> dict:
        """执行节点：把上一轮 AIMessage 里的 tool_calls 逐个跑掉，回写 ToolMessage。"""
        last: AIMessage = state["messages"][-1]
        outputs: list[ToolMessage] = []
        calls: list[tuple[str, bool, dict]] = []   # 供停滞检测判断「这轮有没有产出」
        for tc in (last.tool_calls or []):
            name = tc["name"]
            args = tc.get("args", {}) or {}

            # Plan 工具：只更新内部计划，不碰文件系统（仅当真正启用 plan 时路由）
            if name == "plan":
                if not self._use_plan:
                    outputs.append(ToolMessage(
                        content="⚠️ 当前为纯 ReAct 模式（自适应判定任务较简单，未启用 Plan），请直接执行。",
                        tool_call_id=tc["id"],
                    ))
                    self._trace(name, args, "plan 未启用", False)
                    self.stats.tool_calls.append((name, False))
                    calls.append((name, False, args))
                    continue
                steps = args.get("steps", []) or []
                done = [int(x) for x in (args.get("done", []) or [])]
                self._plan = {"steps": list(steps), "done": done}
                ok = len([d for d in done if 0 <= d < len(steps)])
                obs = f"✅ 计划已记录：共 {len(steps)} 步，已完成 {ok} 步。"
                outputs.append(ToolMessage(content=obs, tool_call_id=tc["id"]))
                self.stats.tool_calls.append((name, True))
                calls.append((name, True, args))
                continue

            # Skill 全文加载：内容不进 messages 的 system，而作为 ToolMessage 回到对话里。
            # 这样「读了哪篇」在轨迹中可见，也不会每轮重发（省 token）。
            if name == "LoadSkill":
                want = str(args.get("name", "") or "")
                skill = self.router.get(want) if self.router is not None else None
                if self.router is None:
                    obs = "❌ 当前未启用技能库（skills 关闭），请依靠现有代码与通用知识判断。"
                elif skill is None:
                    avail = ", ".join(s.name for s in self.router.skills) or "（空）"
                    obs = f"❌ 没有名为 {want!r} 的技能。可用技能：{avail}"
                else:
                    obs = f"# 技能：{skill.name}\n\n{skill.body}"
                    self.stats.skill_loaded.append(skill.name)
                ok = not obs.startswith("❌")
                self._trace(name, args, obs, ok)
                self.stats.tool_calls.append((name, ok))
                calls.append((name, ok, args))
                outputs.append(ToolMessage(content=obs, tool_call_id=tc["id"]))
                continue

            try:
                obs = call(name, args, self.workdir, max_obs=self.max_obs)
            except Exception as e:  # 任何意外都转成 observation，让 Agent 自行反应
                obs = f"❌ 工具执行异常: {type(e).__name__}: {e}"
            ok = not obs.startswith("❌") and not obs.startswith("⛔")
            self._trace(name, args, obs, ok)
            self.stats.tool_calls.append((name, ok))
            calls.append((name, ok, args))
            # 重读统计：只记成功的读操作，记路径即可（不记内容，避免撑大 stats）
            if ok and name in ("ReadFile", "ReadLines", "ViewFile"):
                self.stats.read_paths.append(str(args.get("path", "")))
            # 交付统计：成功的写操作。writes_ok == 0 表示"零交付"（见 RunStats 注释）
            if ok and name in ("WriteFile", "EditFile"):
                self.stats.writes_ok += 1
            outputs.append(ToolMessage(content=obs, tool_call_id=tc["id"]))
        self.stats.tool_steps += len(last.tool_calls or [])

        # 停滞检测：本轮既没拿到新信息、也没成功写文件 → 空转计数 +1
        if self.stall is not None:
            event = self.stall.observe(calls)
            if event == "warn":
                self._stall_hint = STALL_HINT.format(idle=self.stall.idle)
            elif event == "stop":
                self.stats.status = "stalled"
            for k, v in self.stall.summary().items():
                setattr(self.stats, k, v)

        return {"messages": outputs}

    def _should_continue(self, state: _State) -> str:
        """条件边：模型还想调用工具就进 tools，否则结束。"""
        if self.stats.status == "stalled":
            return END          # 兜底终止：警告后仍无进展，不再让它烧预算
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return END

    # ---- 系统提示构建 ----
    def _build_system(self) -> SystemMessage:
        prompt = SYSTEM_PROMPT
        if self._use_plan:
            prompt += PLAN_INSTRUCTION
        # 增量计划注入：完整计划已在历史 plan 工具调用中可见，这里只注入
        # 「当前步骤 + 进度 + 接下来 1-2 步预览」，避免每轮把整张计划重发进 system prompt
        # （这正是之前 plan 模式 token 膨胀的根因）。系统提示只承载「活在当下」的一步。
        if self._use_plan and self._plan.get("steps"):
            steps = self._plan["steps"]
            done = set(int(x) for x in self._plan.get("done", []))
            total = len(steps)
            ndone = len(done & set(range(total)))
            remaining_idx = [i for i in range(total) if i not in done]
            lines = [f"\n\n## 执行计划（{ndone}/{total} 步已完成）"]
            if remaining_idx:
                cur = remaining_idx[0]
                lines.append(f"▶ 当前步骤（第 {cur + 1} 步）：{steps[cur]}")
                nxt = remaining_idx[1:3]
                if nxt:
                    preview = "；".join(f"第 {i + 1} 步：{steps[i]}" for i in nxt)
                    lines.append(f"后续预览：{preview}")
            else:
                lines.append("所有步骤已完成，请直接给出总结。")
            prompt += "\n".join(lines)
        # 停滞警告：注入 system 而不是追加进 messages，避免污染对话历史
        # （也不需要伪造一条「用户消息」，那会干扰 ReAct 的 tool_call 配对语义）。
        if self._stall_hint:
            prompt += self._stall_hint
        # Skill 摘要：只注入命中的几条 description（约 40 字/条），正文要 agent 自己 LoadSkill 拉。
        # 未命中时**什么都不加** —— 不列全量目录，避免诱导 agent 去读用不上的技能。
        if self._skill_hits:
            prompt += summarize_hits(self._skill_hits)
        return SystemMessage(content=prompt)

    # ---- 运行入口 ----
    def run(self, task: str) -> str:
        self.stats = RunStats()
        self._iter = 0
        self._stall_hint = ""
        if self.stall is not None:
            self.stall.reset()
        if self.plan_mode:
            self._plan = {"steps": [], "done": []}
        # 自适应规划：仅当任务被判为「复杂」才真正注入 plan（否则退回纯 ReAct）
        if self.adaptive_plan:
            self._use_plan = is_complex_task(task)
            if self.verbose:
                print(f"🧭 自适应规划判定: 任务{'复杂 → 启用 Plan' if self._use_plan else '简单 → 纯 ReAct'}")
        # Skill 检索：每次 run 重算（和 plan/压缩不同，它依赖任务文本本身）
        self._skill_hits = (
            self.router.retrieve(task, top_k=self.skill_k) if self.router is not None else []
        )
        self.stats.skill_hits = [h.skill.name for h in self._skill_hits]
        if self.verbose and self.router is not None:
            if self._skill_hits:
                hits = ", ".join(f"{h.skill.name}({h.score:.1f})" for h in self._skill_hits)
                print(f"📚 技能召回: {hits}")
            else:
                print("📚 技能召回: 无命中（按通用知识执行）")
        t0 = time.perf_counter()
        if self.verbose:
            print(f"\n🎯 任务: {task}\n{'='*60}")

        inp = {"messages": [HumanMessage(content=task)]}
        cfg = {"recursion_limit": 2 * self.max_iter + 2}

        try:
            if self.verbose:
                final_state = None
                for chunk in self.graph.stream(inp, cfg, stream_mode="values"):
                    final_state = chunk
                    last = chunk["messages"][-1]
                    if isinstance(last, AIMessage):
                        if last.tool_calls:
                            step = sum(1 for m in chunk["messages"] if isinstance(m, AIMessage))
                            names = ", ".join(t["name"] for t in last.tool_calls)
                            print(f"\n[第 {step} 步] 调用工具: {names}")
                        else:
                            print("\n[agent] 给出最终回答（无工具调用）")
                    elif isinstance(last, ToolMessage):
                        preview = last.content if len(last.content) <= 600 else last.content[:600] + " …(已截断)"
                        print(f"   └─ {last.name or 'tool'} → {preview}")
                if final_state is None:
                    final_state = self.graph.invoke(inp, cfg)
            else:
                final_state = self.graph.invoke(inp, cfg)
            self.stats.status = "stalled" if self.stats.stall_stopped else "done"
        except GraphRecursionError:
            self.stats.status = "max_iter"
            self.stats.total_seconds = time.perf_counter() - t0
            return "⚠️ 达到最大步数仍未结束（max_iter=%d）。" % self.max_iter
        except Exception as e:
            self.stats.status = "error"
            self.stats.error = f"{type(e).__name__}: {e}"
            self.stats.total_seconds = time.perf_counter() - t0
            return f"⚠️ 运行异常: {self.stats.error}"

        # 取最后一条「没有 tool_calls 的 AIMessage」作为总结
        for m in reversed(final_state["messages"]):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                self.stats.total_seconds = time.perf_counter() - t0
                result = m.content.strip() or "（模型未返回有效总结）"
                if self.verbose:
                    self._print_plan()
                return result
        self.stats.total_seconds = time.perf_counter() - t0
        # 停滞终止要先判：否则会被下面兜底的 "max_iter" 覆盖，
        # 让报告把「空转被拦下」误记成「步数不够」（归因就错了）。
        if self.stats.stall_stopped:
            if self.verbose:
                self._print_plan()
            return (f"⚠️ 停滞终止：连续 {self.stall.idle} 轮既无新信息也无文件写入，"
                    f"判定空转（stalled）。")
        self.stats.status = "max_iter"
        if self.verbose:
            self._print_plan()
        return "⚠️ 达到最大步数仍未结束（max_iter=%d）。" % self.max_iter

    def _print_plan(self) -> None:
        if self._use_plan and self._plan.get("steps"):
            print("\n📌 计划:")
            for i, step in enumerate(self._plan["steps"]):
                mark = "✅" if i in self._plan["done"] else "⬜"
                print(f"   {i + 1}. [{mark}] {step}")

    def run_with_stats(self, task: str) -> tuple[str, RunStats]:
        """同 run()，但返回 (最终文本, 本次运行指标)。"""
        text = self.run(task)
        return text, self.stats
