---
name: pytest-style
description: 本仓库写 pytest 的团队规范：使用 parametrize、禁止循环断言、fixture 命名与异常断言写法
when_to_use: 新增或修改 tests/ 下的测试、写 pytest 用例、给函数补单测
tags: [pytest, test, testing, parametrize, fixture, unittest]
---

# 本仓库的 pytest 规范

这些是**团队约定**，不在代码里，也不在 pyproject 里。新增测试必须遵守，否则 review 会被打回。

## 1. 多组输入一律用 `parametrize`，禁止循环断言

```python
# ❌ 打回：失败时只报一行 assert，不知道是哪个 case
def test_clamp():
    for x, want in [(5, 5), (-1, 0), (101, 100)]:
        assert clamp(x) == want

# ✅ 通过：每个 case 独立成测试 id，失败能直接定位
import pytest

@pytest.mark.parametrize("x,want", [(5, 5), (-1, 0), (101, 100)])
def test_clamp(x, want):
    assert clamp(x) == want
```

判据：**一个 `for` 里出现 `assert`，就是这个测试写错了。**

## 2. 异常用 `pytest.raises` 且必须断言消息

```python
# ❌ 只断言抛了异常，抛错原因变了也测不出来
with pytest.raises(ValueError):
    parse("-")

# ✅ 断言异常类型 + 消息片段
with pytest.raises(ValueError, match="negative"):
    parse("-")
```

## 3. fixture 命名固定

- `tmp_path` / `tmp_path_factory`：pytest 内置，**不要自己造临时目录**（自己造的不会清理）。
- 自定义 fixture 用名词，不要用动词：`db` / `sample_rows`，不要 `setup_db` / `do_prepare`。
- fixture 有副作用必须在 docstring 写明。

## 4. 测试名 = 行为描述，不是函数名复读

- ❌ `test_add` / `test_add_2`
- ✅ `test_add_returns_sum_for_positive_ints` / `test_add_rejects_bool_input`

命名模板：`test_<被测行为>_<条件>_<预期>`。

## 5. 一个测试只断言一个行为

同一测试里断言三个不相关的行为，失败时分不清是谁坏了。
边界值（0 / 空 / None / 负数）**单独成用例**，不要塞进正常路径的用例末尾。

## 6. 禁止在测试里写 `sleep`

要控制时间就注入时钟（`now_fn` 参数或 `freezegun`），不要 `time.sleep(1)`——
它会让整个测试套件越来越慢，最终没人愿意跑。
