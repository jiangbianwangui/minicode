"""t25：超大规模单文件 —— 测试"上下文被撑爆"的场景

难度来源（与 t01–t24 的本质区别）：
1) **单文件约 5,960 行 / 179k 字符 / 44.5k token**（tiktoken cl100k 实测），
   整读一次就吃掉大半上下文预算；分段读又容易丢全局。
2) **要改 12 处**（6 个 __init__ + 6 个 _normalize），分散在文件不同位置，
   靠"读一次改一次"会反复重读。
3) **有 6 处同名但不同义的函数**（`_normalize` 在 6 个类里各有一份），
   必须精确区分作用域，不能全局替换。

这个任务专门用来检验：**分层上下文压缩**在长轨迹 + 大文件下是否真能省钱。

⚠️ 测量口径修正（2026-10-21）：此前 docstring/prompt 写的是「2,400 行」，
那是 `_gen_class` 注释里的估算值（6 类 × ~400 行），与 `_gen_source()` 的实际产物不符。
现已按实测时间统一为真实规模，并**从 prompt 里删掉「文件较大、可分块读」的提示**——
那句话等于提前告诉 agent 该用什么策略（泄题），会让「能否自主发现规模超限」这项能力测不出来。
"""

import re
import subprocess
import sys

_CLASSES = [
    ("UserRepo", "user"),
    ("OrderRepo", "order"),
    ("ProductRepo", "product"),
    ("InvoiceRepo", "invoice"),
    ("ShipmentRepo", "shipment"),
    ("PaymentRepo", "payment"),
]

# 每个类里放 28 组方法（每组 4 个方法 ≈ 32 行）≈ 每个类 990 行，6 个类合计约 5,960 行。
# 注：早期注释写「~380 行/类、合计 2400 行」是估算失误，与实际产物不符，已按实测更正。


def _gen_class(name: str, entity: str, idx: int, n_groups: int = 28) -> str:
    lines = [
        f"class {name}:",
        f'    """{entity} 仓库（遗留实现）。"""',
        "",
        "    def __init__(self, conn):",
        "        self.conn = conn",
        "        self._cache = {}",
        "",
        "    def _normalize(self, row):",
        "        # 注意：每个类都有同名的 _normalize，但语义不同",
        f'        return dict(row, entity="{entity}_{idx}")',
        "",
    ]
    # 大量样板方法，撑大文件（n_groups 是难度旋钮：每组约 32 行）
    for i in range(n_groups):
        lines += [
            f"    def fetch_{entity}_{i}(self, key):",
            f'        """获取 {entity} 记录 #{i}。"""',
            "        if key in self._cache:",
            "            return self._cache[key]",
            "        cur = self.conn.execute(",
            f'            "SELECT * FROM {entity} WHERE id = ?", (key,)',
            "        )",
            "        row = cur.fetchone()",
            "        if row is None:",
            "            return None",
            "        out = self._normalize(row)",
            "        self._cache[key] = out",
            "        return out",
            "",
            f"    def list_{entity}_{i}(self, limit=100):",
            f'        """列出 {entity} 记录 #{i}。"""',
            "        cur = self.conn.execute(",
            f'            "SELECT * FROM {entity} ORDER BY id LIMIT ?", (limit,)',
            "        )",
            f"        return [self._normalize(r) for r in cur.fetchall()]",
            "",
            f"    def count_{entity}_{i}(self):",
            f'        """统计 {entity} 记录数 #{i}。"""',
            "        cur = self.conn.execute(",
            f'            "SELECT COUNT(*) FROM {entity}"',
            "        )",
            "        return cur.fetchone()[0]",
            "",
            f"    def delete_{entity}_{i}(self, key):",
            f'        """删除 {entity} 记录 #{i}。"""',
            f'        print("deleting {entity} {i}", key)',
            "        self._cache.pop(key, None)",
            '        self.conn.execute("DELETE FROM '
            f'{entity} WHERE id = ?", (key,))',
            "        return True",
            "",
        ]
    return "\n".join(lines) + "\n"


def _gen_source(classes: list[tuple[str, str]] | None = None,
                n_groups: int = 28) -> str:
    """生成遗留数据访问层源码。

    Args:
        classes: [(类名, 实体名)]，默认用模块级 `_CLASSES`（6 个类）。
        n_groups: 每个类里放多少组样板方法（约 32 行/组）。**这是难度旋钮**，
            由 `eval/sweep.py` 在难度扫描时调节；默认值 28 保持 t25 原样。

    可参数化的原因：难度扫描需要「同一个任务、连续变化的规模」，
    否则就是每次重新造题、难度不可比。
    """
    cls = classes if classes is not None else _CLASSES
    parts = [
        '"""遗留数据访问层：单文件多仓库实现。"""',
        "",
        "",
        "class _Base:",
        '    """所有仓库的基类。"""',
        "",
        "    def __init__(self, conn):",
        "        self.conn = conn",
        "",
    ]
    for idx, (name, entity) in enumerate(cls):
        parts.append(_gen_class(name, entity, idx, n_groups=n_groups))
    return "\n".join(parts)


def _setup(wd):
    (wd / "repos.py").write_text(_gen_source(), encoding="utf-8")


_EXPECTED_MARK = "    # AUDIT-ADDED: 已审计"


def _verify(wd):
    f = wd / "repos.py"
    if not f.exists():
        return False, "缺少 repos.py"
    src = f.read_text(encoding="utf-8")

    lines = src.splitlines()
    if len(lines) < 2000:
        return False, f"文件行数异常（{len(lines)}），疑似被重写"

    # 1) 每个类的 _normalize 里都必须有审计标记（6 处）
    n_mark = src.count(_EXPECTED_MARK.strip())
    if n_mark != len(_CLASSES):
        return False, f"审计标记应 {len(_CLASSES)} 处，实际 {n_mark}"

    # 2) 每个类的 __init__ 里必须初始化 self._audit = []
    n_init = src.count("self._audit = []")
    if n_init != len(_CLASSES):
        return False, f"self._audit = [] 应 {len(_CLASSES)} 处，实际 {n_init}"

    # 3) 不能把类结构改坏：每个类仍存在且 _normalize 仍在
    for name, _ in _CLASSES:
        if f"class {name}:" not in src:
            return False, f"类 {name} 丢失"
    n_norm = src.count("def _normalize(self, row):")
    if n_norm != len(_CLASSES):
        return False, f"_normalize 应 {len(_CLASSES)} 个，实际 {n_norm}"

    # 4) 语法必须仍然正确，且能 import
    p = subprocess.run(
        [sys.executable, "-c", "import repos; print('OK')"],
        cwd=str(wd), capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0 or "OK" not in p.stdout:
        return False, f"import 失败: {p.stderr.strip()[-200:]!r}"

    # 5) 6 个类都要能实例化（用假 conn）
    code = (
        "import repos\n"
        "class C:\n"
        "    def execute(self, *a, **k):\n"
        "        class R:\n"
        "            def fetchone(self): return None\n"
        "            def fetchall(self): return []\n"
        "        return R()\n"
        "c = C()\n"
        + "".join(f"assert hasattr(repos.{n}(c), '_audit')\n" for n, _ in _CLASSES)
        + "print('OK')\n"
    )
    p = subprocess.run(
        [sys.executable, "-c", code.strip()], cwd=str(wd),
        capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0 or "OK" not in p.stdout:
        return False, f"实例化/属性检查失败: {p.stderr.strip()[-200:]!r}"

    return True, f"{len(_CLASSES)} 个仓库类均已加入审计字段（文件 {len(lines)} 行未被重写）"


TASK = {
    "id": "t25",
    "name": "超长文件12处审计改造(5960行)",
    "prompt": (
        "`repos.py` 是一个遗留的数据访问层，里面有 6 个仓库类"
        "（UserRepo / OrderRepo / ProductRepo / InvoiceRepo / ShipmentRepo / PaymentRepo）。\n\n"
        "请给**这 6 个类每一个**都加一个审计能力：\n"
        "1) 在各类的 `__init__` 末尾加上 `self._audit = []`；\n"
        "2) 在各类的 `_normalize` 方法体第一行加上注释 `# AUDIT-ADDED: 已审计`。\n\n"
        "注意：**每个类都各有一个同名的 `_normalize`，但它们是不同类的不同方法**，"
        "必须分别处理，不能只改一个或全局替换。\n"
        "改完后 `import repos` 不能报错，且 6 个类都要能被实例化、都带 `_audit` 属性。\n"
        "原有代码结构不得删减或重写（只做增量修改）。"
    ),
    "setup": _setup,
    "verify": _verify,
}
