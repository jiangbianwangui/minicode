"""LLM 环境配置加载（无外部兄弟目录依赖）。

历史：原来用 `agent-prototypes/shared/llm_client.py`，那是多个原型共用的模块。
为了让本仓库**独立 clone 下来就能跑**，这里只保留 env 加载这一小块，
不再去翻 SQLPilot 或父目录。

环境文件查找顺序：
    1) 环境变量 `MINICODE_ENV_FILE` 显式指定
    2) 本文件所在目录的 `.env`（推荐：`cp .env.example .env`）
    3) 当前工作目录的 `.env`

需要的变量：
    LLM_API_KEY    必需
    LLM_BASE_URL   默认 https://api.deepseek.com
    LLM_MODEL_ID   默认 deepseek-chat
"""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def candidate_env_files() -> list[Path]:
    """按优先级列出可能的 .env 路径。"""
    files: list[Path] = []
    explicit = os.environ.get("MINICODE_ENV_FILE", "").strip()
    if explicit:
        files.append(Path(explicit).expanduser())
    files += [_HERE / ".env", Path.cwd() / ".env"]
    return files


def load_env() -> Path | None:
    """加载第一个存在的 .env，返回其路径（找不到返回 None）。

    已存在的 os.environ 优先，不会被 .env 覆盖；用 python-dotenv 解析，
    缺失时退化为手写极简解析（KEY=VALUE，忽略 # 注释与空行）——
    少装一个包也能跑。
    """
    path = next((p for p in candidate_env_files() if p.is_file()), None)
    if path is None:
        return None
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(path, override=False)
    except ImportError:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    return path
