"""改写查询集（预注册，2026-11-12）。

为什么需要它：
    原有 44 条查询上召回已经是 12/12 全对——**那个集合被用来调过参，已经饱和**，
    在它上面测不出任何改进。想验证「加入语义通道能不能提升召回」，
    必须在**没被调过参的问法**上测。

写法约束（重要，决定了结论能不能引用）：
    1. 按「一个真实工程师不知道有这个 skill 时，会怎么在任务描述里写」来写，
       不刻意绕开 tag，也不刻意塞 tag —— 结果落在哪就是哪。
    2. **先写、先跑 baseline，再改代码**。写完之后不许因为结果不好看而修改条目；
       要加只能新增条目并整体重跑（并在 commit message 里注明）。
    3. 每条标注 intended skill；若一条同时合理指向两篇（如「重试」同时属于
       retry-budget 与 rate-limit），标注 preferred，并对「召回到次优」单独统计——
       **命中率之外还要看首位精度**，否则「召回了一堆」也能拿高分。
"""

# (查询, 期望 skill, preferred：允许出现的次优 skill)
PARAPHRASES: list[tuple[str, str, tuple[str, ...]]] = [
    # rates-api：外部汇率服务
    ("调第三方汇率接口老是被对方限速，想先加个本地缓存，失败再重发一次",
     "rates-api", ()),
    ("对方返回的时间字段到底是秒还是毫秒？我们算出来的年份差了好几十年",
     "rates-api", ()),

    # messaging-contract：消息队列
    ("消费者老是重复处理同一条订单，是不是确认得太早了",
     "messaging-contract", ()),
    ("队列里堆了几十万条没人处理，机器却是闲着的，这该从哪查",
     "messaging-contract", ()),

    # logging-standard：日志与可观测性
    ("线上报错了，但翻遍日志找不到是哪个请求出的问题",
     "logging-standard", ()),
    ("日志里把用户手机号打出来了，安全那边要求我们改掉",
     "logging-standard", ()),

    # retry-budget：重试预算
    ("批量任务跑一半挂了，重跑会重复扣款，这个该退避多久",
     "retry-budget", ("rate-limit-policy",)),
    ("对方偶尔 503，我写了三次重试，结果有时候越重越糟",
     "retry-budget", ("rate-limit-policy",)),

    # report-export-encoding：导出编码
    ("财务那边用 Excel 打开我们导出的表，中文全是乱码",
     "report-export-encoding", ()),
    ("客户说下载的文件打开以后，一整行全挤在第一列里",
     "report-export-encoding", ()),

    # sla-timeout-policy：超时与降级
    ("支付接口必须 200 毫秒内返回，慢的请求该怎么处理",
     "sla-timeout-policy", ()),
    ("高峰期网关把请求全掐了，能不能按重要性分级处理",
     "sla-timeout-policy", ("rate-limit-policy",)),

    # canary-gate-policy：灰度放量
    ("新版本先给 5% 的流量，指标变差就自动退回去",
     "canary-gate-policy", ()),
    ("灰度期间错误率涨了 0.3 个点，这算不算该停下来了",
     "canary-gate-policy", ()),

    # tracking-event-policy：埋点校验
    ("前端上报的点击数据和后台对不上，差了三成",
     "tracking-event-policy", ()),
    ("同一个按钮点两次会传两条记录，运营说数据虚高",
     "tracking-event-policy", ()),

    # rate-limit-policy：限流与配额
    ("同一个客户疯狂调我们接口，把别人的资源全挤没了",
     "rate-limit-policy", ()),
    ("内部服务互相调用，A 一挂 B 就狂重试，最后把 A 彻底打死",
     "rate-limit-policy", ("retry-budget",)),

    # backfill-approval-policy：回刷与补数审批
    ("历史数据算错了，要重算三个月，这事得先找谁签字",
     "backfill-approval-policy", ()),
    ("补数据的申请，什么情况能直接跑、什么情况要等人批",
     "backfill-approval-policy", ()),

    # export-approval-policy：导出审批与脱敏
    ("运营要下载全量用户表，里面有身份证号，能不能直接给",
     "export-approval-policy", ()),
    ("同一个手机号，内部看的要完整、对外要打码，这怎么判",
     "export-approval-policy", ()),
]


# 短负例：**都不该召回任何 skill**。
#
# 由来（2026-11-12）：试过把 min_abs 换成按查询长度缩放，改写集召回从 0/22 涨到
# 7–10/22 且"零误召"——但那是假象：原有 32 条负例全是长任务描述，短查询天然离阈值远。
# 补上这批短负例后立刻穿帮：`把日志文件按日期归档` 会召回 logging-standard，
# `把导出的图改个颜色` 会同时召回两篇。
#
# 结论：**用"查询长度"当"相关性"的代理，在别处一定翻车**。
# 凡是再动阈值/打分形式，都必须先跑这组，否则只看长负例会得到虚假的安全感。
SHORT_NEGATIVES: list[str] = [
    "把日志文件按日期归档",
    "给脚本加一个超时保护",
    "把导出的图改个颜色",
    "统计每天的调用次数",
    "写个 hello world 并运行",
    "把 fizzbuzz 写成函数",
    "修复 calculate 里的除零错误",
    "读 config.json 并打印内容",
    "给 User 类加一个 __repr__",
]
