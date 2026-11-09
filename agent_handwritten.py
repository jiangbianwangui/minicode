"""ReAct 主循环：把 LLM 的「思考→工具调用→观察」串成闭环。

与 SQLPilot 的 SQL 执行闭环是同一个思想，只是执行器从数据库换成了
「文件系统 + shell」。这里坚持自实现，便于面试时讲清每一层。
"""

from __future__ import annotations

import json
from pathlib import Path

from prompts import SYSTEM_PROMPT
from tools import TOOL_SCHEMAS, call


class CodingAgent:
    def __init__(
        self,
        llm,
        workdir: str | Path,
        max_iter: int = 20,
        max_obs: int = 2000,
        verbose: bool = True,
    ) -> None:
        self.llm = llm
        self.workdir = Path(workdir).resolve()
        self.max_iter = max_iter
        self.max_obs = max_obs
        self.verbose = verbose
        self.workdir.mkdir(parents=True, exist_ok=True)

    # -- 内部：把一次模型返回的 tool_calls 重组成 OpenAI 要求的 assistant 消息 --
    @staticmethod
    def _assistant_message(content: str, tool_calls: list[dict]) -> dict:
        msg = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
        return msg

    def run(self, task: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        if self.verbose:
            print(f"\n🎯 任务: {task}\n{'='*60}")

        for step in range(1, self.max_iter + 1):
            content, tool_calls = self.llm.chat(messages, tools=TOOL_SCHEMAS)

            # 模型不再调用工具 → 视为给出最终答案
            if not tool_calls:
                if self.verbose:
                    print(f"\n[第 {step} 步] 模型给出最终回答（无工具调用）")
                return content.strip() or "（模型未返回有效总结）"

            messages.append(self._assistant_message(content, tool_calls))

            if self.verbose:
                names = ", ".join(tc["name"] for tc in tool_calls)
                print(f"\n[第 {step} 步] 调用工具: {names}")

            # 依次执行每个工具，把 observation 作为 tool 消息回填
            for tc in tool_calls:
                obs = call(tc["name"], tc["arguments"], self.workdir, max_obs=self.max_obs)
                if self.verbose:
                    preview = obs if len(obs) <= 600 else obs[:600] + " …(已截断)"
                    print(f"   └─ {tc['name']} → {preview}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": obs,
                    }
                )

        return "⚠️ 达到最大步数仍未结束（max_iter=%d）。" % self.max_iter
