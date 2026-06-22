#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import libs.common as common
import pandas as pd
import numpy as np
import datetime
import os
import traceback
import akshare as ak
import stockstats

# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `stock_morning_report` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `date` VARCHAR(8) NOT NULL COMMENT '日期YYYYMMDD',
    `report_content` LONGTEXT NOT NULL COMMENT 'Markdown报告内容',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_date` (`date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

REPORT_DIR = "/data/reports/"


def ensure_table():
    """确保报告表存在"""
    try:
        common.insert(CREATE_TABLE_SQL)
    except Exception as e:
        print("建表异常（可能已存在）:", e)


def is_weekend(tmp_datetime):
    """判断是否周末"""
    return tmp_datetime.weekday() >= 5


def phase1_sentiment(tmp_datetime):
    """Phase 1: 消息面选方向 - 从当日数据分析热门板块和龙头股"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    result = {
        "hot_concept_sectors": [],
        "hot_industry_sectors": [],
        "leader_stocks": []
    }

    # 从当日股票数据中分析涨幅榜龙头股
    try:
        print("[Phase1] 从当日数据分析龙头股...")
        sql = """
            SELECT code, name, latest_price, quote_change, volume, turnover
            FROM stock_zh_ah_name
            WHERE date = '%s' AND quote_change > 0 AND latest_price > 0
            ORDER BY quote_change DESC
            LIMIT 20
        """ % datetime_int
        rows = common.select(sql)
        if rows:
            for row in rows:
                result["leader_stocks"].append({
                    "sector": "涨幅榜",
                    "code": str(row[0]),
                    "name": str(row[1]),
                    "latest_price": float(row[2]) if row[2] else 0,
                    "quote_change": float(row[3]) if row[3] else 0
                })
            print("[Phase1] 涨幅榜龙头: %d只" % len(result["leader_stocks"]))
    except Exception as e:
        print("[Phase1] 分析龙头股异常:", e)
        traceback.print_exc()

    # 尝试获取板块数据（akshare东方财富API，容器内可能不通）
    try:
        print("[Phase1] 尝试获取概念板块...")
        concept_df = ak.stock_board_concept_name_em()
        common._rate_limit()
        if concept_df is not None and not concept_df.empty:
            concept_df = concept_df.sort_values(by="涨跌幅", ascending=False).head(10)
            for _, row in concept_df.iterrows():
                result["hot_concept_sectors"].append({
                    "name": row.get("板块名称", ""),
                    "quote_change": float(row.get("涨跌幅", 0)),
                    "leader_name": row.get("领涨股票", ""),
                    "leader_change": float(row.get("领涨股票-涨跌幅", 0)) if "领涨股票-涨跌幅" in row.index else 0
                })
    except Exception as e:
        print("[Phase1] 概念板块API不通，使用涨幅榜数据替代")

    try:
        print("[Phase1] 尝试获取行业板块...")
        industry_df = ak.stock_board_industry_name_em()
        common._rate_limit()
        if industry_df is not None and not industry_df.empty:
            industry_df = industry_df.sort_values(by="涨跌幅", ascending=False).head(10)
            for _, row in industry_df.iterrows():
                result["hot_industry_sectors"].append({
                    "name": row.get("板块名称", ""),
                    "quote_change": float(row.get("涨跌幅", 0)),
                    "leader_name": row.get("领涨股票", ""),
                    "leader_change": float(row.get("领涨股票-涨跌幅", 0)) if "领涨股票-涨跌幅" in row.index else 0
                })
    except Exception as e:
        print("[Phase1] 行业板块API不通，使用涨幅榜数据替代")

    # 如果板块API不通，用涨幅榜数据生成虚拟板块
    if not result["hot_concept_sectors"] and result["leader_stocks"]:
        print("[Phase1] 使用涨幅榜TOP10作为热点参考")
        for s in result["leader_stocks"][:10]:
            result["hot_concept_sectors"].append({
                "name": s["name"],
                "quote_change": s["quote_change"],
                "leader_name": s["name"],
                "leader_change": s["quote_change"]
            })

    return result


def phase2_technical(tmp_datetime):
    """Phase 2: 技术面择时 - 两种策略筛选"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    strategy_a = []  # 超买追涨
    strategy_b = []  # 突破回踩
    market_kdj = {"kdjk": 0, "kdjd": 0, "kdjj": 0}

    # 计算大盘KDJ均值
    try:
        print("[Phase2] 计算大盘KDJ均值...")
        avg_sql = """
            SELECT AVG(kdjk) as avg_k, AVG(kdjd) as avg_d, AVG(kdjj) as avg_j
            FROM stock_data.guess_indicators_daily
            WHERE `date` = %s AND kdjk > 0 AND kdjd > 0
        """
        avg_rows = common.select(avg_sql, (datetime_int,))
        if avg_rows and avg_rows[0][0] is not None:
            market_kdj["kdjk"] = round(float(avg_rows[0][0]), 2)
            market_kdj["kdjd"] = round(float(avg_rows[0][1]), 2)
            market_kdj["kdjj"] = round(float(avg_rows[0][2]), 2)
    except Exception as e:
        print("[Phase2] 计算KDJ均值异常:", e)

    # 策略A: 超买追涨 - J>=100, K>=80, D>=70, RSI6>70, MACD柱>0
    try:
        print("[Phase2] 策略A: 超买追涨筛选...")
        sql_a = """
            SELECT `date`,`code`,`name`,`latest_price`,`quote_change`,`volume`,`turnover`,
                   `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macd`,`macdh`,`macds`
            FROM stock_data.guess_indicators_daily
            WHERE `date` = %s
                AND kdjj >= 100 AND kdjk >= 80 AND kdjd >= 70
                AND rsi_6 > 70
            ORDER BY kdjj DESC
            LIMIT 20
        """
        rows_a = common.select(sql_a, (datetime_int,))
        print("[Phase2] 策略A初筛: %d只" % (len(rows_a) if rows_a else 0))
        if rows_a:
            for row in rows_a:
                stock = {
                    "date": row[0], "code": row[1], "name": row[2],
                    "latest_price": float(row[3]) if row[3] else 0,
                    "quote_change": float(row[4]) if row[4] else 0,
                    "volume": float(row[5]) if row[5] else 0,
                    "turnover": float(row[6]) if row[6] else 0,
                    "kdjk": float(row[7]) if row[7] else 0,
                    "kdjd": float(row[8]) if row[8] else 0,
                    "kdjj": float(row[9]) if row[9] else 0,
                    "rsi_6": float(row[10]) if row[10] else 0,
                    "cci": float(row[11]) if row[11] else 0,
                    "macd": float(row[12]) if row[12] else 0,
                    "macdh": float(row[13]) if row[13] else 0,
                    "macds": float(row[14]) if row[14] else 0,
                }
                # 验证MACD柱为正（趋势确认）
                if stock["macdh"] > 0:
                    strategy_a.append(stock)
        print("[Phase2] 策略A MACD验证后: %d只" % len(strategy_a))
    except Exception as e:
        print("[Phase2] 策略A异常:", e)
        traceback.print_exc()

    # 策略B: 突破回踩 - 涨跌幅-2%~2%, 价格在60MA上方, 靠近20MA, RSI 40-60, CCI -100~100
    try:
        print("[Phase2] 策略B: 突破回踩筛选...")
        sql_b = """
            SELECT `date`,`code`,`name`,`latest_price`,`quote_change`,`volume`,`turnover`,
                   `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`
            FROM stock_data.guess_indicators_daily
            WHERE `date` = %s
                AND quote_change BETWEEN -2 AND 2
                AND rsi_6 BETWEEN 40 AND 60
                AND cci BETWEEN -100 AND 100
                AND volume > 0
            ORDER BY ABS(quote_change) ASC
            LIMIT 30
        """
        rows_b = common.select(sql_b, (datetime_int,))
        if rows_b:
            print("[Phase2] 策略B初筛: %d只, 开始K线验证..." % len(rows_b))
            for row in rows_b:
                code = row[1]
                try:
                    # 获取K线计算均线
                    date_end = tmp_datetime.strftime("%Y-%m-%d")
                    date_start = (tmp_datetime + datetime.timedelta(days=-100)).strftime("%Y-%m-%d")
                    stock_data = common.get_hist_data_cache(code, date_start, date_end)
                    if stock_data is None or stock_data.empty:
                        continue

                    stock_stat = stockstats.StockDataFrame.retype(stock_data)
                    # stockstats懒加载列，直接访问触发计算
                    close_20_sma = float(stock_stat["close_20_sma"].iloc[-1])
                    close_60_sma = float(stock_stat["close_60_sma"].iloc[-1])
                    current_price = float(row[3]) if row[3] else 0

                    if close_60_sma > 0 and close_20_sma > 0 and current_price > 0:
                        # 价格在60MA上方，且靠近20MA（偏离2%以内）
                        above_60ma = current_price > close_60_sma
                        near_20ma = abs(current_price - close_20_sma) / close_20_sma < 0.02

                        if above_60ma and near_20ma:
                            strategy_b.append({
                                "date": row[0], "code": code, "name": row[2],
                                "latest_price": current_price,
                                "quote_change": float(row[4]) if row[4] else 0,
                                "volume": float(row[5]) if row[5] else 0,
                                "turnover": float(row[6]) if row[6] else 0,
                                "kdjk": float(row[7]) if row[7] else 0,
                                "kdjd": float(row[8]) if row[8] else 0,
                                "kdjj": float(row[9]) if row[9] else 0,
                                "rsi_6": float(row[10]) if row[10] else 0,
                                "cci": float(row[11]) if row[11] else 0,
                                "ma20": round(close_20_sma, 2),
                                "ma60": round(close_60_sma, 2)
                            })
                            if len(strategy_b) >= 10:
                                break
                except Exception as e:
                    print("[Phase2] 策略B处理异常:", code, e)
            print("[Phase2] 策略B K线验证后: %d只" % len(strategy_b))
    except Exception as e:
        print("[Phase2] 策略B异常:", e)
        traceback.print_exc()

    return strategy_a, strategy_b, market_kdj


def get_market_stats(tmp_datetime):
    """获取当日市场统计数据"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    stats = {
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "limit_up": 0, "limit_down": 0,
        "total_count": 0,
        "avg_change": 0,
        "top_gainers": [],
        "top_losers": []
    }
    try:
        # 涨跌家数
        sql = """
            SELECT
                SUM(CASE WHEN quote_change > 0 THEN 1 ELSE 0 END) as up_count,
                SUM(CASE WHEN quote_change < 0 THEN 1 ELSE 0 END) as down_count,
                SUM(CASE WHEN quote_change = 0 THEN 1 ELSE 0 END) as flat_count,
                SUM(CASE WHEN quote_change >= 9.9 THEN 1 ELSE 0 END) as limit_up,
                SUM(CASE WHEN quote_change <= -9.9 THEN 1 ELSE 0 END) as limit_down,
                COUNT(1) as total,
                AVG(quote_change) as avg_change
            FROM stock_zh_ah_name WHERE date = '%s'
        """ % datetime_int
        rows = common.select(sql)
        if rows and rows[0][0] is not None:
            stats["up_count"] = int(rows[0][0])
            stats["down_count"] = int(rows[0][1])
            stats["flat_count"] = int(rows[0][2])
            stats["limit_up"] = int(rows[0][3])
            stats["limit_down"] = int(rows[0][4])
            stats["total_count"] = int(rows[0][5])
            stats["avg_change"] = round(float(rows[0][6]), 2)

        # 涨幅榜TOP10
        sql_top = """
            SELECT code, name, latest_price, quote_change
            FROM stock_zh_ah_name WHERE date = '%s' AND quote_change > 0
            ORDER BY quote_change DESC LIMIT 10
        """ % datetime_int
        top_rows = common.select(sql_top)
        if top_rows:
            for r in top_rows:
                stats["top_gainers"].append({
                    "code": r[0], "name": r[1],
                    "price": float(r[2]) if r[2] else 0,
                    "change": float(r[3]) if r[3] else 0
                })

        # 跌幅榜TOP5
        sql_bottom = """
            SELECT code, name, latest_price, quote_change
            FROM stock_zh_ah_name WHERE date = '%s' AND quote_change < 0
            ORDER BY quote_change ASC LIMIT 5
        """ % datetime_int
        bottom_rows = common.select(sql_bottom)
        if bottom_rows:
            for r in bottom_rows:
                stats["top_losers"].append({
                    "code": r[0], "name": r[1],
                    "price": float(r[2]) if r[2] else 0,
                    "change": float(r[3]) if r[3] else 0
                })
    except Exception as e:
        print("[市场统计] 异常:", e)
    return stats


WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def generate_report(tmp_datetime, phase1_data, phase2_data):
    """Phase 3: 生成Markdown报告 - 专业晨报风格"""
    strategy_a, strategy_b, market_kdj = phase2_data
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    is_weekend = tmp_datetime.weekday() >= 5
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 获取市场统计数据
    stats = get_market_stats(tmp_datetime)

    lines = []

    # ===== 标题 =====
    lines.append("# A股选股晨报 · %s（%s）\n" % (date_str, weekday))

    # ===== 休市提示 =====
    if is_weekend:
        lines.append("**休市提示**：今日%s，A股休市无交易数据，本期为周末复盘。" % weekday)
    elif stats["total_count"] == 0:
        lines.append("**休市提示**：今日无交易数据（可能为节假日）。")
    lines.append("")

    # ===== 一、大盘概览 =====
    lines.append("## 一、大盘概览\n")

    if stats["total_count"] > 0:
        # 涨跌家数
        lines.append("全市场 %d 只股票：**上涨 %d 家** / 下跌 %d 家 / 平盘 %d 家" % (
            stats["total_count"], stats["up_count"], stats["down_count"], stats["flat_count"]))
        if stats["limit_up"] > 0 or stats["limit_down"] > 0:
            lines.append("涨停 **%d** 家 / 跌停 **%d** 家" % (stats["limit_up"], stats["limit_down"]))
        lines.append("")

        # 技术面
        kdj_comment = ""
        if market_kdj["kdjj"] > 80:
            kdj_comment = "J值偏高，短期超买，注意回调风险"
        elif market_kdj["kdjj"] < 20:
            kdj_comment = "J值偏低，超卖区域，关注反弹机会"
        elif market_kdj["kdjj"] > 60:
            kdj_comment = "偏强运行，多头占优"
        elif market_kdj["kdjj"] < 40:
            kdj_comment = "偏弱运行，空头压制"
        else:
            kdj_comment = "中性区间，方向待选择"

        lines.append("KDJ 均值：K=%.1f / D=%.1f / J=%.1f — %s" % (
            market_kdj["kdjk"], market_kdj["kdjd"], market_kdj["kdjj"], kdj_comment))
        lines.append("")

        # 牛股TOP
        if stats["top_gainers"]:
            gainers_str = "、".join(["%s（+%.1f%%）" % (g["name"], g["change"]) for g in stats["top_gainers"][:6]])
            lines.append("牛股 TOP：%s" % gainers_str)
            lines.append("")
    else:
        lines.append("今日无交易数据。\n")

    # ===== 二、今日热点 =====
    lines.append("## 二、今日热点\n")

    if phase1_data["hot_concept_sectors"]:
        if phase1_data["hot_industry_sectors"]:
            sectors_str = "、".join([s["name"] for s in phase1_data["hot_industry_sectors"][:5]])
            lines.append("行业领涨：%s" % sectors_str)
        if phase1_data["leader_stocks"]:
            leaders_str = "、".join(["%s（+%.1f%%）" % (s["name"], s["quote_change"]) for s in phase1_data["leader_stocks"][:6]])
            lines.append("龙头股：%s" % leaders_str)
        lines.append("")

    # ===== 三、策略A：超买追涨 =====
    lines.append("## 三、策略A（超买追涨）\n")

    if strategy_a:
        lines.append("**筛选条件**：KDJ-J≥100、K≥80、D≥70、RSI6>70、MACD柱>0\n")
        names_str = "、".join(["**%s**（%s，J=%.0f）" % (s["name"], s["code"], s["kdjj"]) for s in strategy_a[:8]])
        lines.append("今日命中 %d 只：%s\n" % (len(strategy_a), names_str))

        # TOP3 重点分析
        lines.append("**重点个股**\n")
        for s in strategy_a[:3]:
            lines.append("- **%s（%s）** %.2f元 %+.2f%%" % (s["name"], s["code"], s["latest_price"], s["quote_change"]))
            lines.append("  KDJ-J=%.0f RSI=%.0f MACD柱=%.2f | 追涨策略，J值拐头即止盈" % (
                s["kdjj"], s["rsi_6"], s["macdh"]))
        lines.append("")
    else:
        lines.append("今日无超买追涨标的，策略暂缓。\n")

    # ===== 四、策略B：突破回踩 =====
    lines.append("## 四、策略B（突破回踩）\n")

    if strategy_b:
        lines.append("**筛选条件**：涨跌幅±2%内、价格站上60MA、靠近20MA（偏离<2%）、RSI 40-60\n")
        names_str = "、".join(["**%s**（%s）" % (s["name"], s["code"]) for s in strategy_b[:8]])
        lines.append("今日命中 %d 只：%s\n" % (len(strategy_b), names_str))

        # 重点分析
        lines.append("**重点个股**\n")
        for s in strategy_b[:3]:
            lines.append("- **%s（%s）** %.2f元 %+.2f%%" % (s["name"], s["code"], s["latest_price"], s["quote_change"]))
            lines.append("  20MA=%.2f 60MA=%.2f RSI=%.0f | 回踩确认，放量反弹可介入，跌破60MA止损" % (
                s["ma20"], s["ma60"], s["rsi_6"]))
        lines.append("")
    else:
        lines.append("今日无突破回踩标的，策略暂缓。\n")

    # ===== 五、风控建议 =====
    lines.append("## 五、风控建议\n")

    # 根据KDJ状态给出不同建议
    if market_kdj["kdjj"] > 80:
        lines.append("- 市场超买，**总仓位 ≤ 40%**，优先止盈高位票")
    elif market_kdj["kdjj"] < 20:
        lines.append("- 市场超卖，可适度加仓至 **60%**，分批低吸")
    else:
        lines.append("- 总仓位 **≤ 60%**，跟随趋势但控制回撤")

    lines.append("- 止损纪律：单票 **-5%** 或跌破 20 日线强制止损")
    lines.append("- 规避：高位连板妖股、无量反弹、业绩地雷股")
    lines.append("")

    # ===== 免责声明 =====
    lines.append("---")
    lines.append("")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。")
    lines.append("> 技术指标存在滞后性，请结合基本面和市场环境综合判断。")
    lines.append("> **股市有风险，投资需谨慎。**")

    report_content = "\n".join(lines)
    return report_content


def save_report(tmp_datetime, report_content):
    """保存报告到MySQL和文件"""
    date_int = tmp_datetime.strftime("%Y%m%d")

    # 保存到MySQL
    try:
        # 先删除旧数据
        del_sql = "DELETE FROM `stock_morning_report` WHERE `date` = '%s'" % date_int
        common.insert(del_sql)
        # 插入新数据
        ins_sql = "INSERT INTO `stock_morning_report` (`date`, `report_content`) VALUES (%s, %s)"
        common.insert(ins_sql, (date_int, report_content))
        print("[保存] MySQL写入成功")
    except Exception as e:
        print("[保存] MySQL写入异常:", e)
        traceback.print_exc()

    # 保存到文件
    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        file_path = os.path.join(REPORT_DIR, "A股晨报-%s.md" % date_int)
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
    print("A股选股晨报 - %s" % date_str)
    print("=" * 60)

    # 周末/节假日处理
    if is_weekend(tmp_datetime):
        report_content = "# A股选股晨报 - %s\n\n> 今日为非交易日（%s），无交易数据。\n" % (
            tmp_datetime.strftime("%Y年%m月%d日"),
            "周末" if tmp_datetime.weekday() == 5 else "周日"
        )
        save_report(tmp_datetime, report_content)
        print("周末/节假日，跳过数据分析")
        return

    # 检查当日是否有交易数据
    check_sql = "SELECT count(1) FROM stock_zh_ah_name WHERE `date` = '%s'" % date_int
    count = common.select_count(check_sql)
    if count == 0:
        report_content = "# A股选股晨报 - %s\n\n> 今日无交易数据（可能为节假日），请确认。\n" % (
            tmp_datetime.strftime("%Y年%m月%d日")
        )
        save_report(tmp_datetime, report_content)
        print("当日无交易数据，跳过分析")
        return

    # Phase 1: 消息面选方向
    print("\n[Phase 1] 消息面选方向...")
    phase1_data = phase1_sentiment(tmp_datetime)
    print("[Phase 1] 概念板块: %d个, 行业板块: %d个, 龙头股: %d个" % (
        len(phase1_data["hot_concept_sectors"]),
        len(phase1_data["hot_industry_sectors"]),
        len(phase1_data["leader_stocks"])
    ))

    # Phase 2: 技术面择时
    print("\n[Phase 2] 技术面择时...")
    strategy_a, strategy_b, market_kdj = phase2_technical(tmp_datetime)
    print("[Phase 2] 策略A(超买追涨): %d只, 策略B(突破回踩): %d只" % (
        len(strategy_a), len(strategy_b)))

    # Phase 3: 生成报告
    print("\n[Phase 3] 生成报告...")
    report_content = generate_report(tmp_datetime, phase1_data, (strategy_a, strategy_b, market_kdj))

    # 保存报告
    save_report(tmp_datetime, report_content)
    print("\n[完成] A股选股晨报生成成功")


# 初始化建表
ensure_table()


if __name__ == '__main__':
    tmp_datetime = common.run_with_args(stat_all)
