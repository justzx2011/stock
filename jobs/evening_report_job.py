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


def ensure_table():
    """确保报告表存在"""
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

    # 从当日数据中分析涨幅集中度（按代码前缀分行业近似）
    try:
        print("[主线] 分析当日强势股...")
        # 涨幅>5%的活跃股
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

    # 涨停股
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

    # 尝试akshare板块API
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


def select_candidates(tmp_datetime, overview, main_line):
    """从最强主线中筛选尾盘买入候选股"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    strategy_a = []  # 超买追涨
    strategy_b = []  # 突破回踩
    market_kdj = {"kdjk": 0, "kdjd": 0, "kdjj": 0}

    # 计算大盘KDJ均值
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

    # 如果大盘弱势，侧重策略B
    if overview["is_weak"]:
        print("[选股] 大盘弱势(avg=%.2f)，侧重策略B" % overview["avg_change"])

    # 策略A: 超买追涨 - 从涨幅>5%的强势股中筛选技术指标符合的
    try:
        print("[选股] 策略A: 超买追涨...")
        sql_a = """
            SELECT g.date, g.code, g.name, g.latest_price, g.quote_change,
                   g.volume, g.turnover, g.kdjk, g.kdjd, g.kdjj, g.rsi_6, g.cci,
                   g.macd, g.macdh, g.macds
            FROM stock_data.guess_indicators_daily g
            WHERE g.date = '%s'
                AND g.kdjj >= 100 AND g.kdjk >= 80 AND g.kdjd >= 70
                AND g.rsi_6 > 70
                AND g.quote_change > 1
                AND g.macdh > 0
            ORDER BY g.kdjj DESC
            LIMIT 15
        """ % datetime_int
        rows = common.select(sql_a)
        if rows:
            for row in rows:
                strategy_a.append({
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
                })
        print("[选股] 策略A: %d只" % len(strategy_a))
    except Exception as e:
        print("[选股] 策略A异常:", e)
        traceback.print_exc()

    # 策略B: 突破回踩 - 涨跌幅-2%~2%，站上60MA，靠近20MA
    try:
        print("[选股] 策略B: 突破回踩...")
        sql_b = """
            SELECT g.date, g.code, g.name, g.latest_price, g.quote_change,
                   g.volume, g.turnover, g.kdjk, g.kdjd, g.kdjj, g.rsi_6, g.cci
            FROM stock_data.guess_indicators_daily g
            WHERE g.date = '%s'
                AND g.quote_change BETWEEN -2 AND 2
                AND g.rsi_6 BETWEEN 40 AND 60
                AND g.volume > 0
            ORDER BY ABS(g.quote_change) ASC
            LIMIT 40
        """ % datetime_int
        rows = common.select(sql_b)
        if rows:
            print("[选股] 策略B初筛: %d只, K线验证中..." % len(rows))
            for row in rows:
                code = row[1]
                try:
                    date_end = tmp_datetime.strftime("%Y-%m-%d")
                    date_start = (tmp_datetime + datetime.timedelta(days=-100)).strftime("%Y-%m-%d")
                    stock_data = common.get_hist_data_cache(code, date_start, date_end)
                    if stock_data is None or stock_data.empty:
                        continue

                    stock_stat = stockstats.StockDataFrame.retype(stock_data)
                    close_20_sma = float(stock_stat["close_20_sma"].iloc[-1])
                    close_60_sma = float(stock_stat["close_60_sma"].iloc[-1])
                    current_price = float(row[3]) if row[3] else 0

                    if close_60_sma > 0 and close_20_sma > 0 and current_price > 0:
                        above_60ma = current_price > close_60_sma
                        near_20ma = abs(current_price - close_20_sma) / close_20_sma < 0.02

                        if above_60ma and near_20ma:
                            # 检查成交量是否放大（与前一日比）
                            vol_series = stock_stat["volume"]
                            vol_today = vol_series.iloc[-1] if len(vol_series) > 0 else 0
                            vol_yesterday = vol_series.iloc[-2] if len(vol_series) > 1 else 0
                            vol_expanding = vol_today > vol_yesterday if vol_yesterday > 0 else True

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
                                "ma60": round(close_60_sma, 2),
                                "vol_expanding": vol_expanding
                            })
                            if len(strategy_b) >= 10:
                                break
                except Exception as e:
                    pass  # 静默跳过单只失败
            print("[选股] 策略B: %d只" % len(strategy_b))
    except Exception as e:
        print("[选股] 策略B异常:", e)

    return strategy_a, strategy_b, market_kdj


def pick_top5(strategy_a, strategy_b, overview, main_line):
    """从两个策略中选出最终5只推荐"""
    picks = []

    # 从策略A中选（超买追涨），大盘弱势时少选
    max_a = 2 if overview["is_weak"] else 3
    for s in strategy_a[:max_a]:
        s["strategy"] = "超买追涨"
        picks.append(s)

    # 从策略B中选（突破回踩），补齐到5只
    remaining = 5 - len(picks)
    for s in strategy_b[:remaining]:
        s["strategy"] = "突破回踩"
        picks.append(s)

    # 如果还不够5只，从涨停龙头中补
    if len(picks) < 5 and main_line["leaders"]:
        existing_codes = set(p["code"] for p in picks)
        for leader in main_line["leaders"]:
            if leader["code"] not in existing_codes and len(picks) < 5:
                picks.append({
                    "code": leader["code"], "name": leader["name"],
                    "latest_price": leader["price"],
                    "quote_change": leader["change"],
                    "strategy": "涨停龙头",
                    "kdjj": 0, "rsi_6": 0, "macdh": 0,
                    "note": "涨停板强势股，追高风险较大"
                })

    return picks


def generate_report(tmp_datetime, overview, main_line, picks, market_kdj):
    """生成尾盘选股报告"""
    date_str = tmp_datetime.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[tmp_datetime.weekday()]
    is_wknd = is_weekend(tmp_datetime)
    now_str = datetime.datetime.now().strftime("%H:%M")

    lines = []

    # ===== 标题 =====
    lines.append("# 尾盘选股报告 · %s（%s）\n" % (date_str, weekday))

    # ===== 休市提示 =====
    if is_wknd:
        lines.append("**休市提示**：今日%s，A股休市，本期为周末复盘。\n" % weekday)
        return "\n".join(lines)

    if overview["total_count"] == 0:
        lines.append("**休市提示**：今日无交易数据（可能为节假日）。\n")
        return "\n".join(lines)

    # ===== 一、大盘概况 =====
    lines.append("## 一、大盘概况\n")

    # 涨跌家数
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

    # KDJ
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
    lines.append("")

    # ===== 二、最强主线 =====
    lines.append("## 二、最强主线\n")

    if main_line["sectors"]:
        sectors_str = "、".join(["%s（%+.1f%%）" % (s["name"], s["change"]) for s in main_line["sectors"][:5]])
        lines.append("板块领涨：%s" % sectors_str)

    if main_line["leaders"]:
        leaders_str = "、".join(["%s（%+.1f%%）" % (s["name"], s["change"]) for s in main_line["leaders"][:6]])
        lines.append("涨停龙头：%s" % leaders_str)

    if main_line["hot_stocks"]:
        hot_str = "、".join(["%s（%+.1f%%）" % (s["name"], s["change"]) for s in main_line["hot_stocks"][:8]])
        lines.append("强势股：%s" % hot_str)

    if overview["is_weak"]:
        lines.append("")
        lines.append("**大盘弱势，策略侧重回调低吸，回避追高**")

    lines.append("")

    # ===== 三、尾盘买入推荐 =====
    lines.append("## 三、尾盘买入推荐（5只）\n")

    if not picks:
        lines.append("今日无符合条件的标的，建议观望。\n")
    else:
        for i, s in enumerate(picks, 1):
            strategy = s.get("strategy", "未知")
            lines.append("### %d. %s（%s）— %s\n" % (i, s["name"], s["code"], strategy))
            lines.append("- 现价 **%.2f元** %+.2f%%" % (s["latest_price"], s["quote_change"]))

            if strategy == "超买追涨":
                lines.append("- KDJ-J=%.0f RSI=%.0f MACD柱=%.2f" % (
                    s.get("kdjj", 0), s.get("rsi_6", 0), s.get("macdh", 0)))
                lines.append("- 入场：尾盘确认J值维持100上方，MACD柱持续放大")
                lines.append("- 止损：J值拐头向下或跌破5日线，止损位 %.2f" % (s["latest_price"] * 0.95))
            elif strategy == "突破回踩":
                lines.append("- 20MA=%.2f 60MA=%.2f RSI=%.0f" % (
                    s.get("ma20", 0), s.get("ma60", 0), s.get("rsi_6", 0)))
                vol_hint = "放量" if s.get("vol_expanding") else "缩量"
                lines.append("- 量能：%s | 入场：尾盘确认站稳20MA，放量反弹" % vol_hint)
                lines.append("- 止损：跌破60MA（%.2f）" % s.get("ma60", 0))
            elif strategy == "涨停龙头":
                lines.append("- 涨停板强势股，追高风险较大")
                lines.append("- 入场：仅限尾盘封板确认，次日高开即走")
                lines.append("- 止损：次日低开 -3% 即止损")
            lines.append("")

    # ===== 四、风控建议 =====
    lines.append("## 四、风控建议\n")

    if overview["is_weak"]:
        lines.append("- 大盘弱势，**总仓位 ≤ 30%**，仅做试探性建仓")
    elif overview["avg_change"] > 1:
        lines.append("- 市场强势，仓位可至 **50-60%**，但警惕次日分歧")
    else:
        lines.append("- 总仓位 **≤ 50%**，尾盘买入优势在于用收盘价确认趋势")

    lines.append("- 尾盘买入核心：14:30 后观察分时走势稳定再介入")
    lines.append("- 止损纪律：单票 **-3%** 或跌破关键均线强制止损")
    lines.append("- 规避：冲高回落型、无量涨停、高位连板妖股")
    lines.append("")

    # ===== 免责声明 =====
    lines.append("---")
    lines.append("")
    lines.append("> 本报告基于历史数据和技术指标自动生成，不构成投资建议。")
    lines.append("> 尾盘操作需结合实时盘面判断，技术指标存在滞后性。")
    lines.append("> **股市有风险，投资需谨慎。**")

    return "\n".join(lines)


def save_report(tmp_datetime, report_content):
    """保存报告到MySQL和文件"""
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
            [], {"kdjk": 0, "kdjd": 0, "kdjj": 0})
        save_report(tmp_datetime, report_content)
        print("周末，跳过")
        return

    # 检查数据
    check = common.select_count("SELECT count(1) FROM stock_zh_ah_name WHERE date='%s'" % date_int)
    if check == 0:
        report_content = generate_report(tmp_datetime,
            {"total_count": 0}, {"hot_stocks": [], "sectors": [], "leaders": []},
            [], {"kdjk": 0, "kdjd": 0, "kdjj": 0})
        save_report(tmp_datetime, report_content)
        print("无交易数据")
        return

    # Step 1: 大盘概况
    print("\n[Step1] 大盘概况...")
    overview = get_market_overview(tmp_datetime)
    print("[Step1] 涨:%d 跌:%d 涨停:%d 均涨幅:%.2f%%" % (
        overview["up_count"], overview["down_count"], overview["limit_up"], overview["avg_change"]))

    # Step 2: 锁定最强主线
    print("\n[Step2] 锁定最强主线...")
    main_line = find_main_line(tmp_datetime)
    print("[Step2] 强势股:%d 涨停:%d 板块:%d" % (
        len(main_line["hot_stocks"]), len(main_line["leaders"]), len(main_line["sectors"])))

    # Step 3: 选股
    print("\n[Step3] 策略选股...")
    strategy_a, strategy_b, market_kdj = select_candidates(tmp_datetime, overview, main_line)
    print("[Step3] 策略A:%d 策略B:%d" % (len(strategy_a), len(strategy_b)))

    # Step 4: 选出TOP5
    picks = pick_top5(strategy_a, strategy_b, overview, main_line)
    print("[Step4] 最终推荐: %d只" % len(picks))

    # Step 5: 生成报告
    print("\n[Step5] 生成报告...")
    report_content = generate_report(tmp_datetime, overview, main_line, picks, market_kdj)
    save_report(tmp_datetime, report_content)
    print("\n[完成] 尾盘选股报告生成成功")


# 初始化建表
ensure_table()

if __name__ == '__main__':
    tmp_datetime = common.run_with_args(stat_all)
