#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import libs.common as common
import libs.buy_point as buy_point
import datetime
import os
import traceback

# 建表 SQL（简化版本）
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `hsc_style_report` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `date` VARCHAR(8) NOT NULL COMMENT '日期YYYYMMDD',
    `code` VARCHAR(10) NOT NULL COMMENT '股票代码',
    `name` VARCHAR(50) NOT NULL COMMENT '股票名称',
    `price` DECIMAL(10,2) COMMENT '最新价',
    `quote_change` DECIMAL(10,2) COMMENT '涨跌幅',
    `score` INT COMMENT '综合评分',
    `report_content` TEXT COMMENT 'Markdown报告内容',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_date_code` (`date`, `code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 日报表 SQL（汇总报告）
CREATE_SUMMARY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `hsc_style_summary` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `date` VARCHAR(8) NOT NULL COMMENT '日期YYYYMMDD',
    `report_content` LONGTEXT NOT NULL COMMENT 'Markdown报告内容',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_date` (`date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

REPORT_DIR = "/data/reports/"
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_eastmoney_link(code, name):
    """
    生成东方财富行情页链接
    6开头 → sh（上海），0或3开头 → sz（深圳）
    """
    code = str(code)
    if code.startswith('6'):
        prefix = 'sh'
    else:
        prefix = 'sz'
    return f"[{name}](https://quote.eastmoney.com/{prefix}{code}.html)"


def ensure_table():
    """确保报告表存在"""
    try:
        common.insert(CREATE_TABLE_SQL)
        common.insert(CREATE_SUMMARY_TABLE_SQL)
    except Exception as e:
        print("建表异常（可能已存在）:", e)


def is_weekend(tmp_datetime):
    """判断是否周末"""
    return tmp_datetime.weekday() >= 5


def generate_report(tmp_datetime, hsc_hits):
    """生成华盛昌风格选股报告"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    date_int = tmp_datetime.strftime("%Y%m%d")

    lines = []
    lines.append("# 华盛昌风格选股 · %s（%s）\n" % (date_str, weekday))
    lines.append("> 选股条件：深度回调后企稳反弹，借鉴华盛昌(002980)走势特征\n")
    lines.append("\n")

    if is_weekend(tmp_datetime):
        lines.append("**休市提示**：今日%s，A股休市。\n" % weekday)
        return "\n".join(lines), []

    # 检查是否有数据
    if not hsc_hits:
        lines.append("**今日无符合条件的股票**\n")
        lines.append("\n")
        lines.append("选股逻辑说明：\n")
        lines.append("1. **深度回调**：从20日高点回调8-20%\n")
        lines.append("2. **企稳反弹**：5日涨幅2-15%，开始回升\n")
        lines.append("3. **量能配合**：量能萎缩或稳定，无大幅放量\n")
        lines.append("4. **位置适中**：60日位置30-70%，不追高\n")
        lines.append("5. **均线多头**：MA20 > MA60，中期趋势向好\n")
        lines.append("6. **大跌企稳**：10日内有大跌且近期不再创新低\n")
        return "\n".join(lines), []

    # 有符合条件的股票
    lines.append("## 今日推荐\n")
    lines.append("共选出 **%d** 只股票，展示TOP5:\n" % len(hsc_hits))
    lines.append("\n")

    saved_records = []
    for i, hit in enumerate(hsc_hits[:5], 1):
        lines.append("### %d. %s（%s）\n" % (i, get_eastmoney_link(hit["code"], hit["name"]), hit["code"]))
        lines.append("- 现价：**%.2f元**（%+.2f%%）\n" % (hit["price"], hit["quote_change"]))
        lines.append("- 综合评分：**%d分**\n" % hit["score"])
        lines.append("- 趋势表现：5日%.1f%% / 10日%.1f%% / 20日%.1f%%\n" % (
            hit["trend_5"], hit["trend_10"], hit["trend_20"]
        ))
        lines.append("- 回调深度：**%.1f%%**（从20日高点）\n" % hit["pullback_depth"])
        lines.append("- 量能比：%.2f  / 60日位置：%.1f%%\n" % (hit["vol_ratio"], hit["position_pct"]))
        lines.append("- 均线系统：MA5 %.2f / MA10 %.2f / MA20 %.2f / MA60 %.2f\n" % (
            hit["ma5"], hit["ma10"], hit["ma20"], hit["ma60"]
        ))
        lines.append("- 技术指标：KDJ(%.1f, %.1f, %.1f) / RSI %.1f / CCI %.1f / MACDH %.2f\n" % (
            hit["kdjk"], hit["kdjd"], hit["kdjj"], hit["rsi_6"], hit["cci"], hit["macdh"]
        ))
        status_flags = []
        if hit["has_big_drop"]:
            status_flags.append("有大跌")
        if hit["stabilized"]:
            status_flags.append("已企稳")
        if hit["midterm_bullish"]:
            status_flags.append("中期多头")
        if hit["in_mainline"]:
            status_flags.append("主线板块")
        lines.append("- 状态标记：%s\n" % (" / ".join(status_flags) if status_flags else "无"))
        lines.append("\n")

        # 保存记录
        saved_records.append({
            "date": date_int,
            "code": hit["code"],
            "name": hit["name"],
            "price": hit["price"],
            "quote_change": hit["quote_change"],
            "score": hit["score"],
        })

    # 如果超过5只，列出剩余的
    if len(hsc_hits) > 5:
        lines.append("## 其他推荐（共%d只）\n" % len(hsc_hits))
        for i, hit in enumerate(hsc_hits[5:], 6):
            lines.append("- %s（%s）：%.2f元，评分%d分\n" % (
                hit["name"], hit["code"], hit["price"], hit["score"]
            ))
        lines.append("\n")

    # 选股逻辑说明
    lines.append("## 选股逻辑说明\n")
    lines.append("### 华盛昌(002980)走势特征\n")
    lines.append("- 股价经历了较深回调（约12%）\n")
    lines.append("- 10日内有过大跌（单日跌幅超8%）\n")
    lines.append("- 近期开始企稳反弹（5日涨幅约5%）\n")
    lines.append("- 量能有所萎缩（量能比0.71）\n")
    lines.append("- 60日位置适中（约52%）\n")
    lines.append("- 中期均线呈多头排列（MA20 > MA60）\n")
    lines.append("\n")

    lines.append("### 评分规则\n")
    lines.append("- **回调深度（35分）**：回调8-20%最佳，5-25%次之\n")
    lines.append("- **近期反弹（20分）**：5日涨幅2-15%最佳，正收益次之\n")
    lines.append("- **量能配合（15分）**：量能比0.5-0.85最佳，0.4-1.0次之\n")
    lines.append("- **位置适中（15分）**：60日位置30-70%最佳，20-85%次之\n")
    lines.append("- **中期多头（15分）**：MA20 > MA60\n")
    lines.append("- **大跌企稳（10分）**：有大跌且已企稳额外加分\n")
    lines.append("- **主线板块（10分）**：属于主线额外加分\n")
    lines.append("- **反弹确认（5分）**：近期确认反弹\n")
    lines.append("\n")

    lines.append("### 操作建议\n")
    lines.append("1. **大盘低开时**：观察这些股票的开盘承接情况\n")
    lines.append("2. **入场时机**：如果开盘不继续大跌且有买盘承接，可以考虑\n")
    lines.append("3. **止损设置**：建议设在MA20或近期低点下方\n")
    lines.append("4. **主线优先**：优先选择属于主线板块的股票\n")
    lines.append("\n")

    lines.append("---\n")
    lines.append("\n")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。\n")
    lines.append("> 股市有风险，投资需谨慎。\n")

    return "\n".join(lines), saved_records


def save_report(tmp_datetime, report_content, hsc_hits):
    """保存报告到MySQL和文件"""
    date_int = tmp_datetime.strftime("%Y%m%d")
    try:
        # 保存汇总报告
        del_sql = "DELETE FROM `hsc_style_summary` WHERE `date` = '%s'" % date_int
        common.insert(del_sql)
        ins_sql = "INSERT INTO `hsc_style_summary` (`date`, `report_content`) VALUES (%s, %s)"
        common.insert(ins_sql, (date_int, report_content))

        # 保存明细数据（简化版本，先保存关键信息）
        for i, hit in enumerate(hsc_hits):
            del_detail_sql = "DELETE FROM `hsc_style_report` WHERE `date` = %s AND `code` = %s"
            common.insert(del_detail_sql, (date_int, hit["code"]))
            ins_detail_sql = """
                INSERT INTO `hsc_style_report`
                (`date`, `code`, `name`, `price`, `quote_change`, `score`, `report_content`)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            common.insert(ins_detail_sql, (
                date_int, hit["code"], hit["name"], hit["price"], hit["quote_change"],
                hit["score"], report_content
            ))

        print("[保存] MySQL写入成功")
    except Exception as e:
        print("[保存] MySQL写入异常:", e)
        traceback.print_exc()

    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        file_path = os.path.join(REPORT_DIR, "hsc_style_%s.md" % date_int)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        print("[保存] 文件写入成功:", file_path)
    except Exception as e:
        print("[保存] 文件写入异常:", e)
        traceback.print_exc()


def stat_all(tmp_datetime):
    """主入口函数"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    date_int = tmp_datetime.strftime("%Y%m%d")
    print("=" * 60)
    print("华盛昌风格选股 - %s" % date_str)
    print("=" * 60)

    # 确保表存在
    ensure_table()

    if is_weekend(tmp_datetime):
        report_content, _ = generate_report(tmp_datetime, [])
        save_report(tmp_datetime, report_content, [])
        print("周末/节假日，跳过数据分析")
        return

    # 检查是否有前一交易日数据
    check_sql = "SELECT COUNT(1) FROM stock_zh_ah_name WHERE `date` = '%s'" % date_int
    count = common.select_count(check_sql)
    if count == 0:
        report_content = "# 华盛昌风格选股 - %s\n\n> 今日无交易数据（可能为节假日），请确认。\n" % (
            tmp_datetime.strftime("%Y年%m月%d日")
        )
        save_report(tmp_datetime, report_content, [])
        print("当日无交易数据，跳过分析")
        return

    # 执行华盛昌风格选股
    print("\n[华盛昌风格] 开始选股...")
    hsc_hits = buy_point.select_hsc_style(tmp_datetime)

    # 生成报告
    print("\n[华盛昌风格] 生成报告...")
    report_content, _ = generate_report(tmp_datetime, hsc_hits)

    # 保存报告
    save_report(tmp_datetime, report_content, hsc_hits)

    print("\n[完成] 华盛昌风格选股报告生成成功，推荐 %d 只股票" % len(hsc_hits))


# 初始化建表
ensure_table()


if __name__ == '__main__':
    common.run_with_args(stat_all)
