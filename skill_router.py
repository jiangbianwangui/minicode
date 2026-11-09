"""Skill 分层路由：把「仓库外部的知识」按需送进上下文。

## 为什么需要这个（问题定义）

Agent 读得到仓库里的代码，读不到仓库外的约定：对端 API 的限频、团队的测试规范、
历史事故留下的禁忌。这些知识有两个特征：

1. **不在代码里** —— 所以 Grep/ReadFile 怎么搜都搜不到；
2. **猜错不报错** —— 写个 60 秒 TTL 代码照样跑，本地测试全绿，只有配额打满时才炸。

缺了它，agent 只能靠"通用最佳实践"猜，而通用做法在这类场景里恰好是错的
（60 秒 TTL 在别的项目很合理，在本项目会把 60 次/小时的配额打满）。

## 检索策略：两阶段，故意不做向量

- **阶段 1 · BM25 粗召回**：纯词频统计，零 token、零依赖、可离线、可复现。
- **阶段 2 · 规则精排**：skill 名精确出现在任务里 +3，tag 命中 +1.5，when_to_use 命中 +0.8。

为什么不上向量：引入 embedding 就多一个网络依赖和一个不可解释的分数，
而且本项目只有个位数 skill，BM25 已经够。**代价**是同义改写召回差
（任务说"限流"而 skill 写"限频"就可能漏），这是明确记在 README 里的边界。

## 注入策略：只给摘要，正文按需拉取

粗召回后只把 **description** 拼进 system prompt（每条约 40 字），
agent 觉得相关再调 `LoadSkill(name)` 拿全文。

这不是为了省事，是为了让"要不要读全文"变成 agent 的决策——
把 3 篇全文（约 2000 token）无条件塞进去，等于替它做了判断，
而判断错了就是纯亏（和上下文压缩踩的是同一个坑）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ---- 分词 ----
_EN = re.compile(r"[a-z0-9_]+")
_ZH = re.compile(r"[\u4e00-\u9fff]+")


def _min_tag_len(tag: str) -> int:
    """tag 的最短匹配长度门槛。

    中文 2 字符就有区分度（"限流" / "配额" / "退避"），英文 2 字符几乎没有（"re" / "id" / "to"）。
    按字符集分开定门槛，避免"一刀切 3 字符"把两字中文 tag 全部废掉。
    """
    return 2 if any("\u4e00" <= ch <= "\u9fff" for ch in tag) else 3


def tokenize(text: str) -> list[str]:
    """英文按词、中文按 2-gram。

    不引入 jieba：一来多一个依赖，二来 skill 语料里英文技术词占主要区分度
    （pytest / retry / cache / rename），中文 2-gram 已足够把"限频""重试""调用点"捞出来。
    """
    low = text.lower()
    toks = _EN.findall(low)
    for run in _ZH.findall(low):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析极简 YAML frontmatter（只支持 key: value 与 key: [a, b]）。

    不引 PyYAML：skill 文件的 frontmatter 结构固定且简单，
    自己解析能把"格式错误"变成明确的报错，而不是静默解析成空。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k] = v.strip("'\"")
    return meta, body.lstrip("\n")


@dataclass
class Skill:
    name: str
    description: str
    when_to_use: str
    tags: list[str]
    body: str
    path: Path
    _index: Counter = field(default_factory=Counter, repr=False)

    def __post_init__(self) -> None:
        # ⚠️ 只索引**元数据**，不索引正文。
        #
        # 这是踩过坑之后改的：第一版把 body 也放进索引，结果 26 个任务里**每一个**
        # 都召回了 2–3 个 skill，连 "hello world" 都匹配到 "重构安全检查"——
        # 因为正文里全是"代码/函数/文件/测试"这类通用词，任何任务都有词重叠。
        #
        # 元数据（name/tags/when_to_use）本来就是"什么时候该用我"的答案，
        # 拿它做检索既准又省；正文只在 agent 主动 LoadSkill 时才读。
        text = " ".join([
            self.name, self.name,
            " ".join(self.tags), " ".join(self.tags),
            self.when_to_use,
            self.description,
        ])
        self._index = Counter(tokenize(text))


@dataclass
class SkillHit:
    skill: Skill
    score: float
    bm25: float
    bonus: float


def load_skills(skill_dir: str | Path) -> list[Skill]:
    """扫描 `<dir>/<name>/SKILL.md`，返回全部 skill。

    缺 name 就以目录名兜底；缺 description 直接跳过并告警——
    没有 description 的 skill 在注入阶段等于隐身，加载了也没用。
    """
    root = Path(skill_dir)
    if not root.is_dir():
        return []
    out: list[Skill] = []
    for md in sorted(root.glob("*/SKILL.md")):
        meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
        name = str(meta.get("name") or md.parent.name).strip()
        desc = str(meta.get("description") or "").strip()
        if not desc:
            print(f"⚠️  跳过 skill {name}：缺 description（注入阶段会隐身）")
            continue
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        out.append(Skill(
            name=name,
            description=desc,
            when_to_use=str(meta.get("when_to_use") or ""),
            tags=list(tags),
            body=body,
            path=md,
        ))
    return out


class SkillRouter:
    """两阶段检索：BM25 粗召回 → 规则精排。"""

    def __init__(self, skills: list[Skill], k1: float = 1.5, b: float = 0.75) -> None:
        self.skills = skills
        self.k1 = k1
        self.b = b
        self._df: Counter = Counter()
        for s in skills:
            for t in set(s._index):
                self._df[t] += 1
        self._n = len(skills)
        self._avgdl = (
            sum(sum(s._index.values()) for s in skills) / self._n if self._n else 0.0
        )

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def _bm25(self, q_terms: Counter, s: Skill) -> float:
        dl = sum(s._index.values()) or 1
        total = 0.0
        for t, qtf in q_terms.items():
            tf = s._index.get(t, 0)
            if not tf:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
            total += self._idf(t) * (tf * (self.k1 + 1)) / denom * min(qtf, 3)
        return total

    def _rule_bonus(self, task_low: str, q_tokens: set[str], s: Skill) -> float:
        """（已废弃，保留签名以兼容旧调用；打分见 _tag_score / retrieve）"""
        bonus = 0.0
        if s.name.lower() in task_low:
            bonus += 3.0
        bonus += 1.5 * sum(1 for t in s.tags if t.lower() in task_low or t.lower() in q_tokens)
        wt = set(tokenize(s.when_to_use))
        if wt:
            overlap = len(wt & q_tokens) / len(wt)
            bonus += 0.8 * overlap * 3
        return bonus

    def _tag_hits(self, task_low: str, q_tokens: set[str], s: Skill) -> list[str]:
        """精确命中 skill 声明过的 tag —— 这是**准入门槛**。

        为什么用 tag 而不是 BM25 关键词相似度：
        本项目语料只有个位数 skill，BM25 的 IDF 会失效（df≈N，所有词权重趋同），
        退化成纯词频匹配，于是"代码/文件/测试"这种通用词把一切都连接起来。
        tag 是人手写的、有区分度的触发词，在小语料下比统计相似度可靠得多。

        匹配规则：
        - 单词 tag（api / cache / pytest）：作为子串出现在任务里，长度达到门槛才算；
        - 多词 tag（rate-limit / cross-file）：整体出现，或每一段都出现在任务 token 里。

        🔴 **长度门槛按字符集区分（2026-11-02 修）**：
        原来是英文视角的 `len(t) >= 3`，结果**所有两字中文 tag 永久失效**
        （"限流""配额""租户""网关""重试" 全是 2 字符）——
        前面几个 skill 之所以还能召回，全靠它们的英文 tag（prefetch / sla / canary）恰好命中，
        是运气不是设计。新加的 `rate-limit-policy` 唯一英文 tag 是带空格的 "rate limit"，
        而任务里写的是 `ratelimit/`，于是**一个 tag 都没命中 → 完全不召回**，
        skill 模式 0/3，看起来像"skill 没用"，实际是路由器漏召。

        中文 2 个字符的区分度 ≈ 英文 2 个**词**（"限流" vs "re"），
        所以中文门槛取 2、英文仍取 3。
        """
        hits: list[str] = []
        for raw in s.tags:
            t = raw.lower().strip()
            if not t:
                continue
            if t in task_low and len(t) >= _min_tag_len(t):
                hits.append(t)
                continue
            parts = [p for p in re.split(r"[-_\s]+", t) if p]
            if len(parts) > 1 and all(
                len(p) >= _min_tag_len(p) and p in q_tokens for p in parts
            ):
                hits.append(t)
        return hits

    # 🔴 2026-11-09 加：通用 tag 不构成证据。
    #
    # 起因：随机抽样任务的阴性对照里，5/5 都被塞了不相关文档。
    # 查明细发现 `pytest-style` 靠 `pytest` + `test` 两个词命中了 **20 个任务**
    # —— 因为几乎每个任务 prompt 都写着"跑一遍 pytest 确认"。
    # 这不是"某篇 skill 写得好"，是**通用词把一切连起来**，正是 _tag_hits 注释里
    # 说 BM25 会犯的错，结果在 tag 这一层又犯了一次。
    #
    # 判据：这些词在任何工程任务里都出现，命中它等于没有信息。
    # 它们可以**陪跑**（不影响分数），但不能**单独构成一次召回**。
    GENERIC_TAGS = frozenset({
        "test", "tests", "testing", "pytest", "unittest",
        "code", "file", "function", "codebase",
        "测试", "代码", "文件", "函数", "字段", "数据",
    })

    def retrieve(
        self,
        task: str,
        top_k: int = 3,
        min_score: float = 1.0,
        rel: float = 0.35,
        min_abs: float = 10.0,
    ) -> list[SkillHit]:
        """返回命中的 skill，按分数降序。

        打分 = tag 命中权重 + name 精确命中 + 0.2×BM25（仅用于同分排序）。

        **硬闸门**：没有命中任何 tag、name 也没出现 → 直接不给。
        宁可漏召也不乱召：乱召会把无关 skill 写进 prompt，还可能诱导 agent
        多花一轮 LoadSkill 去读一篇用不上的文档——那是净亏，而漏召只是退回
        agent 自己的通用知识（等于没这个功能，不亏）。

        🔴 **两道 2026-11-09 补的闸门**（原来只有 `rel` 相对过滤，靠"有更高分项压着"
        才偶然挡住低分项；没有对应 skill 的任务会被硬塞 2–9 分的噪声）：

        1. `GENERIC_TAGS` 不能单独触发（见类常量注释）；
        2. `min_abs=10.0` 绝对分数下限。定这个值的依据是实测分数分布：
           噪声 2.0–9.6（t27 的 retry-budget 9.2、t38 的 backfill 9.6 是已知误召，
           前者还与任务要求**相矛盾**：t27 说 429 不重试，它教重试 3 次），
           真命中 13.3–29.8，唯一落在中间的是 t39（10.4，只靠一个"审批" tag）。
           **10.0 正好卡在 9.6 和 10.4 之间，两边各留 0.4 分** —— 余量非常薄，
           而且是在我自己写的 21 个 prompt 上调出来的，换任务分布未必成立。
           ⚠️ 这条同时说明：**分数本身分不清"结构相似"和"词撞上了"**，
           t39 的 10.4 是真有用（同构），t38 的 9.6 也同构 —— 靠 0.8 分的差距区分它们，
           属于运气，不是设计。要彻底解决得换判据（如 tag 语义分层），不是调阈值。
        """
        if not self.skills:
            return []
        task_low = task.lower()
        q_tokens = set(tokenize(task))
        hits: list[SkillHit] = []
        for s in self.skills:
            tag_hits = self._tag_hits(task_low, q_tokens, s)
            name_hit = len(s.name) >= 3 and s.name.lower() in task_low
            # 通用词不算证据：只有它命中的话，等于没命中
            specific = [t for t in tag_hits if t not in self.GENERIC_TAGS]
            if not specific and not name_hit:
                continue
            # 长的 tag 更具体（"限频"比 "api" 信息量大），轻微加权
            tag_score = sum(1.0 + 0.15 * len(t) for t in specific)
            bonus = tag_score + (3.0 if name_hit else 0.0)
            bm = self._bm25(Counter(tokenize(task)), s)
            score = bonus + 0.2 * bm
            if score >= min_score and score >= min_abs:
                hits.append(SkillHit(skill=s, score=score, bm25=bm, bonus=bonus))
        hits.sort(key=lambda h: (-h.score, h.skill.name))
        if not hits:
            return []
        floor = hits[0].score * rel
        return [h for h in hits if h.score >= floor][:top_k]

    def get(self, name: str) -> Skill | None:
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def catalog(self) -> str:
        """全部 skill 的一行摘要（未命中时用来告诉 agent「你还能查什么」）。"""
        return "\n".join(f"- {s.name}：{s.description}" for s in self.skills)


def summarize_hits(hits: list[SkillHit]) -> str:
    """把命中的 skill 拼成注入 system prompt 的短文本。

    只给 name + description，**不给正文**——正文要 agent 主动 LoadSkill 拉。
    """
    if not hits:
        return ""
    lines = ["\n\n## 相关技能（外部知识，仓库里没有）"]
    for h in hits:
        lines.append(f"- `{h.skill.name}`：{h.skill.description}")
    lines.append(
        "以上只是摘要。若任务涉及它们的细节（限频/重试/测试规范/重构检查），"
        "请先调用 LoadSkill(name) 读全文再动手——这些约定不在代码里，猜错不会报错。"
    )
    return "\n".join(lines)


def default_router() -> SkillRouter:
    """从仓库根的 skills/ 目录加载（不在沙箱 workdir 内，agent 只能通过工具访问）。"""
    root = Path(__file__).resolve().parent / "skills"
    return SkillRouter(load_skills(root))
