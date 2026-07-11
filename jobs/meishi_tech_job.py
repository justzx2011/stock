#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import libs.common as common
import libs.buy_point as buy_point
import datetime
import os
import traceback

# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `meishi_tech_report` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `date` VARCHAR(8) NOT NULL COMMENT '日期YYYYMMDD',
    `code` VARCHAR(10) NOT NULL COMMENT '股票代码',
    `name` VARCHAR(50) NOT NULL COMMENT '股票名称',
    `price` DECIMAL(10,2) COMMENT '最新价',
    `quote_change` DECIMAL(10,2) COMMENT '涨跌幅',
    `kdjk` DECIMAL(10,2) COMMENT 'KDJ-K',
    `kdjd` DECIMAL(10,2) COMMENT 'KDJ-D',
    `kdjj` DECIMAL(10,2) COMMENT 'KDJ-J',
    `rsi_6` DECIMAL(10,2) COMMENT 'RSI-6',
    `cci` DECIMAL(10,2) COMMENT 'CCI',
    `macdh` DECIMAL(10,2) COMMENT 'MACD柱',
    `consecutive_days` INT COMMENT '连续在池天数',
    `report_content` TEXT COMMENT 'Markdown报告内容',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_date_code` (`date`, `code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 日报表 SQL（汇总报告）
CREATE_SUMMARY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `meishi_tech_summary` (
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


def generate_report(tmp_datetime, meishi_hits):
    """生成魅视科技式选股报告"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    date_int = tmp_datetime.strftime("%Y%m%d")

    lines = []
    lines.append("# 魅视科技式选股 · %s（%s）\n" % (date_str, weekday))
    lines.append("> 选股条件：叠加半导体主线 + D≥70 确认 + 连续多日在池\n")
    lines.append("\n")

    if is_weekend(tmp_datetime):
        lines.append("**休市提示**：今日%s，A股休市。\n" % weekday)
        return "\n".join(lines), []

    # 检查是否有数据
    if not meishi_hits:
        lines.append("**今日无符合条件的股票**\n")
        lines.append("\n")
        lines.append("选股条件说明：\n")
        lines.append("1. 属于半导体主线（涨幅≥5%且名称含半导体相关关键词，或为主线强势股）\n")
        lines.append("2. KDJ-D ≥ 70\n")
        lines.append("3. 连续至少3天在策略A候选池中\n")
        lines.append("4. 满足策略A超买追涨条件（KDJ-J≥100、K≥80、D≥70、RSI6>70、MACD柱>0）\n")
        return "\n".join(lines), []

    # 有符合条件的股票
    lines.append("## 今日推荐\n")
    lines.append("共选出 **%d** 只股票：\n" % len(meishi_hits))
    lines.append("\n")

    saved_records = []
    for i, hit in enumerate(meishi_hits, 1):
        lines.append("### %d. %s（%s）\n" % (i, get_eastmoney_link(hit["code"], hit["name"]), hit["code"]))
        lines.append("- 现价：**%.2f元**（%+.2f%%）\n" % (hit["price"], hit["quote_change"]))
        lines.append("- KDJ：K=%.2f / D=%.2f / J=%.2f\n" % (hit["kdjk"], hit["kdjd"], hit["kdjj"]))
        lines.append("- RSI-6：%.2f / CCI：%.2f / MACD柱：%.2f\n" % (hit["rsi_6"], hit["cci"], hit["macdh"]))
        lines.append("- 连续在池天数：**%d天**\n" % hit["consecutive_days"])
        lines.append("- 半导体主线：✅\n")
        lines.append("\n")

        # 保存记录
        saved_records.append({
            "date": date_int,
            "code": hit["code"],
            "name": hit["name"],
            "price": hit["price"],
            "quote_change": hit["quote_change"],
            "kdjk": hit["kdjk"],
            "kdjd": hit["kdjd"],
            "kdjj": hit["kdjj"],
            "rsi_6": hit["rsi_6"],
            "cci": hit["cci"],
            "macdh": hit["macdh"],
            "consecutive_days": hit["consecutive_days"],
        })

    # 选股逻辑说明
    lines.append("## 选股逻辑说明\n")
    lines.append("### 魅视科技式选股理念\n")
    lines.append("借鉴魅视科技这类强势股的走势特征：\n")
    lines.append("1. **主线叠加**：必须属于当前最强主线（半导体）\n")
    lines.append("2. **强势确认**：KDJ-D ≥ 70，表明中期趋势强势确立\n")
    lines.append("3. **连续在池**：连续多日出现在策略A候选池中，说明持续强势\n")
    lines.append("\n")

    lines.append("### 策略A条件（超买追涨）\n")
    lines.append("- KDJ-J ≥ 100\n")
    lines.append("- KDJ-K ≥ 80\n")
    lines.append("- KDJ-D ≥ 70\n")
    lines.append("- RSI-6 > 70\n")
    lines.append("- MACD柱 > 0\n")
    lines.append("\n")

    lines.append("### 半导体主线判定\n")
    lines.append("股票名称包含以下关键词之一，且当日涨幅≥5%%：\n")
    lines.append("- 半导体、芯片、集成电路、IC\n")
    lines.append("- 晶圆、封测、光刻机\n")
    lines.append("- 半导体材料、半导体设备、功率半导体、第三代半导体\n")
    lines.append("- 或属于当日主线强势股（涨幅≥5%%）\n")
    lines.append("\n")

    lines.append("---\n")
    lines.append("\n")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。\n")
    lines.append("> 股市有风险，投资需谨慎。\n")

    return "\n".join(lines), saved_records


def save_report(tmp_datetime, report_content, saved_records):
    """保存报告到MySQL和文件"""
    date_int = tmp_datetime.strftime("%Y%m%d")
    try:
        # 保存汇总报告
        del_sql = "DELETE FROM `meishi_tech_summary` WHERE `date` = '%s'" % date_int
        common.insert(del_sql)
        ins_sql = "INSERT INTO `meishi_tech_summary` (`date`, `report_content`) VALUES (%s, %s)"
        common.insert(ins_sql, (date_int, report_content))

        # 保存明细数据
        for record in saved_records:
            del_detail_sql = "DELETE FROM `meishi_tech_report` WHERE `date` = %s AND `code` = %s"
            common.insert(del_detail_sql, (date_int, record["code"]))
            ins_detail_sql = """
                INSERT INTO `meishi_tech_report`
                (`date`, `code`, `name`, `price`, `quote_change`, `kdjk`, `kdjd`, `kdjj`,
                 `rsi_6`, `cci`, `macdh`, `consecutive_days`, `report_content`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            common.insert(ins_detail_sql, (
                date_int, record["code"], record["name"], record["price"], record["quote_change"],
                record["kdjk"], record["kdjd"], record["kdjj"], record["rsi_6"], record["cci"],
                record["macdh"], record["consecutive_days"], report_content
            ))

        print("[保存] MySQL写入成功")
    except Exception as e:
        print("[保存] MySQL写入异常:", e)
        traceback.print_exc()

    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        file_path = os.path.join(REPORT_DIR, "meishi_tech_%s.md" % date_int)
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
    print("魅视科技式选股 - %s" % date_str)
    print("=" * 60)

    # 确保表存在
    ensure_table()

    if is_weekend(tmp_datetime):
        report_content, _ = generate_report(tmp_datetime, [])
        save_report(tmp_datetime, report_content, [])
        print("周末/节假日，跳过数据分析")
        return

    # 检查是否有前一交易日数据
    check_sql = "SELECT count(1) FROM stock_zh_ah_name WHERE `date` = '%s'" % date_int
    count = common.select_count(check_sql)
    if count == 0:
        report_content = "# 魅视科技式选股 - %s\n\n> 今日无交易数据（可能为节假日），请确认。\n" % (
            tmp_datetime.strftime("%Y年%m月%d日")
        )
        save_report(tmp_datetime, report_content, [])
        print("当日无交易数据，跳过分析")
        return

    # 执行魅视科技式选股
    print("\n[魅视科技] 开始选股...")
    meishi_hits = buy_point.select_meishi_tech(tmp_datetime)

    # 生成报告
    print("\n[魅视科技] 生成报告...")
    report_content, saved_records = generate_report(tmp_datetime, meishi_hits)

    # 保存报告
    save_report(tmp_datetime, report_content, saved_records)

    print("\n[完成] 魅视科技式选股报告生成成功，推荐 %d 只股票" % len(meishi_hits))


# 初始化建表
ensure_table()


if __name__ == '__main__':
    tmp_datetime = common.run_with_args(stat_all)
