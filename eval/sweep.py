"""难度扫描（difficulty sweep）：把「难度」当连续旋钮，扫出通过率的断裂点。

## 为什么需要它（承接 Batch H 的诊断）

Batch H 的尺子自检报了一句关键警告：**「时过时不过」的任务一个都没有**，
说明任务难度分布是两极的（要么全过、要么全不过），中间是空的。
过去造题的方式是"猜难度"——猜难了全失败、猜简单了全通过，永远撞不到边界。

本脚本换成**扫描**：同一个任务、连续变化的规模，量出「规模 → 通过率」曲线。
曲线从 100% 掉到 0% 的那一段，就是模型的能力边界。

## 关键设计：两个维度必须分开扫

文件变大和"要改的地方变多"是两件不同的事，混在一起就归因不了：

- `--dim size`  ：固定 6 个类、固定 12 个修改点，**只加样板方法**让文件变大
                  → 测「上下文压力」（读不完 / 分块读丢全局）
- `--dim count` ：固定总行数、**只加类数**让修改点变多
                  → 测「覆盖广度」（会不会漏改）

只有把两个维度拆开，才能回答：到底是"文件太大撑爆上下文"，还是"要改的地方太多漏掉"？
Batch H 之前把这两件事混在"加难度"里，所以只能得出"大文件反而简单"这种没法归因的结论。

## 双重校验（防止造出死题）

每一档在正式跑之前，先确认：
1. **初始必败**（setup 后不跑 agent，verify 必须失败）——否则该档没有区分度
2. **参考解必过**（套用参考解，verify 必须通过）——否则该档是死题（作弊也过不了）

继承自 `selftest_tasks.py` 的纪律。**扫出来的每一档都必须是"可解但没那么容易解"的题。**

用法（在 cli_coding_agent 目录下，必须用项目 venv）：
    python eval/sweep.py --dim size  --runs 2
    python eval/sweep.py --dim count --runs 2
    python eval/sweep.py --dim both  --runs 2 --out eval/EVAL.sweep.md
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from runner import run_one  # noqa: E402
from tasks.t25_huge_file_audit import _gen_source  # noqa: E402

WORKSPACES = _HERE / "workspaces"

# 6 个基准类（与 t25 一致），类数 > 6 时按规则扩展
_BASE_CLASSES = [
    ("UserRepo", "user"),
    ("OrderRepo", "order"),
    ("ProductRepo", "product"),
    ("InvoiceRepo", "invoice"),
    ("ShipmentRepo", "shipment"),
    ("PaymentRepo", "payment"),
]

# 标定值：每个类约 10 行表头 + 每组约 35 行（实测 n_groups=28 → 每类 990 行）
_ROWS_PER_GROUP = 35
_ROWS_PER_CLASS_HEAD = 10

_MARK = "# AUDIT-ADDED: 已审计"


def _make_classes(n: int) -> list[tuple[str, str]]:
    """生成 n 个类名；前 6 个与 t25 完全一致，超出部分按规则扩展。"""
    if n <= len(_BASE_CLASSES):
        return _BASE_CLASSES[:n]
    out = list(_BASE_CLASSES)
    for i in range(len(_BASE_CLASSES), n):
        out.append((f"Entity{i}Repo", f"entity{i}"))
    return out


def _groups_for_target(n_classes: int, target_rows: int) -> int:
    """反推 n_groups，使总行数最接近 target_rows（用于 --dim count 固定规模）。"""
    per_class = target_rows / max(1, n_classes)
    g = (per_class - _ROWS_PER_CLASS_HEAD) / _ROWS_PER_GROUP
    return max(1, round(g))


def _apply_ref(src: str) -> str:
    """参考解：在每个 __init__ 末尾加 self._audit=[]，在每个 _normalize 首行加审计注释。

    靠代码结构定位而不是类名——这样对任意类数/规模都成立，
    也顺便证明"真的有唯一正确的改法"，不是靠猜。
    """
    out = []
    for line in src.splitlines():
        out.append(line)
        stripped = line.strip()
        if stripped == "self._cache = {}":          # 每个仓库类 __init__ 的最后一行
            out.append("        self._audit = []")
        elif stripped == "def _normalize(self, row):":
            out.append(f"        {_MARK}")
    return "\n".join(out) + "\n"


def _verify_impl(src: str, classes: list[tuple[str, str]], base_lines: int,
                 wd) -> tuple[bool, str]:
    """与 t25 等价的严格校验，但类数/行数参数化。

    检查项（任一不过即失败）：
      1. 文件行数不得少于生成时（防止"重写文件"这种作弊）
      2. 审计标记数 == n_classes
      3. self._audit = [] 数 == n_classes
      4. 每个类仍存在、_normalize 数 == n_classes
      5. 能 import
      6. 每个类能实例化且带 _audit

    `wd=None` 表示只做静态检查（用于参考解预校验，不落盘）。
    """
    lines = src.splitlines()
    if len(lines) < base_lines:
        return False, f"行数 {len(lines)} < 原始 {base_lines}，疑似被重写"

    n_mark = src.count(_MARK)
    if n_mark != len(classes):
        return False, f"审计标记应 {len(classes)} 处，实际 {n_mark}"

    n_init = src.count("self._audit = []")
    if n_init != len(classes):
        return False, f"self._audit = [] 应 {len(classes)} 处，实际 {n_init}"

    for name, _ in classes:
        if f"class {name}:" not in src:
            return False, f"类 {name} 丢失"
    n_norm = src.count("def _normalize(self, row):")
    if n_norm != len(classes):
        return False, f"_normalize 应 {len(classes)} 个，实际 {n_norm}"

    if wd is None:
        return True, "静态检查通过"

    p = subprocess.run(
        [sys.executable, "-c", "import repos; print('OK')"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0 or "OK" not in p.stdout:
        return False, f"import 失败: {p.stderr.strip()[-200:]!r}"

    code = (
        "import repos\n"
        "class C:\n"
        "    def execute(self, *a, **k):\n"
        "        class R:\n"
        "            def fetchone(self): return None\n"
        "            def fetchall(self): return []\n"
        "        return R()\n"
        "c = C()\n"
        + "".join(f"assert hasattr(repos.{n}(c), '_audit')\n" for n, _ in classes)
        + "print('OK')\n"
    )
    p = subprocess.run(
        [sys.executable, "-c", code.strip()], cwd=str(wd),
        capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0 or "OK" not in p.stdout:
        return False, f"实例化/属性检查失败: {p.stderr.strip()[-200:]!r}"
    return True, f"{len(classes)} 个类均已加入审计字段（{len(lines)} 行未被重写）"


def _make_task(dim: str, n_classes: int, n_groups: int, tid: str) -> dict:
    """构造一档扫描任务（setup / verify 都按该档参数定死，严格性与 t25 持平）。"""
    classes = _make_classes(n_classes)
    src0 = _gen_source(classes, n_groups)
    base_lines = len(src0.splitlines())

    # 参考解预校验：确认"存在一个能让 verify 通过的改法"（死题的早期警报）
    ref_ok, ref_detail = _verify_impl(_apply_ref(src0), classes, base_lines, None)

    def setup(wd):
        (wd / "repos.py").write_text(src0, encoding="utf-8")

    def verify(wd):
        f = wd / "repos.py"
        if not f.exists():
            return False, "缺少 repos.py"
        return _verify_impl(f.read_text(encoding="utf-8"), classes, base_lines, wd)

    names = " / ".join(n for n, _ in classes)
    shown = names if len(names) <= 120 else names[:120] + " …"
    return {
        "id": tid,
        "name": f"审计改造({n_classes}类/{base_lines}行)",
        "prompt": (
            f"`repos.py` 是一个遗留的数据访问层，里面有 {n_classes} 个仓库类"
            f"（{shown}）。\n\n"
            f"请给**这 {n_classes} 个类每一个**都加一个审计能力：\n"
            "1) 在各类的 `__init__` 末尾加上 `self._audit = []`；\n"
            f"2) 在各类的 `_normalize` 方法体第一行加上注释 `{_MARK}`。\n\n"
            "注意：**每个类都各有一个同名的 `_normalize`，但它们是不同类的不同方法**，"
            "必须分别处理，不能只改一个或全局替换。\n"
            f"改完后 `import repos` 不能报错，且 {n_classes} 个类都要能被实例化、都带 `_audit` 属性。\n"
            "原有代码结构不得删减或重写（只做增量修改）。"
        ),
        "setup": setup,
        "verify": verify,
        # 扫描元数据，不参与 agent 运行
        "_dim": dim,
        "_n_classes": n_classes,
        "_n_groups": n_groups,
        "_lines": base_lines,
        "_ref_ok": ref_ok,
        "_ref_detail": ref_detail,
    }


def _selftest(task: dict, tmp_wd: pathlib.Path) -> tuple[bool, str]:
    """双重校验：① setup 后（未跑 agent）verify 必须失败；② 套参考解后必须通过。"""
    tmp_wd.mkdir(parents=True, exist_ok=True)
    for f in tmp_wd.iterdir():
        if f.is_file():
            f.unlink()
    task["setup"](tmp_wd)

    passed0, detail0 = task["verify"](tmp_wd)
    if passed0:
        return False, f"初始就通过（无区分度）: {detail0}"

    src = (tmp_wd / "repos.py").read_text(encoding="utf-8")
    (tmp_wd / "repos.py").write_text(_apply_ref(src), encoding="utf-8")
    passed1, detail1 = task["verify"](tmp_wd)
    if not passed1:
        return False, f"参考解也过不了（死题）: {detail1}"

    return True, f"初始失败（{detail0}）/ 参考解通过"


def _bar(rate: float, width: int = 20) -> str:
    n = round(rate * width)
    return "█" * n + "·" * (width - n)


def _infra_label(infra: str) -> str:
    """基础设施健康度（时间维度的判读）：耗时长到底是任务重还是 API 慢。"""
    return {"ok": "🟢 正常", "api_jitter": "🟡 偶发慢",
            "api_slow": "🔴 整体限流", "unknown": "⚪ 未采集"}.get(infra, infra)


def classify_failure_mode(row: dict) -> str:
    """失败模式分类——「没改」「改多」「漏改」是完全不同的病，不能都叫「失败」。

    实测同一档位两种模式都出现过：
    - 48 类失败一次：`应 48 处，实际 49` → 把基类也当成仓库类（**改多了，语义误判**）
    - 96 类失败一次：`应 96 处，实际 0`，却烧了 41 次调用 → **探索循环，一处没动手**
    前者说明题目有陷阱，后者说明预算被探索吃光，修法完全不同。
    """
    import re as _re
    detail = row.get("detail") or ""
    m = _re.search(r"应\s*(\d+)\s*处[，,]?\s*实际\s*(\d+)", detail)
    if m:
        want, got = int(m.group(1)), int(m.group(2))
        if got == 0:
            return "未动手（探索循环）"
        if got > want:
            return f"改多了（差 {got - want} 处）"
        return f"漏改（差 {want - got} 处）"
    if row.get("status") == "max_iter":
        return "预算耗尽"
    if row.get("status") == "error":
        return "运行异常"
    return "行为不符"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dim", choices=["size", "count", "both"], default="size")
    ap.add_argument("--runs", type=int, default=2, help="每档重复次数")
    ap.add_argument("--max-iter", type=int, default=30, help="大文件需要更多步")
    ap.add_argument("--groups", nargs="*", type=int, default=[2, 4, 8, 16, 28, 48],
                    help="--dim size 的档位（每组约 35 行）")
    ap.add_argument("--counts", nargs="*", type=int, default=[2, 4, 6, 12, 24],
                    help="--dim count 的档位（类数）")
    ap.add_argument("--target-rows", type=int, default=6000,
                    help="--dim count 下固定的总行数（分离规模与修改点数）")
    ap.add_argument("--out", default=str(_HERE / "EVAL.sweep.md"))
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()

    out_path = pathlib.Path(args.out)
    t0 = time.time()

    # ---- 构造档位 ----
    specs: list[tuple[str, int, int]] = []
    if args.dim in ("size", "both"):
        for g in args.groups:
            specs.append(("size", 6, g))
    if args.dim in ("count", "both"):
        for c in args.counts:
            specs.append(("count", c, _groups_for_target(c, args.target_rows)))

    tasks = []
    for i, (dim, nc, ng) in enumerate(specs, start=1):
        tasks.append(_make_task(dim, nc, ng, f"s{i:02d}_{dim}_{nc}x{ng}"))

    print(f"▶ 难度扫描：{len(tasks)} 档 × {args.runs} 次（max_iter={args.max_iter}）")
    for t in tasks:
        print(f"  · {t['id']}: {t['_n_classes']} 类 × {t['_n_groups']} 组 "
              f"= {t['_lines']} 行  参考解预校验={'✅' if t['_ref_ok'] else '❌'}")

    # ---- 双重校验（造死题防线）----
    if not args.skip_selftest:
        print("\n▶ 双重校验（初始必败 / 参考解必过）...")
        bad = []
        for t in tasks:
            ok, msg = _selftest(t, WORKSPACES / f"_selftest_{t['id']}")
            print(f"  {'✅' if ok else '❌'} {t['id']}: {msg}")
            if not ok:
                bad.append(t["id"])
        if bad:
            print(f"\n❌ 以下档位不合格，已中止（避免用死题污染结论）：{bad}")
            return

    # ---- 正式扫描 ----
    print("\n▶ 开始扫描 ...")
    results = []
    for t in tasks:
        rows = []
        for rep in range(args.runs):
            suffix = f"_{t['id']}_r{rep}_{time.strftime('%m%d_%H%M%S')}"
            print(f"  · {t['id']} 第 {rep + 1}/{args.runs} 次 ...", end=" ", flush=True)
            r = run_one(t, args.max_iter, ws_suffix=suffix)
            rows.append(r)
            print(f"{'✅' if r['passed'] else '❌'} "
                  f"({r['llm_calls']} calls, {r['prompt_tokens']:,} tok, {r['seconds']}s)",
                  flush=True)

        # 逐次结果如实保存（绝不靠通过率反推）
        each = [r["passed"] for r in rows]
        npass = sum(each)
        fails = [r for r in rows if not r["passed"]]
        wins = [r for r in rows if r["passed"]]
        # 成本只按「成功轨迹」算：失败轨迹常是空转/循环（实测一次 41 调用 358k token
        # 却一处未改），混进均值会把成本趋势污染成「规模越大越贵」的假象。
        base_cost = wins or rows
        results.append({
            "id": t["id"],
            "dim": t["_dim"],
            "n_classes": t["_n_classes"],
            "n_groups": t["_n_groups"],
            "lines": t["_lines"],
            "runs": args.runs,
            "each": each,
            "passed": npass,
            "rate": npass / args.runs,
            "avg_tokens": round(sum(r["prompt_tokens"] for r in base_cost) / len(base_cost)),
            "avg_calls": round(sum(r["llm_calls"] for r in base_cost) / len(base_cost), 2),
            "avg_sec": round(sum(r["seconds"] for r in base_cost) / len(base_cost), 1),
            "fail_tokens": (round(sum(r["prompt_tokens"] for r in fails) / len(fails))
                            if fails else None),
            "fail_mode": (classify_failure_mode(fails[0]) if fails else "—"),
            "lat_p50": round(sorted(r.get("lat_p50", 0) for r in rows)[len(rows) // 2], 1),
            # 取该档所有运行里最差的基础设施判读（有一 run 限流，整档耗时都不可信）
            "infra": max((r.get("infra", "unknown") for r in rows),
                         key=lambda x: {"ok": 0, "unknown": 1, "api_jitter": 2,
                                        "api_slow": 3}.get(x, 0)),
            "reason": (fails[0]["detail"][:120] if fails else "—"),
        })

    # ---- 报告 ----
    L: list[str] = [
        "# MiniCode 难度扫描报告（定位能力边界）",
        "",
        f"> `python eval/sweep.py --dim {args.dim} --runs {args.runs}`，"
        f"{len(tasks)} 档 × {args.runs} 次，max_iter={args.max_iter}。",
        "> 目的：**不再猜难度，而是扫难度**。曲线从 100% 掉到 0% 的区间即能力边界，",
        "> 该区间的任务才是「既有区分度、又确实可解」的评测资产。",
        "",
        "## 一、扫描结果",
        "",
        "| 档位 | 维度 | 类数(修改点) | 组数 | 行数 | 通过率 | "
        + " | ".join(f"第{i + 1}次" for i in range(args.runs))
        + " | 平均token | 平均调用 | 平均耗时 | p50延迟 | 基础设施 |",
        "| --- | --- | ---: | ---: | ---: | --- | "
        + " | ".join("---" for _ in range(args.runs)) + " | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in results:
        cells = " | ".join("✅" if e else "❌" for e in r["each"])
        L.append(
            f"| {r['id']} | {r['dim']} | {r['n_classes']} | {r['n_groups']} "
            f"| {r['lines']:,} | **{r['passed']}/{r['runs']}** | {cells} "
            f"| {r['avg_tokens']:,} | {r['avg_calls']} | {r['avg_sec']}s "
            f"| {r['lat_p50']}s | {_infra_label(r['infra'])} |"
        )

    L += ["", "## 二、通过率曲线（找断裂点）", "", "```"]
    for r in sorted(results, key=lambda x: x["lines"]):
        L.append(f"{r['lines']:>7,} 行  {_bar(r['rate'])}  {r['rate'] * 100:5.0f}%  {r['id']}")
    L += ["```", ""]

    # ---- 边界区判定 ----
    L += ["## 三、边界区判定（本报告的真正产出）", ""]
    boundary = [r for r in results if 0 < r["rate"] < 1]
    allpass = [pts for pts in (r for r in results if r["rate"] == 1)]
    allfail = [r for r in results if r["rate"] == 0]

    if boundary:
        L.append(f"🟢 **找到 {len(boundary)} 个边界区档位**（通过率在 0 与 100% 之间）：")
        L += ["", "| 档位 | 规模 | 通过率 | 失败模式 | verify 原文 |",
              "| --- | ---: | --- | --- | --- |"]
        for r in boundary:
            L.append(f"| {r['id']} | {r['lines']:,} 行 | {r['passed']}/{r['runs']} "
                     f"| {r['fail_mode']} | {r['reason']} |")
        L += ["", "→ **这些档位应被收编进评测集**：既能失败（有区分度），又确实可解",
              "（已过双重校验），是唯一能让 feature 优化显出差异的地形。", "",
              "⚠️ **但必须先读「失败模式」再归类**：",
              "- 「未动手 / 预算耗尽」= 探索阶段耗光了预算 → 属于**能力（或策略）上限**；",
              "- 「改多了」= 把不该改的目标也改了 → 属于**语义辨析陷阱**，题目本身在考判断力；",
              "- 「漏改」= 覆盖不完整 → 属于**规模/记忆压力**。",
              "三者混成一句「难度不够」，就会重复之前两周「猜难度」的错误。"]
    else:
        L.append("🔴 **没有找到边界区**：所有档位要么全过、要么全败。")
        L.append("")
        if allpass and not allfail:
            if args.dim == "size":
                L.append("→ 全部通过说明**文件规模不是瓶颈**。agent 不整读文件、靠 Grep 定位，")
                L.append("文件再大也不影响它。**结论：换维度** → 扫 `--dim count`（修改点数量），")
                L.append("因为「漏改一个」比「文件太大」更可能是失败来源。")
            elif args.dim == "count":
                L.append("→ 全部通过说明**修改点数量也不是瓶颈**（在这个量级内）。")
                L.append("结合 `size` 维度的结果（规模也不构成瓶颈），可以下一个更强的判断：")
                L.append("")
                L.append("> **这个任务族（机械式批量修改）对当前 agent 已完全饱和。**")
                L.append("> 它有两条通用策略：文件大就用 Grep 定位，改动多就写脚本批量处理——")
                L.append("> 两者都不依赖「把大量内容装进上下文」，所以规模与数量都打不穿它。")
                L.append("")
                L.append("**结论：不要再加量了，要换任务性质**——")
                L.append("必须用**不可脚本化**的难度：每个位置改法不同、需要判断该不该改、")
                L.append("或需要从测试/文档反推隐含规则。任何「重复 N 次同一个动作」的题，")
                L.append("都只会被它用一个 Bash 脚本吃掉。")
            else:
                L.append("→ 两个维度都全过，说明这批任务的难度旋钮已经拧到头。")
        elif allfail and not allpass:
            L.append("→ 全部失败说明最小档就已超限，应把档位往小调。")
        else:
            L.append("→ 两极分化：难度跳变太陡，档位间隔需要加密。")

    if allfail:
        ids = "、".join(f"{r['id']}({r['lines']:,}行)" for r in allfail)
        L += ["", "⚫ 全败档位（已超能力上限，可作上限参照，不适合作常规评测）：" + ids]

    # ---- 非单调检测：通过率不随规模单调下降 → 波动，而非难度轴 ----
    if boundary and allpass:
        ordr = sorted(results, key=lambda x: x.get("lines", 0))
        rates = [r["rate"] for r in ordr]
        if any(rates[i] < rates[i + 1] for i in range(len(rates) - 1)):
            L += [
                "", "### ⚠️ 通过率非单调：这更像是波动，不是难度轴", "",
                "通过率没有随规模单调下降（规模更大的档反而更高）。两种解释必须分开：", "",
                "| 解释 | 特征 | 怎么区分 |",
                "| --- | --- | --- |",
                "| 任务在能力边界上抖 | **同一档**重复运行结果不一致 | 看「第 N 次」列的勾叉 |",
                "| 基础设施不稳 | p50 延迟偏高、slow_calls 多 | 看 p50 延迟 / 基础设施列 |", "",
                "→ **只要同一档出现「一次过、一次不过」，这档就已经在边界上了**。",
                "但失败原因必须逐条读：若是「改多了一处」这类**语义误判**（把基类也当成了目标类），",
                "那是**题目里埋了陷阱**，不是能力上限——可以收编，但归类要写成「语义辨析」，",
                "不能写成「规模超限」。",
            ]

    # ---- 基础设施健康度（时间维度的判读，专治「把 API 慢误读成任务难」） ----
    slow_tiers = [r for r in results if r.get("infra") in ("api_slow", "api_jitter")]
    L += [
        "", "## 四、基础设施健康度（先确认耗时可信，再看耗时）", "",
        "| 档位 | p50 延迟 | 判读 |", "| --- | ---: | --- |",
    ]
    for r in sorted(results, key=lambda x: x.get("lines", 0)):
        L.append(f"| {r['id']} | {r.get('lat_p50', 0)}s | {_infra_label(r.get('infra', 'unknown'))} |")
    if slow_tiers:
        L += ["", "⚠️ 本轮存在慢请求（p50 ≥ 30s 或 有 ≥30s 的单次调用）："
              + "、".join(r["id"] for r in slow_tiers)
              + "。**这些档位的「耗时」字段不可与其他批次比较**，通过率仍可用。"]
    else:
        L += ["", "🟢 全部档位 p50 延迟正常，耗时字段可跨批次比较。"]

    # ---- 成本趋势 ----
    L += ["", "## 五、成本趋势（token 随规模怎么涨）", "",
          "> **只统计成功轨迹**。失败轨迹常是空转（实测一次 41 调用 358k token 却一处未改），",
          "> 混进均值会把趋势污染成「规模越大越贵」的假象。失败成本单列在最后一列。", "",
          "| 行数 | 成功轨迹 token | 平均调用 | token/行 | 失败轨迹 token |",
          "| ---: | ---: | ---: | ---: | ---: |"]
    by_lines = sorted(results, key=lambda x: x["lines"])
    for r in by_lines:
        per = r["avg_tokens"] / max(1, r["lines"])
        ft = f"{r['fail_tokens']:,}" if r.get("fail_tokens") else "—"
        L.append(f"| {r['lines']:,} | {r['avg_tokens']:,} | {r['avg_calls']} | {per:.2f} | {ft} |")

    # 判读要看**趋势**而不是绝对值：token/行 随规模上升 = 有重复读；下降 = 定位式命中。
    L.append("")
    if len(by_lines) >= 2:
        first, last = by_lines[0], by_lines[-1]
        per_first = first["avg_tokens"] / max(1, first["lines"])
        per_last = last["avg_tokens"] / max(1, last["lines"])
        ratio = per_last / per_first if per_first else 1.0
        L.append("**趋势判读**（看 token/行 是升还是降，不看单点绝对值）：")
        L.append("")
        L.append(f"- 最小档 {first['lines']:,} 行：{per_first:.2f} token/行；"
                 f"最大档 {last['lines']:,} 行：{per_last:.2f} token/行"
                 f"（比值 {ratio:.2f}×）")
        if ratio > 1.3:
            L.append("- 🔴 **token/行 随规模上升** → 存在重复读，上下文压力真实存在，"
                     "压缩类优化在这里有戏。")
        elif ratio < 0.77:
            L.append("- 🟢 **token/行 随规模显著下降** → agent 是**定位式**工作"
                     "（Grep 找位置、只读需要的片段），规模越大越摊薄。")
            L.append("  **这意味着压缩类优化在这个维度上永远不会赢**：它没有"
                     "「反复重读历史」这个病灶可治。")
        else:
            L.append("- 🟡 token/行 基本持平 → 成本随规模线性增长，没有重复读的浪费，"
                     "也没有显著的摊薄效应。")

    L += [
        "",
        "## 六、方法论说明",
        "",
        "- **为什么是扫描而不是造题**：造题的难度不可控（靠猜），扫描的难度连续可调（靠量）。",
        "  扫描能得到「通过率 vs 规模」的完整曲线从而定位断点；造题只能得到一个个孤立数据点。",
        "- **为什么两个维度分开**：文件变大 ≠ 要改的地方变多。",
        "  `size` 维度固定 12 个修改点只加样板代码；`count` 维度固定总行数只加类数。",
        "  混在一起扫（Batch H 之前的做法）无法归因到底是「读不完」还是「改漏了」。",
        "- **双重校验不可省**：每档都要求「初始必败 + 参考解必过」，",
        "  否则扫出来的「高难度」可能是死题，得到的能力边界全是假的。",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}，总耗时 {round(time.time() - t0, 1)}s",
    ]

    out_path.write_text("\n".join(L), encoding="utf-8")

    print("\n" + "=" * 60)
    for r in results:
        mark = "✅" if r["rate"] == 1 else ("🟡" if r["rate"] > 0 else "❌")
        print(f"  {mark} {r['lines']:>7,} 行  {r['passed']}/{r['runs']}  {r['id']}")
    print(f"\n✅ 报告已写入 {out_path}")


if __name__ == "__main__":
    main()
