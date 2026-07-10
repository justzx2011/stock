#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import libs.common as common
import libs.buy_point as buy_point
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


def select_buyable(tmp_datetime, mainline_codes):
    """技术面择时：宽候选集评分 + 策略A情绪观察池。

    返回: (scored_list, sentiment_pool, market_kdj)
    - scored_list: 通过评分的候选（含分数、买点类型、止损位）
    - sentiment_pool: 策略A超买追涨命中但未入可买池的票（仅观察）
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    market_kdj = {"kdjk": 0, "kdjd": 0, "kdjj": 0}
    scored = []
    sentiment_pool = []

    # 大盘KDJ均值
    try:
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

    # 1) 宽候选预筛：涨跌幅 -5%~9.9%，有量，按成交额降序取 60 只
    #    覆盖趋势回踩 / 平台突破 / 分歧转强 三类买点
    try:
        print("[Phase2] 宽候选预筛...")
        pre_sql = """
            SELECT `code`,`name`,`latest_price`,`quote_change`,
                   `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macd`,`macdh`,`macds`,`boll`
            FROM stock_data.guess_indicators_daily
            WHERE `date` = %s
                AND quote_change BETWEEN -5 AND 9.9
                AND volume > 0 AND latest_price > 0
            ORDER BY turnover DESC
            LIMIT 60
        """
        rows = common.select(pre_sql, (datetime_int,))
        print("[Phase2] 宽候选: %d只, 开始评分..." % (len(rows) if rows else 0))
        for row in rows or []:
            code = str(row[0])
            ind = {
                "kdjj": row[6], "rsi_6": row[7], "cci": row[8], "macdh": row[10],
            }
            s = buy_point.score_candidate(
                code, row[1], row[2], row[3], ind, tmp_datetime, mainline_codes)
            if s:
                scored.append(s)
        print("[Phase2] 评分完成: %d只" % len(scored))
    except Exception as e:
        print("[Phase2] 宽候选评分异常:", e)
        traceback.print_exc()

    # 2) 策略A：超买追涨（情绪观察池来源 + 可能进可买池）
    #    命中票同样跑评分：分数够且位置/主线合适则进可买池，否则只进情绪观察池
    try:
        print("[Phase2] 策略A: 超买追涨筛选（情绪观察池）...")
        sql_a = """
            SELECT `code`,`name`,`latest_price`,`quote_change`,
                   `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macd`,`macdh`,`macds`
            FROM stock_data.guess_indicators_daily
            WHERE `date` = %s
                AND kdjj >= 100 AND kdjk >= 80 AND kdjd >= 70
                AND rsi_6 > 70 AND macdh > 0
            ORDER BY kdjj DESC
            LIMIT 20
        """
        rows_a = common.select(sql_a, (datetime_int,))
        existing = set(c["code"] for c in scored)
        for row in rows_a or []:
            code = str(row[0])
            ind = {"kdjj": row[6], "rsi_6": row[7], "cci": row[8], "macdh": row[10]}
            s = buy_point.score_candidate(
                code, row[1], row[2], row[3], ind, tmp_datetime, mainline_codes)
            if s and s["score"] >= buy_point.BUYABLE_THRESHOLD and code not in existing:
                scored.append(s)
                existing.add(code)
            else:
                # 进情绪观察池：这些票强势但位置偏高/非主线，追高盈亏比差
                pos_note = "位置偏高" if (s and s["position_pct"] > 70) else "未达买点结构"
                sentiment_pool.append({
                    "code": code, "name": row[1],
                    "price": float(row[2]) if row[2] else 0,
                    "quote_change": float(row[3]) if row[3] else 0,
                    "kdjj": float(row[6]) if row[6] else 0,
                    "rsi_6": float(row[7]) if row[7] else 0,
                    "score": s["score"] if s else 0,
                    "note": pos_note,
                })
        print("[Phase2] 策略A情绪观察池: %d只" % len(sentiment_pool))
    except Exception as e:
        print("[Phase2] 策略A异常:", e)
        traceback.print_exc()

    # 3) 分数达标但被硬门槛排除的票（位置过高/盈亏比过差）→ 挪到情绪观察池
    excluded = buy_point.excluded_by_gate(scored)
    if excluded:
        sentiment_pool = excluded + sentiment_pool
        print("[Phase2] 硬门槛排除(高位/低盈亏比): %d只" % len(excluded))

    return scored, sentiment_pool, market_kdj


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


def generate_report(tmp_datetime, phase1_data, scored, sentiment_pool, market_kdj, market_state):
    """生成 Markdown 报告：以可买池为核心"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    is_wknd = tmp_datetime.weekday() >= 5

    stats = get_market_stats(tmp_datetime)
    picks = buy_point.pick_buyable(scored, market_state)

    lines = []
    lines.append("# A股选股晨报 · %s（%s）\n" % (date_str, weekday))

    if is_wknd:
        lines.append("**休市提示**：今日%s，A股休市无交易数据，本期为周末复盘。" % weekday)
    elif stats["total_count"] == 0:
        lines.append("**休市提示**：今日无交易数据（可能为节假日）。")
    lines.append("")

    # ===== 一、大盘概览 =====
    lines.append("## 一、大盘概览\n")
    if stats["total_count"] > 0:
        lines.append("全市场 %d 只：**上涨 %d 家** / 下跌 %d 家 / 平盘 %d 家" % (
            stats["total_count"], stats["up_count"], stats["down_count"], stats["flat_count"]))
        if stats["limit_up"] > 0 or stats["limit_down"] > 0:
            lines.append("涨停 **%d** 家 / 跌停 **%d** 家" % (stats["limit_up"], stats["limit_down"]))
        lines.append("")

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
        lines.append("市场状态：**%s** | 均涨幅 %+.2f%%" % (
            buy_point.market_label(market_state), stats["avg_change"]))
        lines.append("")

        if stats["top_gainers"]:
            gainers_str = "、".join(["%s（+%.1f%%）" % (get_eastmoney_link(g["code"], g["name"]), g["change"]) for g in stats["top_gainers"][:6]])
            lines.append("牛股 TOP：%s" % gainers_str)
            lines.append("")
    else:
        lines.append("今日无交易数据。\n")

    # ===== 二、主线扫描 =====
    lines.append("## 二、主线扫描\n")
    if phase1_data["hot_concept_sectors"]:
        if phase1_data["hot_industry_sectors"]:
            sectors_str = "、".join([s["name"] for s in phase1_data["hot_industry_sectors"][:5]])
            lines.append("行业领涨：%s" % sectors_str)
        if phase1_data["leader_stocks"]:
            leaders_str = "、".join(["%s（+%.1f%%）" % (get_eastmoney_link(s["code"], s["name"]), s["quote_change"]) for s in phase1_data["leader_stocks"][:6]])
            lines.append("龙头股：%s" % leaders_str)
        lines.append("")

    # ===== 三、可买池（核心）=====
    lines.append("## 三、可买池（核心·按评分排序）\n")
    lines.append("> 评分维度：位置25 / 趋势20 / 结构25 / 确认20 / 主线10，满分100。入池线 %d 分。\n" % buy_point.BUYABLE_THRESHOLD)
    lines.append("> 市场状态 %s，本日最多推荐 %d 只，**宁缺毋滥**。\n" % (
        buy_point.market_label(market_state), buy_point.max_picks(market_state)))

    if picks:
        for i, s in enumerate(picks, 1):
            rr_tag = "" if s["rr_ok"] else " ⚠️盈亏比偏低"
            ml_tag = " ｜ 主线核心" if s["in_mainline"] else ""
            lines.append("### %d. %s（%s）— %s ｜ 评分 **%d**%s%s\n" % (
                i, get_eastmoney_link(s["code"], s["name"]), s["code"], s["bp_type"], s["score"], rr_tag, ml_tag))
            lines.append("- 现价 **%.2f元** %+.2f%%" % (s["price"], s["quote_change"]))
            lines.append("- 结构：60日位置 %.0f%%（距前高 %.1f%%）｜ 回踩深度 %.1f%% ｜ %s" % (
                s["position_pct"], s["dist_from_high"], s["pullback_depth"],
                "趋势向上" if s["trend_up"] else (
                    "下跌末段转强" if s["bp_type"] == "分歧转强" else "趋势走平/向下")))
            vol_tag = "缩量" if s["vol_shrink"] else ("放量" if s["vol_expand"] else "量能平稳")
            confirm_tags = []
            if s["today_strong"]: confirm_tags.append("阳线收高")
            if s["stop_falling"]: confirm_tags.append("低点抬高")
            if s["broke_platform"]: confirm_tags.append("突破平台")
            lines.append("- 量能：%s ｜ 确认：%s" % (vol_tag, "、".join(confirm_tags) if confirm_tags else "待确认"))
            lines.append("- 20MA=%.2f 60MA=%.2f ｜ KDJ-J=%.0f RSI=%.0f" % (
                s["ma20"], s["ma60"], s["kdjj"], s["rsi_6"]))
            lines.append("- **止损：跌破 %.2f**（%s结构化止损）" % (s["stop_price"], s["bp_type"]))
            lines.append("- 盈亏比 %.2f ｜ 入场：确认买点结构成立后分批介入" % s["rr"])
            lines.append("")
    else:
        lines.append("今日无可买标的（无票达到 %d 分结构），**建议观望**。\n" % buy_point.BUYABLE_THRESHOLD)

    # ===== 四、情绪观察池（仅观察，不建议买入）=====
    lines.append("## 四、情绪观察池（仅观察，不建议买入）\n")
    if sentiment_pool:
        lines.append("> 以下为超买追涨命中票：指标强势，但位置偏高或未达买点结构，追高盈亏比差，仅作情绪参考。\n")
        for s in sentiment_pool[:10]:
            lines.append("- %s（%s） %.2f元 %+.2f%% ｜ J=%.0f RSI=%.0f ｜ 评分%d ｜ %s" % (
                get_eastmoney_link(s["code"], s["name"]), s["code"], s["price"], s["quote_change"],
                s["kdjj"], s["rsi_6"], s["score"], s["note"]))
        lines.append("")
    else:
        lines.append("今日无超买追涨命中。\n")

    # ===== 五、风控建议 =====
    lines.append("## 五、风控建议\n")
    if market_state == "strong":
        lines.append("- 市场强势，总仓位可至 **50-60%**，但警惕次日分歧")
    elif market_state == "weak":
        lines.append("- 市场弱势，**总仓位 ≤ 30%**，仅试探性建仓，宁缺毋滥")
    else:
        lines.append("- 总仓位 **≤ 50%**，跟随趋势控制回撤")
    lines.append("- 个股止损按买点结构执行：")
    lines.append("  - 平台突破 → 跌破平台下沿")
    lines.append("  - 趋势回踩 → 跌破回踩低点")
    lines.append("  - 分歧转强 → 跌破转强K线低点")
    lines.append("- 规避：高位连板妖股、无量反弹、业绩地雷股、盈亏比偏低标的")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。")
    lines.append("> 买点评分侧重结构与位置，仍需结合实时盘面与基本面判断。")
    lines.append("> **股市有风险，投资需谨慎。**")

    return "\n".join(lines)


def save_report(tmp_datetime, report_content):
    """保存报告到MySQL和文件"""
    date_int = tmp_datetime.strftime("%Y%m%d")
    try:
        del_sql = "DELETE FROM `stock_morning_report` WHERE `date` = '%s'" % date_int
        common.insert(del_sql)
        ins_sql = "INSERT INTO `stock_morning_report` (`date`, `report_content`) VALUES (%s, %s)"
        common.insert(ins_sql, (date_int, report_content))
        print("[保存] MySQL写入成功")
    except Exception as e:
        print("[保存] MySQL写入异常:", e)
        traceback.print_exc()
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

    if is_weekend(tmp_datetime):
        report_content = "# A股选股晨报 - %s\n\n> 今日为非交易日（%s），无交易数据。\n" % (
            tmp_datetime.strftime("%Y年%m月%d日"),
            "周末" if tmp_datetime.weekday() == 5 else "周日"
        )
        save_report(tmp_datetime, report_content)
        print("周末/节假日，跳过数据分析")
        return

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

    # 主线核心代码集合
    mainline_codes = buy_point.get_mainline_codes(tmp_datetime)
    print("[主线] 主线核心代码: %d个" % len(mainline_codes))

    # 市场状态
    stats = get_market_stats(tmp_datetime)
    overview = {
        "avg_change": stats["avg_change"], "up_count": stats["up_count"],
        "down_count": stats["down_count"], "is_weak": stats["avg_change"] < -1.0,
    }
    market_state = buy_point.classify_market(overview)
    print("[市场] 状态: %s" % market_state)

    # Phase 2: 评分选股
    print("\n[Phase 2] 评分选股...")
    scored, sentiment_pool, market_kdj = select_buyable(tmp_datetime, mainline_codes)
    print("[Phase 2] 评分候选: %d只, 情绪观察池: %d只" % (len(scored), len(sentiment_pool)))

    picks = buy_point.pick_buyable(scored, market_state)
    print("[Phase 2] 可买池: %d只" % len(picks))

    # Phase 3: 生成报告
    print("\n[Phase 3] 生成报告...")
    report_content = generate_report(tmp_datetime, phase1_data, scored, sentiment_pool, market_kdj, market_state)
    save_report(tmp_datetime, report_content)
    print("\n[完成] A股选股晨报生成成功")


# 初始化建表
ensure_table()


if __name__ == '__main__':
    tmp_datetime = common.run_with_args(stat_all)
