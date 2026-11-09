"""Skill 路由的单测：检索准不准、噪声有没有、正文会不会混进 prompt。

重点保护两条踩过坑的性质：
1. **不索引正文** —— 第一版把 body 也放进索引，26 个任务全线召回（连 hello world
   都匹配到"重构安全检查"），因为正文里全是"代码/文件/测试"这类通用词。
2. **宁缺毋滥** —— 没命中任何 tag 就不给，不用"总是返回 top_k"填空。
"""

import sys
import pathlib
import tempfile

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from skill_router import (  # noqa: E402
    Skill,
    SkillRouter,
    load_skills,
    summarize_hits,
    tokenize,
    _parse_frontmatter,
)


def _mk(name, desc, when, tags, body="正文内容"):
    return Skill(
        name=name, description=desc, when_to_use=when, tags=tags,
        body=body, path=pathlib.Path(f"/tmp/{name}/SKILL.md"),
    )


# ---------------- 解析 ----------------

def test_frontmatter_parsed():
    text = (
        "---\n"
        "name: demo\n"
        "description: 演示技能\n"
        "when_to_use: 涉及 demo 时\n"
        "tags: [api, cache]\n"
        "---\n"
        "\n正文第一段\n"
    )
    meta, body = _parse_frontmatter(text)
    assert meta["name"] == "demo"
    assert meta["tags"] == ["api", "cache"]
    assert body.strip() == "正文第一段"


def test_frontmatter_absent_is_safe():
    meta, body = _parse_frontmatter("没有 frontmatter 的正文")
    assert meta == {}
    assert "正文" in body


def test_load_skips_skill_without_description():
    d = pathlib.Path(tempfile.mkdtemp(prefix="sk_"))
    (d / "good").mkdir()
    (d / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: 好用\ntags: [x]\n---\n正文\n", encoding="utf-8")
    (d / "bad").mkdir()
    (d / "bad" / "SKILL.md").write_text(
        "---\nname: bad\n---\n正文\n", encoding="utf-8")
    names = [s.name for s in load_skills(d)]
    assert names == ["good"], "缺 description 的 skill 应被跳过（注入时会隐身）"


# ---------------- 检索 ----------------

def _router():
    return SkillRouter([
        _mk("rates-api", "调用汇率 API 的限频、重试、时间戳约定",
            "涉及汇率 rates、给 HTTP 请求加缓存/重试/限频", ["api", "rates", "cache", "retry"]),
        _mk("pytest-style", "写 pytest 的团队规范", "新增或修改 tests 下的测试", ["pytest", "test", "parametrize"]),
        _mk("refactor-safety", "重构前检查清单", "重构、重命名、跨文件改动", ["refactor", "rename", "重构"]),
    ])


def test_hits_relevant_skill():
    r = _router()
    hits = r.retrieve("给汇率 API 调用加缓存和重试", min_abs=0)
    assert hits and hits[0].skill.name == "rates-api", hits


def test_no_hit_for_unrelated_task():
    """hello world 不该召回任何 skill —— 这是第一版最严重的噪声。"""
    r = _router()
    assert r.retrieve("写一个 hello world") == []


def test_body_not_indexed():
    """正文里堆满关键词，也不该因此被召回（元数据才是准入依据）。"""
    s = _mk("noise", "无关技能", "无关场景", ["zzz"],
            body="缓存 重试 汇率 api 重构 重命名 pytest 测试 " * 20)
    r = SkillRouter([s])
    assert r.retrieve("给汇率 api 加缓存和重试") == []


def test_name_exact_match_counts():
    r = SkillRouter([_mk("rates-api", "d", "w", ["zzz"])])
    hits = r.retrieve("请按 rates-api 的约定实现", min_abs=0)
    assert hits and hits[0].skill.name == "rates-api"


def test_relative_floor_trims_weak_hits():
    """一个强命中不该带出一串弱命中。"""
    r = SkillRouter([
        _mk("strong", "d", "w", ["cache", "retry", "api"]),
        _mk("weak", "d", "w", ["api"]),
    ])
    names = [h.skill.name for h in r.retrieve("加 cache 和 retry 逻辑", min_abs=0)]
    assert names == ["strong"], names


def test_get_by_name():
    r = _router()
    assert r.get("pytest-style") is not None
    assert r.get("不存在") is None


# ---------------- 注入 ----------------

def test_summary_has_description_but_not_body():
    s = _mk("rates-api", "限频与重试约定", "w", ["api"], body="TTL 必须 >= 600 秒（秘密细节）")
    text = summarize_hits(SkillRouter([s]).retrieve("给 api 加缓存", min_abs=0))
    assert "限频与重试约定" in text
    assert "600" not in text, "正文不该进 system prompt，必须让 agent 自己 LoadSkill"


def test_summary_empty_when_no_hits():
    assert summarize_hits([]) == ""


def test_tokenize_covers_cjk_bigrams():
    toks = tokenize("限频重试")
    assert "限频" in toks and "重试" in toks


def test_chinese_two_char_tag_matches():
    """中文两字 tag 必须能命中。

    2026-11-02 的 bug：匹配门槛写的是 `len(tag) >= 3`（英文视角），
    导致"限流""配额""租户""网关"这类两字中文 tag **永久失效**。
    前面几个 skill 还能召回，全靠英文 tag 侥幸命中；新加的
    `rate-limit-policy` 唯一英文 tag 是带空格的 "rate limit"，
    而任务里写的是 `ratelimit/` —— 一个 tag 都没命中，skill 模式 0/3。
    """
    s = _mk("rate-limit-policy", "网关限流配额", "写接口限流", tags=["限流", "配额"])
    r = SkillRouter([s])
    hits = r.retrieve("接口网关的限流模块，一分钟给多少配额", min_abs=0)
    assert [h.skill.name for h in hits] == ["rate-limit-policy"]


def test_english_two_letter_tag_still_blocked():
    """英文两字母 tag 仍然不许命中 —— 修 bug 不能把原来防的东西放进来。"""
    s = _mk("noise", "无关技能", "别用", tags=["re", "id", "to"])
    r = SkillRouter([s])
    assert r.retrieve("please re do it and id it") == []


# ---------------- 2026-11-09：通用 tag 与绝对分数下限 ----------------

def test_generic_tags_alone_do_not_trigger():
    """只有 `pytest` / `test` 命中时不该召回。

    起因：随机抽样任务的阴性对照里 5/5 都被塞了不相关文档，查明细发现
    `pytest-style` 靠这两个词命中了 **20 个任务** —— 因为几乎每个 prompt
    都写着"跑一遍 pytest 确认"。通用词不是证据。
    """
    s = _mk("pytest-style", "pytest 写法", "写测试时", tags=["pytest", "test", "fixture"])
    r = SkillRouter([s])
    assert r.retrieve("实现 split_amount，改完自己跑一遍 python -m pytest tests -q 确认") == []


def test_specific_tag_still_triggers_alongside_generic():
    """具体 tag 在，仍然要召回 —— 修 bug 不能把正常的也拦掉。"""
    s = _mk("pytest-style", "pytest 写法", "写测试时", tags=["pytest", "test", "fixture"])
    r = SkillRouter([s])
    hits = r.retrieve("用 fixture 组织参数化用例，改完跑 pytest", min_abs=0)
    assert [h.skill.name for h in hits] == ["pytest-style"]


def test_low_score_below_absolute_floor_is_dropped():
    """单个宽泛 tag 的分数（实测 2–9.6）必须被绝对下限挡住。

    真实例子：t27 会误召 `retry-budget`（9.2），而它与任务要求相矛盾
    ——t27 明确要求 429 不重试，那篇教的是"重试 3 次带退避"。
    """
    s = _mk("vague", "宽泛技能", "到处都可能用", tags=["缓存"])
    r = SkillRouter([s])
    assert r.retrieve("给 LRU 加一个缓存淘汰策略") == []


def test_absolute_floor_keeps_strong_hit():
    """真正对口的（多个具体 tag）在**真实语料**里不能被下限误伤。

    注意这里用 `min_abs=0` 之外的方式验证不了：绝对分数里 0.2×BM25 会随语料规模
    剧烈变化（迷你语料下 BM25 退化，分数只有 5–6；真实 13 篇库是真命中 13–30）。
    所以真实语料的行为放在 tests/test_recall_snapshot.py 里锁。
    """
    s = _mk("rate-limit-policy", "网关限流", "写限流器时", tags=["限流", "配额", "租户", "网关"])
    r = SkillRouter([s])
    hits = r.retrieve("实现网关限流器：按租户共享配额，60 秒窗口", min_abs=0)
    assert [h.skill.name for h in hits] == ["rate-limit-policy"]
