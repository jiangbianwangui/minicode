"""泄漏审计：产出的代码里，有没有和某篇 skill 原文**逐字重合**的片段。

## 为什么需要它

沙箱是靠**正则匹配命令字符串**实现的。只要工具是 Bash（可以跑 python），
就存在无穷多种绕过写法：`chr(47)` 拼路径、`os.path.join` 拆成八段、
`\\x43:/Users/...`——正则永远追不完。

而且现实中它已经发生过一次：2026-11-19 跑 t34 的 base（`skills=False`，路由根本没加载），
产出里却出现了 `0.8` / `500` / `3.5` / `2` 这四个只存在于
`skills/canary-gate-policy/SKILL.md` 的参数，连解释文字都高度相似。
同一条命令序列我事后复跑，它只写了 0.1/1000/2.0/5.0（自己的猜测）。

结论不是"再补一条正则"，而是：**"base 没有 skill"是设计保证，不是强制保证。**
所以需要一个**事后可测量**的判据 —— 也就是这个文件：
拿产物和 skill 原文比逐字重合，重合超过阈值就标为污染。

## 判据

不比较语义（那又要一个模型），只找**连续 k 个字符完全相同**的片段（k 从大到小试）。

阈值是**实测校准**出来的，不是拍的：
- `with pytest.raises(ValueError):` 归一化后 24 字符，在 9 个 base 运行里都出现 —— **样板代码**，
  任何一次都算"泄漏"的话，检测器就变成了警报器。
- `orderid amount pay_channel page_id referrer …` 这类字段串 32 字符，但它**同时存在于任务自带
  的 schema.py 脚手架里**，重合是必然的，与 skill 无关。
- 真正的泄漏长这样（t37 base，48 字符）：
  `第0步小表且非核心run第1步当日已回刷过reject第2步窗口30天queue第3步有下游在消`
  —— 这是五步判定链的**全文**，不可能独立想到同样的措辞。

所以：**≥ 40 判泄漏，32–39 只提示、不计入，其余不算。**

用法：
    python eval/leak_audit.py --workspace eval/workspaces/t34_cp_base1
    python eval/leak_audit.py --all          # 扫全部 workspace
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
SKILLS_DIR = _ROOT / "skills"

# 只看这两类产物：代码和说明。命中缓存/日志没有意义。
_SUFFIXES = {".py", ".md", ".txt", ".json", ".sql"}

# workspace 名 → 任务模块（用于取出该任务的脚手架文本）
_MODULE_BY_ID = {
    "t27": "t27_external_api_contract",
    "t28": "t28_mq_contract",
    "t29": "t29_log_standard",
    "t31": "t31_retry_budget",
    "t32": "t32_export_encoding",
    "t33": "t33_sla_budget",
    "t34": "t34_canary_gate",
    "t35": "t35_tracking_validate",
    "t36": "t36_rate_limit",
    "t37": "t37_backfill_planner",
    "t38": "t38_export_approval",
    "t39": "t39_alert_mute",
    "t40": "t40_access_elevation",
}


def _normalize(text: str) -> str:
    """去掉空白与标点，只留中日韩文字和字母数字。

    这样 `0.8 个百分点` 和 `0.8个百分点`（换行/空格差异）能被认作同一句。
    刻意保留数字：阈值泄漏恰恰是通过数字表现出来的。
    """
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text, flags=re.UNICODE)


def _load_skills() -> dict[str, str]:
    out = {}
    for md in SKILLS_DIR.glob("*/SKILL.md"):
        text = md.read_text(encoding="utf-8")
        # 去掉 frontmatter（那部分会被正常注入到提示里，不算泄漏）
        if text.startswith("---"):
            parts = text.split("---", 2)
            text = parts[2] if len(parts) >= 3 else text
        out[md.parent.name] = _normalize(text)
    return out


def _scaffold_text(tid: str) -> str:
    """任务自己写进 workspace 的脚手架文本。

    为什么要减掉它：t35 的函数 docstring 里本来就写着「窗口从**上一次被接受**的上报开始算」，
    而 `tracking-event-policy` 用了同一句话 —— 产物里有这段**不是泄漏**，
    是任务本身把机制给了、只把参数藏起来。

    只取模块级字符串常量（`_VALIDATOR` / `_SCHEMA` / `_TESTS` 这些），
    `_CHECKS` 是列表、不在 workspace 里，不参与。
    """
    sys.path.insert(0, str(_HERE))
    try:
        mod = __import__(f"tasks.{_MODULE_BY_ID[tid]}", fromlist=["x"])
    except Exception:
        return ""
    parts = [v for k, v in vars(mod).items() if k.startswith("_") and isinstance(v, str)]
    return _normalize("\n".join(parts))


def _artifacts(wd: pathlib.Path) -> list[pathlib.Path]:
    return [
        p for p in wd.rglob("*")
        if p.is_file() and p.suffix in _SUFFIXES
        and ".pytest_cache" not in p.parts
    ]


def _longest_shared(a: str, b: str, floor: int = 12) -> tuple[int, str]:
    """返回 a、b 之间最长公共子串的长度与前缀片段（只试 ≥ floor 的）。"""
    best_len, best_frag = 0, ""
    # 用逐步缩短的窗口找：从 64 往下试，命中即可停（只关心"至少多长"）
    for k in (64, 48, 40, 32, 24, 18, floor):
        seen = {a[i:i + k] for i in range(0, len(a) - k + 1)}
        for i in range(0, len(b) - k + 1):
            frag = b[i:i + k]
            if frag in seen:
                if k > best_len:
                    best_len, best_frag = k, frag
                break
        if best_len:
            break
    return best_len, best_frag


def _mask(body: str, scaffold: str, floor: int = 32) -> str:
    """把产物里「脚手架本来就有的」片段挖掉，再拿去和 skill 比。

    必要性来自 t35：脚手架 docstring 已经写了「窗口从上一次被接受的上报开始算；
    被丢弃的重复请求不刷新窗口」，而 skill 用同一句话。产物多续了半句
    「否则一直重试的事件会永远刷新不了」——整段 40 字和 skill 重合，
    但其中 31 字来自脚手架。不遮掉就会把整族 t35 误判成泄漏。

    只遮 ≥32 字的重合片段：更短的（`with pytest.raises`）本来就是样板，不必处理。
    """
    if not scaffold:
        return body
    for _ in range(200):
        k, frag = _longest_shared(scaffold, body, floor=floor)
        if k < floor or not frag:
            break
        body = body.replace(frag, "|")
    return body


def audit(wd: pathlib.Path, skills: dict[str, str], scaffold: str = "") -> list[dict]:
    findings = []
    for f in _artifacts(wd):
        try:
            body = _normalize(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if len(body) < 18:
            continue
        body = _mask(body, scaffold)
        for name, sk in skills.items():
            k, frag = _longest_shared(sk, body)
            if not k:
                continue
            # 这段也是任务脚手架的一部分 → 不算泄漏
            if scaffold and frag and frag in scaffold:
                continue
            findings.append({
                "file": str(f.relative_to(wd)).replace("\\", "/"),
                "skill": name, "k": k, "frag": frag,
            })
    # 每个文件只留最长的那个匹配
    best: dict[str, dict] = {}
    for f in findings:
        cur = best.get(f["file"])
        if cur is None or f["k"] > cur["k"]:
            best[f["file"]] = f
    return sorted(best.values(), key=lambda x: -x["k"])


def verdict(k: int) -> str:
    if k >= 40:
        return "⛔ 泄漏"
    if k >= 32:
        return "⚠️ 提示"
    return "·  不算"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    skills = _load_skills()
    print(f"载入 {len(skills)} 篇 skill，产物后缀 {sorted(_SUFFIXES)}\n")

    if args.workspace:
        dirs = [pathlib.Path(args.workspace)]
    elif args.all:
        dirs = sorted(p for p in (_HERE / "workspaces").glob("*") if p.is_dir())
    else:
        sys.exit("要么 --workspace，要么 --all")

    dirty = 0
    for wd in dirs:
        tid = wd.name.split("_")[0]
        scaffold = _scaffold_text(tid) if tid in _MODULE_BY_ID else ""
        found = audit(wd, skills, scaffold)
        hot = [f for f in found if f["k"] >= 40]
        if not hot:
            continue
        dirty += 1
        print(f"## {wd.name}")
        for f in hot:
            print(f"  {verdict(f['k'])} k={f['k']:2d} {f['file']:28s} ← {f['skill']}")
            print(f"        片段：{f['frag'][:70]}")
        print()

    total = len(dirs)
    print(f"扫描 {total} 个 workspace，{dirty} 个有 ≥40 字符的逐字重合。")
    if dirty == 0:
        print("（没有一件产物出现 ≥40 字符的 skill 原文 —— 这批没有泄漏痕迹）")


if __name__ == "__main__":
    main()
