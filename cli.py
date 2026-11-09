"""CLI 入口：python cli.py "你的编程任务" --workdir ./sandbox_example

LLM 由 agent.py 内部从环境变量构造（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID）。
环境文件查找顺序见 llm_env.py：`MINICODE_ENV_FILE` → 本目录 `.env` → 当前工作目录 `.env`。
"""

from __future__ import annotations

import argparse
from agent import CodingAgent


def main() -> None:
    ap = argparse.ArgumentParser(description="CLI 编程 Agent（LangGraph 编排 · 类 Claude Code 迷你版）")
    ap.add_argument("task", help="用自然语言描述的编程任务")
    ap.add_argument(
        "--workdir",
        default="./sandbox_example",
        help="沙箱根目录（默认 ./sandbox_example，工具只能在此目录内操作）",
    )
    ap.add_argument("--max-iter", type=int, default=20, help="最大工具调用轮数（默认 20）")
    ap.add_argument("--max-obs", type=int, default=2000, help="单次 observation 截断长度（默认 2000）")
    ap.add_argument("--plan-mode", action="store_true", help="启用 Plan 模式（先规划再执行）")
    ap.add_argument("--adaptive", action="store_true", help="自适应规划：仅复杂任务启用 Plan（规则式判定）")
    ap.add_argument("--compress", action="store_true", help="启用分层上下文压缩（默认关，只对长轨迹任务可能有收益）")
    ap.add_argument("--stall", action="store_true", help="启用停滞检测（反复探索无产出时警告/终止）")
    ap.add_argument("--skills", action="store_true", help="启用 Skill 分层路由（检索仓库外的知识）")
    ap.add_argument("--quiet", action="store_true", help="关闭过程打印")
    args = ap.parse_args()

    agent = CodingAgent(
        workdir=args.workdir,
        max_iter=args.max_iter,
        max_obs=args.max_obs,
        verbose=not args.quiet,
        plan_mode=args.plan_mode,
        adaptive_plan=args.adaptive,
        compress=args.compress,
        stall=args.stall,
        skills=args.skills,
    )
    result = agent.run(args.task)
    if not args.quiet:
        print("\n" + "=" * 60 + "\n📋 最终总结:\n")
    print(result)

    # Phase 0：运行指标（eval 基线用，命令行也方便看）
    s = agent.stats
    if not args.quiet:
        print(
            f"\n📊 运行指标: LLM调用={s.llm_calls} 提示token={s.prompt_tokens} "
            f"生成token={s.completion_tokens} 工具步数={s.tool_steps} "
            f"耗时={s.total_seconds:.1f}s 状态={s.status}"
        )
        if args.stall:
            print(
                f"🛑 停滞检测: 警告={'是' if s.stall_warned else '否'} "
                f"终止={'是' if s.stall_stopped else '否'} 最长空转={s.stall_idle_max}轮"
            )


if __name__ == "__main__":
    main()
