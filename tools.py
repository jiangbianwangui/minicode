"""CLI 编程 Agent 的工具集（自实现，不依赖任何 Agent 框架）。

提供 6 个工具，与市面主流 coding agent（Claude Code / Cursor / OpenHands）同源：
    ReadFile  读文件
    WriteFile 创建/覆盖文件
    EditFile  精确字符串替换
    Bash      在沙箱内跑命令
    Glob      通配符找文件
    Grep      在文件中搜文本/正则

所有「文件类」工具都被限制在 workdir 内（越界直接拒绝），Bash 受超时约束。
参数校验是手写的「Schema 校验兜底」——这正是工程化 Agent 区别于「调个 prompt」
的关键点：模型可能胡编参数，必须先校验再执行。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 工具 Schema（OpenAI function-calling 格式）——既给模型看，也用于参数校验
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "ReadFile",
            "description": "读取一个文本文件的内容。可只读取前 N 行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于沙箱根目录的文件路径"},
                    "limit": {"type": "integer", "description": "最多读取的行数（可选）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WriteFile",
            "description": "创建新文件或完整覆盖已有文件。会创建必要的父目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于沙箱根目录的文件路径"},
                    "content": {"type": "string", "description": "要写入的完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "EditFile",
            "description": "把文件中的 old_string 精确替换为 new_string。old_string 必须唯一存在。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于沙箱根目录的文件路径"},
                    "old_string": {"type": "string", "description": "要被替换的原始片段（必须唯一）"},
                    "new_string": {"type": "string", "description": "替换后的新片段"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "在沙箱根目录内执行一条 shell 命令（POSIX 优先，Windows 回退 cmd）。超时会被终止。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数（可选，默认 30）"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "按通配符（如 **/*.py）在沙箱内查找文件，返回匹配的路径列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，相对于沙箱根目录"},
                    "limit": {"type": "integer", "description": "最多返回多少条（可选，默认 50）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": "在沙箱文件中递归搜索文本或正则，返回命中行（含文件名:行号:内容）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "要搜索的文本或正则表达式"},
                    "path": {"type": "string", "description": "搜索起点（可选，默认沙箱根）"},
                    "ignore_case": {"type": "boolean", "description": "是否忽略大小写（可选，默认 false）"},
                    "limit": {"type": "integer", "description": "最多返回多少条命中（可选，默认 30）"},
                },
                "required": ["pattern"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 极简参数校验（手写 Schema 校验兜底，不引第三方依赖）
# ---------------------------------------------------------------------------

def _validate(name: str, args: dict) -> tuple[bool, str]:
    """校验工具参数。返回 (ok, error_or_empty)。"""
    schema = next(
        t["function"] for t in TOOL_SCHEMAS if t["function"]["name"] == name
    )
    props = schema["parameters"]["properties"]
    required = schema["parameters"].get("required", [])

    if not isinstance(args, dict):
        return False, "参数必须是一个对象"

    for key in required:
        if key not in args or args[key] in (None, ""):
            return False, f"缺少必需参数: {key}"

    for key, val in args.items():
        if key not in props:
            return False, f"未知参数: {key}"
        expected = props[key]["type"]
        if expected == "string" and not isinstance(val, str):
            return False, f"参数 {key} 应为字符串"
        if expected == "integer" and not isinstance(val, int):
            # 模型常把数字写成字符串，做一次宽容转换
            try:
                args[key] = int(val)
            except (TypeError, ValueError):
                return False, f"参数 {key} 应为整数"
        if expected == "boolean" and not isinstance(val, bool):
            if isinstance(val, str):
                args[key] = val.strip().lower() in ("true", "1", "yes")
            else:
                return False, f"参数 {key} 应为布尔"
    return True, ""


# ---------------------------------------------------------------------------
# 沙箱工具：把相对路径锁死在 workdir 内
# ---------------------------------------------------------------------------

def _safe_path(workdir: Path, rel: str) -> Path:
    """将相对路径解析到 workdir 内，越界直接抛错。"""
    target = (workdir / rel).resolve()
    if workdir.resolve() not in target.parents and target != workdir.resolve():
        raise PermissionError(f"路径越界被拒绝（必须在沙箱内）: {rel}")
    return target


_BASH_DENY = re.compile(
    r"(rm\s+-rf\s+/|rm\s+-rf\s+~|mkfs|format\s+[a-z]:|dd\s+if=|shutdown|reboot|"
    r":\(\)\s*\{.*\}\s*;:)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 沙箱逃逸拦截（2026-10-27 加）
# ---------------------------------------------------------------------------
#
# 由来：写 t27 时，prompt 让 agent「去查技能文档」，base 模式下它没有检索工具，
# 于是自己执行 `find / -name SKILL.md` —— **真的扫到了 C:\Users\王\.claude\plugins**，
# 读到了用户主目录里的文件。跑满 30 秒超时、8 轮预算全烧在找文件上。
#
# 这件事说明：`cwd=workdir` 只限制「默认起点」，不限制「能去哪」。
# `cd /`、`find /`、绝对路径，任何一个都能走出去。
# 真正的隔离要靠容器（本项目未做，README 里一直这么写），
# 但在那之前至少要拦住「明确指向沙箱外」的写法。
#
# 只拦高置信度的逃逸模式，不碰正常用法：
#   ✅ 保留：`python -m pytest` / `pip install` / `ls -la` / `cat rates/rates.py`
#   ⛔ 拦截：`cd /` `cd ~` `cd C:\` `find /` `find ~` `ls /` `cat /etc/...`
#           以及任何出现家目录绝对路径（/Users/、/home/、C:\Users\）的命令
_ESCAPE_PATTERNS = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:cd|pushd)\s+(?:/|~|\.\./\.\.|[A-Za-z]:[\\/])"      # cd 到绝对路径/家目录
    r"|(?:^|[;&|]\s*)"
    r"(?:find|ls|dir|tree|du|grep|cat|head|tail|rm|cp|mv)\s+(?:-\S+\s+)*"
    r"(?:/|~)(?:\s|$)"                                      # 对根/家目录直接操作
    r"|/Users/|/home/[^/\s]+/|C:\\\\Users\\\\|/c/Users/|/etc/|/var/log"  # 家目录/系统路径
    r"|\bfind\s+/\s"
    r"|(?:^|[\s;&|'\"(=,])\.\.[/\\]"
    r"|(?:^|[\s;&|'\"(=,])\.\.(?=$|[\s;&|'\"()),])",
    re.IGNORECASE,
)


def _run_bash(command: str, workdir: Path, timeout: int) -> str:
    if _BASH_DENY.search(command):
        return "⛔ 该命令命中破坏性操作黑名单，已拒绝执行。请改用更安全的写法。"
    if _ESCAPE_PATTERNS.search(command):
        # 文案本身会影响行为：早先版本结尾写「用相对路径重试」，结果模型把它读成
        # 「换个写法继续试」，实测有一次连续 19 次 Bash 全在换着法子探上级目录，
        # 烧到 167k token 仍未交付。所以这里必须同时给出「停止」和「替代动作」。
        return (
            "⛔ 已拒绝：工作目录之外不可访问。\n"
            "不要再用 `..`、`ls ..`、`/`、`find /` 或任何绝对/上级路径重试——"
            "那里没有你要找的信息，重复尝试只会耗尽轮次。\n"
            "请只基于当前工作目录内的文件做判断。如果是缺少某个阈值、约定或规格，"
            "说明它不在仓库里：应当结合任务描述与既有代码给出可辩护的选择，"
            "并在交付说明里写清你的取舍依据。"
        )

    # POSIX 优先：Windows 上若有 bash（如 Git Bash），用它跑，命令语义与模型训练一致
    bash = shutil.which("bash") or shutil.which("sh")
    if bash and os.name == "nt":
        proc = subprocess.run(
            [bash, "-c", command],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    else:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            shell=True,
            timeout=timeout,
        )
    out = (proc.stdout or "") + (proc.stderr or "")
    status = "✅" if proc.returncode == 0 else f"❌(exit={proc.returncode})"
    return f"{status}\n{out}"


# ---------------------------------------------------------------------------
# 工具分发：name + 已校验 args -> observation 字符串
# ---------------------------------------------------------------------------

def dispatch(name: str, args: dict, workdir: Path, max_obs: int = 2000) -> str:
    """执行一个工具，返回给模型的 observation（已截断）。"""
    try:
        if name == "ReadFile":
            p = _safe_path(workdir, args["path"])
            if not p.is_file():
                return f"❌ 文件不存在: {args['path']}"
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            if args.get("limit"):
                lines = lines[: int(args["limit"])]
            body = "\n".join(f"{i+1:>4}│{ln}" for i, ln in enumerate(lines))
            return f"# 文件 {args['path']}（共 {len(text.splitlines())} 行）\n{body}"

        if name == "WriteFile":
            p = _safe_path(workdir, args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8")
            return f"✅ 已写入 {args['path']}（{len(args['content'])} 字符）"

        if name == "EditFile":
            p = _safe_path(workdir, args["path"])
            if not p.is_file():
                return f"❌ 文件不存在: {args['path']}"
            old = p.read_text(encoding="utf-8", errors="replace")
            new = old.replace(args["old_string"], args["new_string"])
            cnt = old.count(args["old_string"])
            if cnt == 0:
                return "❌ old_string 在文件中未找到，请重新 ReadFile 确认内容"
            if cnt > 1:
                return f"❌ old_string 出现 {cnt} 次，不唯一，请提供更多上下文"
            p.write_text(new, encoding="utf-8")
            return f"✅ 已替换 1 处（{args['path']}）"

        if name == "Bash":
            return _run_bash(args["command"], workdir, int(args.get("timeout", 30)))

        if name == "Glob":
            matches = [str(m.relative_to(workdir)) for m in workdir.glob(args["pattern"])]
            matches.sort()
            limit = int(args.get("limit", 50))
            if not matches:
                return "（无匹配文件）"
            shown = matches[:limit]
            more = "" if len(matches) <= limit else f"\n…还有 {len(matches)-limit} 个未显示"
            return "匹配:\n" + "\n".join(shown) + more

        if name == "Grep":
            start = _safe_path(workdir, args.get("path", "."))
            flags = re.IGNORECASE if args.get("ignore_case") else 0
            try:
                rx = re.compile(args["pattern"], flags)
            except re.error as e:
                return f"❌ 正则错误: {e}"
            limit = int(args.get("limit", 30))
            hits: list[str] = []
            for path in (start.rglob("*") if start.is_dir() else [start]):
                if path.is_dir() or path.suffix in (".pyc", ".png", ".pdf"):
                    continue
                try:
                    for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{path.relative_to(workdir)}:{i}:{line}")
                            if len(hits) >= limit:
                                break
                except (OSError, UnicodeDecodeError):
                    continue
                if len(hits) >= limit:
                    break
            return "命中:\n" + "\n".join(hits) if hits else "（无命中）"

        return f"❌ 未知工具: {name}"
    except PermissionError as e:
        return f"⛔ {e}"
    except subprocess.TimeoutExpired:
        return f"⏱️ 命令超时（{args.get('timeout', 30)}s）被终止"
    except Exception as e:  # 任何意外都转成 observation，让 Agent 自行反应
        return f"❌ 工具执行异常: {type(e).__name__}: {e}"


def call(name: str, args: dict, workdir: Path, max_obs: int = 2000) -> str:
    """带参数校验的工具调用入口。"""
    ok, err = _validate(name, args)
    if not ok:
        return f"❌ 参数校验失败: {err}"
    obs = dispatch(name, args, workdir, max_obs=max_obs)
    if len(obs) > max_obs:
        obs = obs[:max_obs] + f"\n…（输出过长，已截断至 {max_obs} 字符）"
    return obs
