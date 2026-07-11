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
CREATE TABLE IF NOT EXISTS `stock_evening_report` (
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
    try:
        common.insert(CREATE_TABLE_SQL)
    except Exception as e:
        print("建表异常（可能已存在）:", e)


def is_weekend(tmp_datetime):
    return tmp_datetime.weekday() >= 5


def get_market_overview(tmp_datetime):
    """获取大盘概况：指数、涨跌家数、成交额"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    overview = {
        "total_count": 0,
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "limit_up": 0, "limit_down": 0,
        "avg_change": 0,
        "total_turnover": 0,
        "is_weak": False
    }
    try:
        sql = """
            SELECT
                COUNT(1),
                SUM(CASE WHEN quote_change > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN quote_change < 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN quote_change = 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN quote_change >= 9.9 THEN 1 ELSE 0 END),
                SUM(CASE WHEN quote_change <= -9.9 THEN 1 ELSE 0 END),
                AVG(quote_change),
                SUM(turnover)
            FROM stock_zh_ah_name WHERE date = '%s'
        """ % datetime_int
        rows = common.select(sql)
        if rows and rows[0][0] is not None:
            overview["total_count"] = int(rows[0][0])
            overview["up_count"] = int(rows[0][1])
            overview["down_count"] = int(rows[0][2])
            overview["flat_count"] = int(rows[0][3])
            overview["limit_up"] = int(rows[0][4])
            overview["limit_down"] = int(rows[0][5])
            overview["avg_change"] = round(float(rows[0][6]), 2)
            overview["total_turnover"] = float(rows[0][7]) if rows[0][7] else 0
            overview["is_weak"] = overview["avg_change"] < -1.0
    except Exception as e:
        print("[大盘概况] 异常:", e)
    return overview


def find_main_line(tmp_datetime):
    """锁定当天最强主线：涨幅最大的板块 + 涨停股最多"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    result = {
        "hot_stocks": [],
        "sectors": [],
        "leaders": []
    }

    try:
        print("[主线] 分析当日强势股...")
        sql_strong = """
            SELECT code, name, latest_price, quote_change, volume, turnover
            FROM stock_zh_ah_name
            WHERE date = '%s' AND quote_change >= 5 AND latest_price > 0
            ORDER BY quote_change DESC
            LIMIT 30
        """ % datetime_int
        rows = common.select(sql_strong)
        if rows:
            for r in rows:
                result["hot_stocks"].append({
                    "code": r[0], "name": r[1],
                    "price": float(r[2]) if r[2] else 0,
                    "change": float(r[3]) if r[3] else 0,
                    "volume": float(r[4]) if r[4] else 0,
                    "turnover": float(r[5]) if r[5] else 0
                })
        print("[主线] 强势股: %d只" % len(result["hot_stocks"]))
    except Exception as e:
        print("[主线] 分析强势股异常:", e)

    try:
        sql_limit_up = """
            SELECT code, name, latest_price, quote_change
            FROM stock_zh_ah_name
            WHERE date = '%s' AND quote_change >= 9.9
            ORDER BY turnover DESC
            LIMIT 10
        """ % datetime_int
        rows = common.select(sql_limit_up)
        if rows:
            for r in rows:
                result["leaders"].append({
                    "code": r[0], "name": r[1],
                    "price": float(r[2]) if r[2] else 0,
                    "change": float(r[3]) if r[3] else 0
                })
        print("[主线] 涨停股: %d只" % len(result["leaders"]))
    except Exception as e:
        print("[主线] 分析涨停股异常:", e)

    try:
        print("[主线] 尝试获取板块数据...")
        concept_df = ak.stock_board_concept_name_em()
        common._rate_limit()
        if concept_df is not None and not concept_df.empty:
            concept_df = concept_df.sort_values(by="涨跌幅", ascending=False).head(10)
            for _, row in concept_df.iterrows():
                result["sectors"].append({
                    "name": row.get("板块名称", ""),
                    "change": float(row.get("涨跌幅", 0)),
                    "leader": row.get("领涨股票", "")
                })
    except Exception as e:
        print("[主线] 板块API不通，使用涨幅榜替代")

    return result


def select_candidates(tmp_datetime, overview, mainline_codes):
    """评分选股：宽候选集评分 + 策略A情绪观察池。

    返回: (scored_list, sentiment_pool, market_kdj, strategy_a_hits)
    - strategy_a_hits: 旧版策略A（超买追涨）全部命中票，供报告附录对比
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    market_kdj = {"kdjk": 0, "kdjd": 0, "kdjj": 0}
    scored = []
    sentiment_pool = []
    strategy_a_hits = []

    # 大盘KDJ均值
    try:
        avg_sql = """
            SELECT AVG(kdjk), AVG(kdjd), AVG(kdjj)
            FROM stock_data.guess_indicators_daily
            WHERE date = '%s' AND kdjk > 0 AND kdjd > 0
        """ % datetime_int
        rows = common.select(avg_sql)
        if rows and rows[0][0] is not None:
            market_kdj["kdjk"] = round(float(rows[0][0]), 2)
            market_kdj["kdjd"] = round(float(rows[0][1]), 2)
            market_kdj["kdjj"] = round(float(rows[0][2]), 2)
    except Exception as e:
        print("[选股] KDJ均值异常:", e)

    if overview["is_weak"]:
        print("[选股] 大盘弱势(avg=%.2f)，可买池按弱势收敛（≤2只）" % overview["avg_change"])

    # 1) 宽候选预筛
    try:
        print("[选股] 宽候选预筛...")
        pre_sql = """
            SELECT g.code, g.name, g.latest_price, g.quote_change,
                   g.kdjk, g.kdjd, g.kdjj, g.rsi_6, g.cci, g.macd, g.macdh, g.macds, g.boll
            FROM stock_data.guess_indicators_daily g
            WHERE g.date = '%s'
                AND g.quote_change BETWEEN -5 AND 9.9
                AND g.volume > 0 AND g.latest_price > 0
            ORDER BY g.turnover DESC
            LIMIT 60
        """ % datetime_int
        rows = common.select(pre_sql)
        print("[选股] 宽候选: %d只, 开始评分..." % (len(rows) if rows else 0))
        for row in rows or []:
            code = str(row[0])
            ind = {"kdjj": row[6], "rsi_6": row[7], "cci": row[8], "macdh": row[10]}
            s = buy_point.score_candidate(
                code, row[1], row[2], row[3], ind, tmp_datetime, mainline_codes)
            if s:
                scored.append(s)
        print("[选股] 评分完成: %d只" % len(scored))
    except Exception as e:
        print("[选股] 宽候选评分异常:", e)
        traceback.print_exc()

    # 2) 策略A：超买追涨（情绪观察池 + 可能进可买池）
    try:
        print("[选股] 策略A: 超买追涨（情绪观察池）...")
        strategy_a_hits = buy_point.select_strategy_a(tmp_datetime)
        existing = set(c["code"] for c in scored)
        for h in strategy_a_hits:
            code = h["code"]
            ind = {"kdjj": h["kdjj"], "rsi_6": h["rsi_6"], "cci": h["cci"], "macdh": h["macdh"]}
            s = buy_point.score_candidate(
                code, h["name"], h["price"], h["quote_change"], ind, tmp_datetime, mainline_codes)
            if s and s["score"] >= buy_point.BUYABLE_THRESHOLD and code not in existing:
                scored.append(s)
                existing.add(code)
            else:
                pos_note = "位置偏高" if (s and s["position_pct"] > 70) else "未达买点结构"
                sentiment_pool.append({
                    "code": code, "name": h["name"],
                    "price": h["price"],
                    "quote_change": h["quote_change"],
                    "kdjj": h["kdjj"],
                    "rsi_6": h["rsi_6"],
                    "score": s["score"] if s else 0,
                    "note": pos_note,
                })
        print("[选股] 策略A命中: %d只, 情绪观察池: %d只" % (len(strategy_a_hits), len(sentiment_pool)))
    except Exception as e:
        print("[选股] 策略A异常:", e)
        traceback.print_exc()

    # 3) 分数达标但被硬门槛排除的票（位置过高/盈亏比过差）→ 挪到情绪观察池
    excluded = buy_point.excluded_by_gate(scored)
    if excluded:
        sentiment_pool = excluded + sentiment_pool
        print("[选股] 硬门槛排除(高位/低盈亏比): %d只" % len(excluded))

    return scored, sentiment_pool, market_kdj, strategy_a_hits


def generate_report(tmp_datetime, overview, main_line, scored, sentiment_pool, market_kdj, market_state, strategy_a_hits, strategy_b_hits):
    """生成尾盘选股报告"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    is_wknd = is_weekend(tmp_datetime)

    picks = buy_point.pick_buyable(scored, market_state)

    lines = []
    lines.append("# 尾盘选股报告 · %s（%s）\n" % (date_str, weekday))

    if is_wknd:
        lines.append("**休市提示**：今日%s，A股休市，本期为周末复盘。\n" % weekday)
        return "\n".join(lines)

    if overview["total_count"] == 0:
        lines.append("**休市提示**：今日无交易数据（可能为节假日）。\n")
        return "\n".join(lines)

    # ===== 一、大盘概况 =====
    lines.append("## 一、大盘概况\n")
    status = ""
    if overview["avg_change"] > 1:
        status = "强势普涨"
    elif overview["avg_change"] > 0:
        status = "温和偏多"
    elif overview["avg_change"] > -1:
        status = "弱势震荡"
    else:
        status = "单边下行"

    lines.append("全市场 %d 只 | 均涨幅 **%+.2f%%**（%s）" % (
        overview["total_count"], overview["avg_change"], status))
    lines.append("上涨 **%d** / 下跌 %d / 涨停 %d / 跌停 %d" % (
        overview["up_count"], overview["down_count"],
        overview["limit_up"], overview["limit_down"]))
    if overview["total_turnover"] > 0:
        turnover_yi = overview["total_turnover"] / 100000000
        lines.append("两市成交额 **%.0f亿**" % turnover_yi)

    kdj_comment = ""
    if market_kdj["kdjj"] > 80:
        kdj_comment = "超买区"
    elif market_kdj["kdjj"] < 20:
        kdj_comment = "超卖区"
    elif market_kdj["kdjj"] > 60:
        kdj_comment = "偏强"
    elif market_kdj["kdjj"] < 40:
        kdj_comment = "偏弱"
    else:
        kdj_comment = "中性"
    lines.append("KDJ 均值：K=%.1f/D=%.1f/J=%.1f（%s）" % (
        market_kdj["kdjk"], market_kdj["kdjd"], market_kdj["kdjj"], kdj_comment))
    lines.append("市场状态：**%s**" % buy_point.market_label(market_state))
    lines.append("")

    # ===== 二、最强主线 =====
    lines.append("## 二、最强主线\n")
    if main_line["sectors"]:
        sectors_str = "、".join(["%s（%+.1f%%）" % (s["name"], s["change"]) for s in main_line["sectors"][:5]])
        lines.append("板块领涨：%s" % sectors_str)
    if main_line["leaders"]:
        leaders_str = "、".join(["%s（%+.1f%%）" % (get_eastmoney_link(s["code"], s["name"]), s["change"]) for s in main_line["leaders"][:6]])
        lines.append("涨停龙头：%s" % leaders_str)
    if main_line["hot_stocks"]:
        hot_str = "、".join(["%s（%+.1f%%）" % (get_eastmoney_link(s["code"], s["name"]), s["change"]) for s in main_line["hot_stocks"][:8]])
        lines.append("强势股：%s" % hot_str)
    if overview["is_weak"]:
        lines.append("")
        lines.append("**大盘弱势，策略侧重回调低吸，回避追高**")
    lines.append("")

    # ===== 三、可买池（核心）=====
    lines.append("## 三、尾盘可买池（核心·按评分排序）\n")
    lines.append("> 评分维度：位置25 / 趋势20 / 结构25 / 确认20 / 主线10，满分100。入池线 %d 分。\n" % buy_point.BUYABLE_THRESHOLD)
    lines.append("> 市场状态 %s，本日最多推荐 %d 只，**宁缺毋滥**。尾盘优势：用收盘价确认买点结构。\n" % (
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
            lines.append("- 盈亏比 %.2f ｜ 入场：14:30 后确认分时稳定，尾盘介入" % s["rr"])
            lines.append("")
    else:
        lines.append("今日无可买标的（无票达到 %d 分结构），**建议观望**。\n" % buy_point.BUYABLE_THRESHOLD)

    # ===== 四、情绪观察池（仅观察）=====
    lines.append("## 四、情绪观察池（仅观察，不建议买入）\n")
    if sentiment_pool:
        lines.append("> 超买追涨命中票：指标强势但位置偏高/未达买点结构，追高盈亏比差，仅作情绪参考。\n")
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
        lines.append("- 市场强势，仓位可至 **50-60%**，但警惕次日分歧")
    elif market_state == "weak":
        lines.append("- 大盘弱势，**总仓位 ≤ 30%**，仅做试探性建仓，宁缺毋滥")
    else:
        lines.append("- 总仓位 **≤ 50%**，尾盘买入优势在于用收盘价确认趋势")
    lines.append("- 尾盘买入核心：14:30 后观察分时走势稳定再介入")
    lines.append("- 个股止损按买点结构执行：")
    lines.append("  - 平台突破 → 跌破平台下沿")
    lines.append("  - 趋势回踩 → 跌破回踩低点")
    lines.append("  - 分歧转强 → 跌破转强K线低点")
    lines.append("- 规避：冲高回落型、无量涨停、高位连板妖股、盈亏比偏低标的")
    lines.append("")

    # ===== 六、附录：旧版逻辑选股（重构前）=====
    lines.append("## 六、附录：旧版逻辑选股（重构前·硬性阈值）\n")
    lines.append("> 以下为重构前硬性阈值策略的原始命中结果，仅作对比参考，**不作为买入依据**。新逻辑以可买池评分为准。\n")

    # 策略A：超买追涨
    lines.append("### 策略A（超买追涨）\n")
    lines.append("**筛选条件**：KDJ-J≥100、K≥80、D≥70、RSI6>70、MACD柱>0\n")
    if strategy_a_hits:
        names_str = "、".join(["%s（%s，J=%.0f）" % (get_eastmoney_link(h["code"], h["name"]), h["code"], h["kdjj"]) for h in strategy_a_hits[:8]])
        lines.append("今日命中 %d 只：%s\n" % (len(strategy_a_hits), names_str))
        lines.append("**重点个股**\n")
        for h in strategy_a_hits[:3]:
            lines.append("- %s（%s） %.2f元 %+.2f%%" % (get_eastmoney_link(h["code"], h["name"]), h["code"], h["price"], h["quote_change"]))
            lines.append("  KDJ-J=%.0f RSI=%.0f MACD柱=%.2f | 追涨策略，J值拐头即止盈" % (h["kdjj"], h["rsi_6"], h["macdh"]))
        lines.append("")
    else:
        lines.append("今日无超买追涨标的，策略暂缓。\n")

    # 策略B：突破回踩
    lines.append("### 策略B（突破回踩）\n")
    lines.append("**筛选条件**：涨跌幅±2%内、站上60MA、靠近20MA（偏离<2%）、RSI 40-60、CCI -100~100\n")
    if strategy_b_hits:
        names_str = "、".join(["%s（%s）" % (get_eastmoney_link(h["code"], h["name"]), h["code"]) for h in strategy_b_hits[:8]])
        lines.append("今日命中 %d 只：%s\n" % (len(strategy_b_hits), names_str))
        lines.append("**重点个股**\n")
        for h in strategy_b_hits[:3]:
            lines.append("- %s（%s） %.2f元 %+.2f%%" % (get_eastmoney_link(h["code"], h["name"]), h["code"], h["price"], h["quote_change"]))
            lines.append("  20MA=%.2f 60MA=%.2f RSI=%.0f | 回踩确认，放量反弹可介入，跌破60MA止损" % (h["ma20"], h["ma60"], h["rsi_6"]))
        lines.append("")
    else:
        lines.append("今日无突破回踩标的，策略暂缓。\n")

    lines.append("---")
    lines.append("")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。")
    lines.append("> 尾盘操作需结合实时盘面判断，买点评分侧重结构与位置。")
    lines.append("> **股市有风险，投资需谨慎。**")

    return "\n".join(lines)


def save_report(tmp_datetime, report_content):
    date_int = tmp_datetime.strftime("%Y%m%d")
    try:
        del_sql = "DELETE FROM `stock_evening_report` WHERE `date` = '%s'" % date_int
        common.insert(del_sql)
        ins_sql = "INSERT INTO `stock_evening_report` (`date`, `report_content`) VALUES (%s, %s)"
        common.insert(ins_sql, (date_int, report_content))
        print("[保存] MySQL写入成功")
    except Exception as e:
        print("[保存] MySQL异常:", e)
        traceback.print_exc()
    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        file_path = os.path.join(REPORT_DIR, "尾盘选股-%s.md" % date_int)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        print("[保存] 文件:", file_path)
    except Exception as e:
        print("[保存] 文件异常:", e)


def stat_all(tmp_datetime):
    """主入口"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    date_int = tmp_datetime.strftime("%Y%m%d")
    print("=" * 60)
    print("尾盘选股报告 - %s" % date_str)
    print("=" * 60)

    if is_weekend(tmp_datetime):
        report_content = generate_report(tmp_datetime,
            {"total_count": 0}, {"hot_stocks": [], "sectors": [], "leaders": []},
            [], [], {"kdjk": 0, "kdjd": 0, "kdjj": 0}, "normal", [], [])
        save_report(tmp_datetime, report_content)
        print("周末，跳过")
        return

    check = common.select_count("SELECT count(1) FROM stock_zh_ah_name WHERE date='%s'" % date_int)
    if check == 0:
        report_content = generate_report(tmp_datetime,
            {"total_count": 0}, {"hot_stocks": [], "sectors": [], "leaders": []},
            [], [], {"kdjk": 0, "kdjd": 0, "kdjj": 0}, "normal", [], [])
        save_report(tmp_datetime, report_content)
        print("无交易数据")
        return

    # Step 1: 大盘概况
    print("\n[Step1] 大盘概况...")
    overview = get_market_overview(tmp_datetime)
    market_state = buy_point.classify_market(overview)
    print("[Step1] 涨:%d 跌:%d 涨停:%d 均涨幅:%.2f%% 状态:%s" % (
        overview["up_count"], overview["down_count"], overview["limit_up"],
        overview["avg_change"], market_state))

    # Step 2: 锁定最强主线
    print("\n[Step2] 锁定最强主线...")
    main_line = find_main_line(tmp_datetime)
    print("[Step2] 强势股:%d 涨停:%d 板块:%d" % (
        len(main_line["hot_stocks"]), len(main_line["leaders"]), len(main_line["sectors"])))

    # 主线核心代码集合
    mainline_codes = buy_point.get_mainline_codes(tmp_datetime)
    print("[主线] 主线核心代码: %d个" % len(mainline_codes))

    # Step 3: 评分选股
    print("\n[Step3] 评分选股...")
    scored, sentiment_pool, market_kdj, strategy_a_hits = select_candidates(tmp_datetime, overview, mainline_codes)
    print("[Step3] 评分候选:%d 情绪观察池:%d" % (len(scored), len(sentiment_pool)))

    # 旧版策略B（突破回踩）：仅供报告附录对比
    strategy_b_hits = buy_point.select_strategy_b(tmp_datetime)
    print("[Step3] 旧版策略A(超买追涨):%d 策略B(突破回踩):%d" % (len(strategy_a_hits), len(strategy_b_hits)))

    picks = buy_point.pick_buyable(scored, market_state)
    print("[Step3] 可买池: %d只" % len(picks))

    # Step 4: 生成报告
    print("\n[Step4] 生成报告...")
    report_content = generate_report(tmp_datetime, overview, main_line, scored, sentiment_pool, market_kdj, market_state, strategy_a_hits, strategy_b_hits)
    save_report(tmp_datetime, report_content)
    print("\n[完成] 尾盘选股报告生成成功")


# 初始化建表
ensure_table()


if __name__ == '__main__':
    tmp_datetime = common.run_with_args(stat_all)
