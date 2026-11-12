"""用 LLM 从每篇 skill 生成 3 条 exemplar 查询，解决「自问自答」的嫌疑。

问题：
    上一版语义通道的锚点是**评测集那 12 条任务 prompt**。它们同时也是判分依据，
    拿它们当检索锚点，等于"用答案找答案"——召回率一定好看，但没有说服力。

做法：
    只给模型每篇 skill 的 name / when_to_use / description / tags，
    让它写 3 条**同事在群里随口问的一句话**（10–25 字，日常说法）。
    产物写进 `skills/<name>/examples.txt`（一行一条），供检索用。

    注意：生成的查询**不参考测试集**，所以后面测出来的召回率才是干净的。

用法：python eval/gen_exemplars.py [--skills a,b] [--n 3]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from agent import _build_llm  # noqa: E402
from skill_router import default_router  # noqa: E402

PROMPT = """你在帮一个 AI 编程助手做「什么时候该查团队规范」的检索样本。

下面是团队里的一篇工程规范：

名字：{name}
什么时候该用：{when}
摘要：{desc}
关键词：{tags}

请写 {n} 条**真实工程师会打在对话框里的那种问题**，要求：
- 每条 10–25 个汉字，口语，像同事在群里随口问的一句
- 都在描述**现象或困惑**，不要直接复述规范里的名词定义
- {n} 条要覆盖不同的侧面（不同场景 / 不同触发条件），不要互相改写
- 只输出 {n} 行，不要编号，不要解释，不要引号"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", default=None, help="逗号分隔的 skill 名，默认全部")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()

    router = default_router()
    targets = router.skills
    if args.skills:
        want = {s.strip() for s in args.skills.split(",")}
        targets = [s for s in router.skills if s.name in want]

    llm = _build_llm()
    for s in targets:
        msg = PROMPT.format(
            name=s.name,
            when=s.when_to_use.strip(),
            desc=s.description.strip()[:300],
            tags="、".join(s.tags),
            n=args.n,
        )
        resp = llm.invoke(msg)
        lines = [ln.strip().lstrip("-·0123456789. ").strip()
                 for ln in str(resp.content).splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln][: args.n]
        out = s.path.parent / "examples.txt"   # s.path 是 SKILL.md 本身
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"## {s.name}  → {out.relative_to(_ROOT)}")
        for ln in lines:
            print(f"   {ln}")


if __name__ == "__main__":
    main()
