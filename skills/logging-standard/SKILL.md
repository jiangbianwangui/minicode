---
name: logging-standard
description: 本团队的日志硬性规范：必带 trace_id/tenant_id、禁止写入 PII、异常必须带堆栈（仓库里没有，组内约定）
when_to_use: 给代码加日志、补可观测性、排查定位不到请求链路、日志合规检查
tags: [logging, 日志, trace, trace_id, pii, 隐私, 合规, 观测]
---

# 日志规范（组内硬性约定）

这些是**团队约定**，不在代码里、不在 lint 规则里。review 会拦，但不 review 就上线了，
代价在排查和合规上——两者都不会在测试里报错。

## 1. 每条日志必带 `trace_id` + `tenant_id`

放在 `extra=` 里，不要拼进消息字符串（拼进去就没法按字段过滤了）。

```python
# ❌ 打不出来也过滤不了
logger.info(f"place_order trace={get_trace_id()} account={user['id']}")

# ✅ 结构化字段，日志平台可按 trace_id / tenant_id 检索
logger.info("place_order start account=%s", user["id"],
            extra={"trace_id": get_trace_id(), "tenant_id": user["tenant_id"]})
```

- **`trace_id`**：一次请求的链路 id。没有它，出事时只能靠时间戳猜哪几条日志是一伙的。
- **`tenant_id`**：多租户隔离。没有它，排查时会把别的客户的数据看成你的。

这两条是**每一行日志**都要有，不是"关键路径有就行"。

## 2. 禁止写入 PII

绝对不能进日志的字段：

| 字段 | 正确处理 |
| --- | --- |
| email | 用 `user_id` 代替；必须显示时掩码 `a***@example.com` |
| 手机号 | 掩码 `138****8000`，或用 `user_id` |
| 身份证 / 银行卡 | **永不写日志**，任何形式都不行 |
| 完整地址、姓名 | 用 `user_id` |

常见踩法：`logger.info(f"user={user}")` —— 把整个 dict 打出来，
里面恰好有 email 和手机号。**这是本组过去一年唯一一次合规事故的原因。**

判据：**日志里出现 `@` 或 11 位连续数字，就要停下来检查。**

## 3. 异常必须 `log.exception(...)`

```python
# ❌ 堆栈丢了，线上只看到一句 "boom"
except Exception as e:
    logger.error(str(e))

# ❌ 堆栈也丢了
except Exception as e:
    logger.error(f"failed: {e}")

# ✅ 自动带 traceback
except Exception:
    logger.exception("place_order failed account=%s", user["id"], extra=ctx)
    raise
```

`log.error` 只记你给它的字符串；`log.exception` 会自动附上当前异常的 traceback。
**"在哪炸的"这个信息只有 traceback 里有。**

## 4. 级别怎么选

| 级别 | 用在哪 |
| --- | --- |
| `debug` | 循环内部、每次调用的细节（线上默认关） |
| `info` | 一个业务动作的开始/结束（下单、导出、同步），**带关键 id，不带明细** |
| `warning` | 可恢复的异常情况（重试了、降级了） |
| `error` | 需要人介入的业务失败 |
| `exception` | **在 except 块里**记录异常（= error + traceback） |

## 5. 日志不能替代异常

记了 `logger.exception` 之后**仍然要 `raise`**（或明确返回失败），
否则调用方以为成功了。日志是给排查用的，不是给控制流用的。
