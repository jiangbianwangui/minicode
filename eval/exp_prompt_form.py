"""prompt 形态对照实验脚手架（t35 专用，可复制改造给别的题）。

## 它解决什么

"某句话的写法会不会影响 agent 的行为"这类问题，只有**只改一句、其余逐字相同**的对照能回答。
手工改 `TASK["prompt"]` 再跑两次很容易引入别的变量（顺手改了约束、改了语气），
对照就废了。所以把"同一任务 × 两版 prompt × N 次"固定成脚本。

## 用法

    python eval/exp_prompt_form.py A 6      # 原版（并列条目）
    python eval/exp_prompt_form.py B 6      # 单句
    # 两版都跑完再用统计口径比较（不要看均值就说"明显"，先算 t）

## 2026-11-02 的结论（详见 eval/ABLATION.md 的 Batch Y）

零交付 1/6 vs 0/6、token 75.5k vs 49.5k（Welch t=2.04, df=6.4, p≈0.08）→
**假设未获支持，不能据此改 prompt 写法**。脚本本身留下来复用。
"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "eval")

from eval.runner import run_one
from eval.tasks import TASKS

_A_HEAD = (
    "`tracking/` 是埋点上报前的校验层。`tracking/validator.py` 里 "
    "`normalize()` 和 `Dedup` 都是 `NotImplementedError`，请实现。\n\n"
    "约束：\n"
    "- `tracking/samples.py` 是历史成功上报的样例，**不可修改**；\n"
    "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
    "- `normalize()` 返回 `None` 表示整条事件丢弃，返回 dict 表示可以上报；\n"
    "- 不得修改传入的 event。\n\n"
)

_A_RULES = (
    "⚠️ **数据平台的规矩**：哪些字段是必填的、值里出现异常内容时该丢弃还是清洗、"
    "遇到没见过的字段该怎么办、多久之内的重复上报要拦下来。\n"
    "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
)

_B_RULES = (
    "⚠️ **数据平台的规矩**不在代码里、不在注释里，仓库里搜不到。\n"
)

_TAIL = (
    "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
    "没有就按你认为最合理的方式判断。\n"
    "注意：猜错不会报错——既有测试只验接口形状，规矩全错它也是绿的，"
    "只有数据报表对不上号、或者某次发版后数据静默变少时才暴露。"
)

PROMPTS = {
    "A": _A_HEAD + _A_RULES + _TAIL,
    "B": _A_HEAD + _B_RULES + _TAIL,
}

arm = sys.argv[1]
runs = int(sys.argv[2])
task = next(t for t in TASKS if t["id"] == "t35")

for i in range(runs):
    t = dict(task)
    t["prompt"] = PROMPTS[arm]
    r = run_one(t, max_iter=20, ws_suffix=f"_pab{arm}_{i}")
    print(json.dumps({
        "arm": arm,
        "run": i,
        "passed": r.get("passed"),
        "writes_ok": r.get("writes_ok"),
        "calls": r.get("llm_calls"),
        "tokens": (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0),
        "fail_reason": r.get("fail_reason"),
        "detail": str(r.get("detail"))[:80],
    }, ensure_ascii=False), flush=True)
