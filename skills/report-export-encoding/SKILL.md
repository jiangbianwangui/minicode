---
name: report-export-encoding
description: 交给业务方用 Excel 打开的 CSV 报表必须遵守的编码与换行约定（UTF-8 BOM、CRLF、数值格式），仓库里查不到
when_to_use: 导出 CSV/报表给非技术用户、Excel 打开中文乱码、整张表挤成一行、导出文件被打回
tags: [csv, 导出, 编码, bom, 换行, excel, 报表]
---

# 交付给业务方的 CSV 报表约定

技术侧读得通的 CSV 和业务侧读得通的 CSV **不是同一种文件**。
以下三条是交付要求，违反任何一条都不会报错——只有业务方打不开时才会被打回。

## 1. 必须带 UTF-8 BOM（`utf-8-sig`）

| 写法 | Excel 双击打开 |
| --- | --- |
| `encoding="utf-8"` | ❌ 中文全变"锟斤拷" |
| `encoding="utf-8-sig"` | ✅ 正常 |

Windows 版 Excel 在没有 BOM 时按 **GBK/ANSI** 解码，跟"UTF-8 是最通用的编码"这条常识相反。
Linux、`pandas.read_csv`、`pytest` 都读得对，所以**本地永远测不出来**。

## 2. 行分隔符必须是 CRLF（`\r\n`）

老版本 Excel 只把 **CRLF** 当行分隔：

- 用 `\n` → 整张表**挤成一行**（所有字段用逗号连着）
- 用 `\r\n` → 正常

`csv.writer` 要显式 `lineterminator="\r\n"`；`csv.DictWriter` 同理。
（Python 的 `newline=""` 只是"不要重复转换"，不等于会写 CRLF。）

## 3. 数值不要做本地化

金额写成 `302.75`，**不要**写成 `1,234.56`（千分位逗号会被 Excel 拆成两列）
或 `¥302.75`（带符号的会变成文本，无法求和）。

## 排查口诀

- 中文乱码 → 查 BOM
- 整张表挤成一行 → 查换行符
- 能看但不能求和 → 查数值有没有被本地化或加单位
