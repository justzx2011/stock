#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

"""
买点评分模块（可买池核心逻辑）
================================

设计目标：把选股从「强势股雷达」改成「以实战买点为中心的评分系统」。
不再用二元过滤（KDJ>=100 之类的硬阈值）当主推荐条件，而是对每只候选票按
位置 / 趋势 / 结构 / 确认 / 主线 五个维度打分，按分排序入池，并给出
对应买点类型的结构化止损位。

本模块被 jobs/morning_report_job.py 与 jobs/evening_report_job.py 共用，
避免两份报告各写一套逻辑。
"""

import datetime
import numpy as np
import libs.common as common

# ============================================================================
# 可调参数（经验值，后续可按回测结果调整）
# ============================================================================
BUYABLE_THRESHOLD = 55          # 入池分数线（满分 100）
POSITION_BUCKETS = (0.30, 0.50, 0.70, 0.85)  # 60日区间位置分档
PULLBACK_HEALTHY = (0.03, 0.15)  # 健康回踩深度区间
PULLBACK_TOO_DEEP = 0.20          # 超过该值视为趋势走坏
VOL_SHRINK_RATIO = 0.80           # 回踩缩量阈值（近3日 < 之前5日 * 该值）
VOL_EXPAND_RATIO = 1.20           # 放量阈值（今日 > 昨日 * 该值）
NEAR_20MA_TOL = 0.03              # 靠近20MA容忍度（3%）
RR_WARN = 1.5                     # 盈亏比低于该值标记为偏低（仍可入池，仅警告）
RR_FLOOR = 1.0                    # 盈亏比硬底线：低于该值不进可买池（下行风险>上行）


# ============================================================================
# 市场状态
# ============================================================================
def classify_market(overview):
    """根据大盘概况判定市场状态：weak / normal / strong

    overview 需含: avg_change, up_count, down_count, is_weak
    """
    try:
        if overview.get("is_weak") or overview["avg_change"] < -1:
            return "weak"
        if overview["avg_change"] > 1 and overview["up_count"] > overview["down_count"] * 2:
            return "strong"
    except Exception:
        pass
    return "normal"


def max_picks(state):
    """不同市场状态下的最大推荐数：弱市宁缺毋滥"""
    return {"weak": 2, "normal": 3, "strong": 5}.get(state, 3)


def market_label(state):
    return {
        "weak": "弱势（宁缺毋滥，≤2只）",
        "normal": "中性（≤3只）",
        "strong": "强势（≤5只）",
    }.get(state, "中性")


# ============================================================================
# 主线核心代码集合
# ============================================================================
def get_mainline_codes(tmp_datetime):
    """当日主线核心股票代码集合：涨幅>=5 或 涨停。

    用于给候选票加「主线」维度分数。没有 stock→sector 映射，
    这里用当日强势股集合做近似，比完全不看主线强。
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    codes = set()
    try:
        sql = ("SELECT code FROM stock_zh_ah_name "
               "WHERE date = '%s' AND quote_change >= 5 AND latest_price > 0") % datetime_int
        for r in common.select(sql) or []:
            codes.add(str(r[0]))
    except Exception as e:
        print("[主线] 获取异常:", e)
    return codes


# ============================================================================
# 工具
# ============================================================================
def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except Exception:
        return default


# ============================================================================
# 评分核心
# ============================================================================
def score_candidate(code, name, price, quote_change, ind_row,
                    tmp_datetime, mainline_codes):
    """对单只股票做买点评分。

    参数:
        code/name/price/quote_change: 基础信息
        ind_row: guess_indicators_daily 的字段字典（至少含 kdjj/rsi_6/cci/macdh，用于展示）
        tmp_datetime: 评分基准日
        mainline_codes: 主线核心代码集合

    返回:
        评分字典（含 score/bp_type/stop_price/rr 等）或 None（数据不足）
    """
    price = _safe_float(price)
    if price <= 0:
        return None

    # 取 100 天日 K（缓存命中时很快）
    date_end = tmp_datetime.strftime("%Y-%m-%d")
    date_start = (tmp_datetime + datetime.timedelta(days=-100)).strftime("%Y-%m-%d")
    try:
        kdf = common.get_hist_data_cache(code, date_start, date_end)
    except Exception:
        return None
    if kdf is None or kdf.empty or len(kdf) < 20:
        return None

    try:
        kdf = kdf.sort_index()
        close = kdf["close"].astype(float)
        high = kdf["high"].astype(float)
        low = kdf["low"].astype(float)
        vol = kdf["volume"].astype(float)
        op = kdf["open"].astype(float)

        cur = float(close.iloc[-1])
        if cur <= 0:
            return None
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else cur

        # ---- 均线 ----
        ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1]) if len(close) >= 20 else float(close.mean())
        ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else ma20
        ma20_5ago = float(close.rolling(20, min_periods=10).mean().iloc[-6]) if len(close) >= 26 else ma20

        # ---- 位置（60日区间内分位）----
        lb = min(len(close), 60)
        high_60 = float(high.iloc[-lb:].max())
        low_60 = float(low.iloc[-lb:].min())
        rng = high_60 - low_60
        position_pct = (cur - low_60) / rng if rng > 0 else 0.5
        dist_from_high = (cur - high_60) / high_60 if high_60 > 0 else 0.0

        # ---- 回踩深度（近20日最高到当前）----
        lb20 = min(len(high), 20)
        peak_recent = float(high.iloc[-lb20:].max())
        pullback_depth = (peak_recent - cur) / peak_recent if peak_recent > 0 else 0.0

        # ---- 量能 ----
        vol_recent_3 = float(vol.iloc[-3:].mean()) if len(vol) >= 3 else float(vol.mean())
        vol_prior_5 = float(vol.iloc[-8:-3].mean()) if len(vol) >= 8 else vol_recent_3
        vol_shrink = (vol_prior_5 > 0 and vol_recent_3 < vol_prior_5 * VOL_SHRINK_RATIO)
        vol_today = float(vol.iloc[-1])
        vol_yest = float(vol.iloc[-2]) if len(vol) >= 2 else vol_today
        vol_expand = (vol_yest > 0 and vol_today > vol_yest * VOL_EXPAND_RATIO)

        # ---- 当日转强信号 ----
        today_strong = (len(close) >= 2 and cur > float(op.iloc[-1]) and cur > prev_close)
        stop_falling = (len(low) >= 2 and float(low.iloc[-1]) > float(low.iloc[-2]))

        # ---- 平台突破：今日收盘 > 过去20日（不含今日）最高 ----
        if len(high) >= 21:
            platform_high = float(high.iloc[-21:-1].max())
            broke_platform = cur > platform_high
        else:
            broke_platform = False

        # ============ 评分 ============
        # 1) 位置 25 分
        if position_pct <= POSITION_BUCKETS[0]:
            pos_s = 25
        elif position_pct <= POSITION_BUCKETS[1]:
            pos_s = 18
        elif position_pct <= POSITION_BUCKETS[2]:
            pos_s = 12
        elif position_pct <= POSITION_BUCKETS[3]:
            pos_s = 6
        else:
            pos_s = 2

        # 2) 趋势 20 分
        ma20_above_ma60 = ma20 > ma60
        ma20_rising = ma20 > ma20_5ago
        if ma20_above_ma60 and ma20_rising:
            tr_s = 20
        elif ma20_above_ma60:
            tr_s = 12
        else:
            tr_s = 4

        # 3) 结构 25 分
        if pullback_depth > PULLBACK_TOO_DEEP:
            st_s = 0                       # 回踩过深，趋势可能走坏
        elif broke_platform and vol_expand:
            st_s = 22                      # 平台突破 + 放量
        elif PULLBACK_HEALTHY[0] <= pullback_depth <= PULLBACK_HEALTHY[1] and vol_shrink:
            st_s = 25                      # 健康回踩 + 缩量（最佳买点）
        elif PULLBACK_HEALTHY[0] <= pullback_depth <= PULLBACK_HEALTHY[1]:
            st_s = 16                      # 健康回踩但未缩量
        elif broke_platform:
            st_s = 12
        elif pullback_depth < PULLBACK_HEALTHY[0]:
            st_s = 8                       # 没有像样回踩，偏追高
        else:
            st_s = 10

        # 4) 确认 20 分（当日转强信号，可叠加）
        cf_s = 0
        if today_strong:
            cf_s += 10
        if vol_expand:
            cf_s += 5
        if stop_falling:
            cf_s += 5

        # 5) 主线 10 分
        in_ml = code in mainline_codes
        ml_s = 10 if in_ml else 0

        total = pos_s + tr_s + st_s + cf_s + ml_s

        # ---- 买点类型判定 ----
        near_20ma = (ma20 > 0 and abs(cur - ma20) / ma20 < NEAR_20MA_TOL)
        if broke_platform and vol_expand:
            bp_type = "平台突破"
        elif pullback_depth >= PULLBACK_HEALTHY[0] and near_20ma and ma20_above_ma60:
            bp_type = "趋势回踩"
        elif today_strong and pullback_depth >= PULLBACK_HEALTHY[0]:
            bp_type = "分歧转强"
        elif near_20ma and ma20_above_ma60:
            bp_type = "趋势回踩"
        else:
            bp_type = "分歧转强"

        # ---- 结构化止损（按买点类型）----
        if bp_type == "平台突破":
            stop_price = float(low.iloc[-20:].min()) if len(low) >= 20 else float(low.min())
        elif bp_type == "趋势回踩":
            stop_price = float(low.iloc[-5:].min()) * 0.99 if len(low) >= 5 else float(low.min()) * 0.99
        else:  # 分歧转强
            stop_price = float(low.iloc[-3:].min()) * 0.99 if len(low) >= 3 else float(low.min()) * 0.99

        # ---- 盈亏比 ----
        reward = high_60 - cur
        risk = cur - stop_price
        rr = reward / risk if risk > 0 else 0.0

        return {
            "code": code, "name": name,
            "price": round(cur, 2),
            "quote_change": round(_safe_float(quote_change), 2),
            "score": total,
            "bp_type": bp_type,
            "position_pct": round(position_pct * 100, 1),
            "dist_from_high": round(dist_from_high * 100, 1),
            "pullback_depth": round(pullback_depth * 100, 1),
            "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "trend_up": bool(ma20_above_ma60 and ma20_rising),
            "vol_shrink": bool(vol_shrink),
            "vol_expand": bool(vol_expand),
            "today_strong": bool(today_strong),
            "stop_falling": bool(stop_falling),
            "broke_platform": bool(broke_platform),
            "stop_price": round(stop_price, 2),
            "rr": round(rr, 2),
            "rr_ok": rr >= RR_WARN,
            "in_mainline": bool(in_ml),
            "kdjj": round(_safe_float(ind_row.get("kdjj")), 0) if ind_row else 0,
            "rsi_6": round(_safe_float(ind_row.get("rsi_6")), 0) if ind_row else 0,
            "cci": round(_safe_float(ind_row.get("cci")), 0) if ind_row else 0,
            "macdh": round(_safe_float(ind_row.get("macdh")), 2) if ind_row else 0,
        }
    except Exception as e:
        print("[评分异常]", code, e)
        return None


# ============================================================================
# 选出可买池
# ============================================================================
def is_buyable(c):
    """硬性买点门槛：位置过高或盈亏比过差直接排除。

    这类票看着强但不适合买（追高/盈亏比差），不进可买池，
    由 job 挪到情绪观察池并标注原因，保持透明。
    """
    if c.get("position_pct", 100) >= 88:
        return False
    if c.get("rr", 0) < RR_FLOOR:
        return False
    return True


def excluded_by_gate(scored_candidates, threshold=BUYABLE_THRESHOLD):
    """分数达标但被硬门槛排除的候选（位置过高/盈亏比过差）。

    供 job 把它们挪到情绪观察池，标注原因，保持透明。
    """
    out = []
    for c in scored_candidates:
        if not c or c["score"] < threshold:
            continue
        if not is_buyable(c):
            if c.get("position_pct", 0) >= 88:
                reason = "位置过高(60日高位)，追高风险大"
            else:
                reason = "盈亏比过低(%.2f)，不建议追" % c.get("rr", 0)
            out.append({
                "code": c["code"], "name": c["name"],
                "price": c["price"], "quote_change": c["quote_change"],
                "kdjj": c["kdjj"], "rsi_6": c["rsi_6"],
                "score": c["score"], "note": reason,
            })
    return out


def pick_buyable(scored_candidates, state, threshold=BUYABLE_THRESHOLD):
    """从评分结果中选出可买池。

    - 只取 score >= threshold 且通过 is_buyable 硬门槛的
    - 按分排序
    - 按市场状态截断（弱2 / 正常3 / 强5）
    - 简单多样化：同一买点类型不超过 n//2+1 只，避免全是同一种结构
    """
    pool = [c for c in scored_candidates
            if c and c["score"] >= threshold and is_buyable(c)]
    pool.sort(key=lambda c: c["score"], reverse=True)

    n = max_picks(state)
    type_cap = n // 2 + 1
    picks = []
    seen = set()
    type_count = {}

    # 第一轮：按类型配额挑高分
    for c in pool:
        if len(picks) >= n:
            break
        if c["code"] in seen:
            continue
        t = c["bp_type"]
        if type_count.get(t, 0) >= type_cap:
            continue
        picks.append(c)
        seen.add(c["code"])
        type_count[t] = type_count.get(t, 0) + 1

    # 第二轮：补足剩余名额（不卡类型）
    for c in pool:
        if len(picks) >= n:
            break
        if c["code"] in seen:
            continue
        picks.append(c)
        seen.add(c["code"])

    return picks


# ============================================================================
# 旧版策略（重构前硬性阈值，仅供报告附录对比，不参与可买池推荐）
# ============================================================================
def select_strategy_a(tmp_datetime, limit=20):
    """旧版策略A（超买追涨）：硬性阈值筛选，满足即进。

    条件：KDJ-J≥100、K≥80、D≥70、RSI6>70、MACD柱>0
    返回命中票列表（按 J 降序），每项含 code/name/price/quote_change
    及 kdjk/kdjd/kdjj/rsi_6/cci/macdh。
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macd`,`macdh`,`macds`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s
            AND kdjj >= 100 AND kdjk >= 80 AND kdjd >= 70
            AND rsi_6 > 70 AND macdh > 0
        ORDER BY kdjj DESC
        LIMIT %s
    """
    try:
        rows = common.select(sql, (datetime_int, limit)) or []
    except Exception as e:
        print("[策略A] 查询异常:", e)
        return []
    hits = []
    for row in rows:
        hits.append({
            "code": str(row[0]), "name": row[1],
            "price": _safe_float(row[2]),
            "quote_change": _safe_float(row[3]),
            "kdjk": _safe_float(row[4]),
            "kdjd": _safe_float(row[5]),
            "kdjj": _safe_float(row[6]),
            "rsi_6": _safe_float(row[7]),
            "cci": _safe_float(row[8]),
            "macdh": _safe_float(row[10]),
        })
    return hits


def select_strategy_b(tmp_datetime, limit=10):
    """旧版策略B（突破回踩）：硬性阈值筛选，满足即进。

    条件：涨跌幅±2%内、RSI 40-60、CCI -100~100、站上60MA、靠近20MA（偏离<2%）。
    前3项走 SQL 预筛（取30只），再逐只做K线均线验证。
    返回命中票列表（按 |涨跌幅| 升序），每项含 code/name/price/quote_change
    及 kdjj/rsi_6/cci/ma20/ma60。
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
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
    try:
        rows = common.select(sql, (datetime_int,)) or []
    except Exception as e:
        print("[策略B] 查询异常:", e)
        return []

    date_end = tmp_datetime.strftime("%Y-%m-%d")
    date_start = (tmp_datetime + datetime.timedelta(days=-100)).strftime("%Y-%m-%d")
    hits = []
    for row in rows:
        code = str(row[0])
        try:
            kdf = common.get_hist_data_cache(code, date_start, date_end)
            if kdf is None or kdf.empty or len(kdf) < 20:
                continue
            kdf = kdf.sort_index()
            close = kdf["close"].astype(float)
            cur = float(close.iloc[-1])
            ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1]) if len(close) >= 20 else float(close.mean())
            ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else ma20
            if ma60 > 0 and ma20 > 0 and cur > 0:
                above_60ma = cur > ma60
                near_20ma = abs(cur - ma20) / ma20 < 0.02
                if above_60ma and near_20ma:
                    hits.append({
                        "code": code, "name": row[1],
                        "price": _safe_float(row[2]),
                        "quote_change": _safe_float(row[3]),
                        "kdjj": _safe_float(row[6]),
                        "rsi_6": _safe_float(row[7]),
                        "cci": _safe_float(row[8]),
                        "ma20": round(ma20, 2),
                        "ma60": round(ma60, 2),
                    })
                    if len(hits) >= limit:
                        break
        except Exception as e:
            print("[策略B] 处理异常:", code, e)
    return hits


# ============================================================================
# 魅视科技式选股
# ============================================================================
def get_semiconductor_mainline_codes(tmp_datetime):
    """获取半导体主线核心股票代码集合。

    通过查询涨幅>=5且名称或概念包含"半导体"、"芯片"、"集成电路"、
    "IC"、"晶圆"、"封测"、"光刻机"等关键词的股票。
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    codes = set()
    try:
        # 查询涨幅>=5的股票，通过名称筛选半导体相关
        sql = ("SELECT code, name FROM stock_zh_ah_name "
               "WHERE date = '%s' AND quote_change >= 5 AND latest_price > 0") % datetime_int
        for r in common.select(sql) or []:
            code = str(r[0])
            name = str(r[1]) if r[1] else ""
            # 半导体相关关键词
            keywords = ["半导体", "芯片", "集成电路", "IC", "晶圆", "封测", "光刻机",
                       "半导体材料", "半导体设备", "功率半导体", "第三代半导体"]
            if any(kw in name for kw in keywords):
                codes.add(code)
        # 也加入所有主线强势股（作为补充）
        mainline_all = get_mainline_codes(tmp_datetime)
        codes.update(mainline_all)
    except Exception as e:
        print("[半导体主线] 获取异常:", e)
    return codes


def check_consecutive_days_in_pool(code, tmp_datetime, min_days=3):
    """检查股票是否连续多日在策略A候选池中。

    返回连续在池的天数。
    """
    consecutive_days = 0
    # 从当天往前查
    for i in range(0, 10):  # 最多查10天
        check_date = tmp_datetime + datetime.timedelta(days=-i)
        datetime_int = check_date.strftime("%Y%m%d")
        # 检查当天是否在策略A命中池中
        try:
            sql = """
                SELECT count(1)
                FROM stock_data.guess_indicators_daily
                WHERE `date` = %s
                    AND code = %s
                    AND kdjj >= 100 AND kdjk >= 80 AND kdjd >= 70
                    AND rsi_6 > 70 AND macdh > 0
            """
            count = common.select_count(sql, (datetime_int, code))
            if count > 0:
                consecutive_days += 1
            else:
                # 如果某天不在，就停止（只算连续的）
                if consecutive_days > 0:
                    break
        except Exception as e:
            print("[连续在池检查] 异常:", code, check_date, e)
            break
    return consecutive_days


def select_meishi_tech(tmp_datetime):
    """魅视科技式选股：叠加半导体主线 + D≥70 确认 + 连续多日在池。

    从"策略A∩lite买入"候选里选出推荐的票。

    条件：
    1. 属于半导体主线（涨幅>=5且名称含半导体相关关键词，或主线强势股）
    2. KDJ-D ≥ 70
    3. 连续多日（至少3天）在策略A候选池中
    4. 来自"策略A∩lite买入"（即策略A命中且满足一定的lite条件）
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")

    # 1. 获取半导体主线代码
    semi_mainline = get_semiconductor_mainline_codes(tmp_datetime)
    print("[魅视科技] 半导体主线股票数:", len(semi_mainline))

    # 2. 从策略A命中中筛选：D≥70 + 半导体主线 + 连续多日在池
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macd`,`macdh`,`macds`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s
            AND kdjj >= 100 AND kdjk >= 80 AND kdjd >= 70  -- 策略A条件
            AND rsi_6 > 70 AND macdh > 0
            AND volume > 0
        ORDER BY kdjj DESC
    """
    try:
        rows = common.select(sql, (datetime_int,)) or []
    except Exception as e:
        print("[魅视科技] 查询异常:", e)
        return []

    hits = []
    for row in rows:
        code = str(row[0])
        kdjd = _safe_float(row[5])

        # 条件1: D≥70（已经在SQL里过滤了，但再确认一次）
        if kdjd < 70:
            continue

        # 条件2: 属于半导体主线
        if code not in semi_mainline:
            continue

        # 条件3: 连续多日在池（至少3天）
        consecutive_days = check_consecutive_days_in_pool(code, tmp_datetime, min_days=3)
        if consecutive_days < 3:
            continue

        # 加入结果
        hits.append({
            "code": code, "name": row[1],
            "price": _safe_float(row[2]),
            "quote_change": _safe_float(row[3]),
            "kdjk": _safe_float(row[4]),
            "kdjd": kdjd,
            "kdjj": _safe_float(row[6]),
            "rsi_6": _safe_float(row[7]),
            "cci": _safe_float(row[8]),
            "macdh": _safe_float(row[10]),
            "consecutive_days": consecutive_days,
            "in_semiconductor_mainline": True,
        })

    print("[魅视科技] 命中数:", len(hits))
    return hits


def select_hsc_style(tmp_datetime):
    """华盛昌风格选股：深度回调后企稳反弹。

    借鉴华盛昌(002980)的走势特征：
    1. 经历较深回调（8-20%）
    2. 近期开始反弹（5日涨幅2-15%）
    3. 量能萎缩或稳定
    4. 60日位置适中（30-70%）
    5. 中期均线多头排列（MA20 > MA60）
    6. 大跌后企稳
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    print("[华盛昌风格] 选股日期:", datetime_int)

    # 1. 获取主线股票（加分项）
    mainline_codes = get_mainline_codes(tmp_datetime)
    print("[华盛昌风格] 主线股票数:", len(mainline_codes))

    # 2. 从技术指标初筛
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macdh`,
               `volume`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s
            AND latest_price > 0
            AND volume > 0
        ORDER BY volume DESC
        LIMIT 300
    """
    try:
        rows = common.select(sql, (datetime_int,)) or []
    except Exception as e:
        print("[华盛昌风格] 查询异常:", e)
        return []

    print("[华盛昌风格] 初筛候选数:", len(rows))

    candidates = []
    for row in rows:
        code = str(row[0])
        name = row[1] if row[1] else ""

        try:
            # 获取K线数据
            date_end = tmp_datetime.strftime("%Y-%m-%d")
            date_start = (tmp_datetime + datetime.timedelta(days=-60)).strftime("%Y-%m-%d")
            kdf = common.get_hist_data_cache(code, date_start, date_end)
            if kdf is None or kdf.empty or len(kdf) < 20:
                continue

            kdf = kdf.sort_index()
            close = kdf["close"].astype(float)
            high = kdf["high"].astype(float)
            low = kdf["low"].astype(float)
            vol = kdf["volume"].astype(float)

            cur = float(close.iloc[-1])

            # 计算均线
            ma5 = float(close.rolling(5, min_periods=3).mean().iloc[-1])
            ma10 = float(close.rolling(10, min_periods=5).mean().iloc[-1])
            ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1])
            ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else ma20

            # 计算趋势
            trend_5 = (cur - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) >= 6 else 0
            trend_10 = (cur - float(close.iloc[-11])) / float(close.iloc[-11]) * 100 if len(close) >= 11 else 0
            trend_20 = (cur - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) >= 21 else 0

            # 计算回调深度（从20日高点）
            peak_20 = float(high.iloc[-20:].max())
            pullback_depth = (peak_20 - cur) / peak_20 * 100 if peak_20 > 0 else 0

            # 量能分析
            vol_5 = float(vol.iloc[-5:].mean())
            vol_10 = float(vol.iloc[-10:-5].mean()) if len(vol) >= 10 else vol_5
            vol_ratio = vol_5 / vol_10 if vol_10 > 0 else 1.0

            # 60日位置
            lb = min(len(close), 60)
            high_60 = float(high.iloc[-lb:].max())
            low_60 = float(low.iloc[-lb:].min())
            rng = high_60 - low_60
            position_pct = (cur - low_60) / rng * 100 if rng > 0 else 50

            # 检查10日内是否有大跌（单日跌幅超过8%）
            has_big_drop = False
            max_drop_10 = 0
            for i in range(min(10, len(close) - 1)):
                idx = -(i + 1)
                day_close = float(close.iloc[idx])
                day_high = float(high.iloc[idx - 1]) if idx - 1 >= -len(close) else day_close
                drop = (day_high - day_close) / day_high * 100
                if drop > max_drop_10:
                    max_drop_10 = drop
                if drop > 8:
                    has_big_drop = True

            # 检查是否企稳（近3日低点不创新低）
            stabilized = False
            if len(close) >= 5:
                recent_low = float(low.iloc[-3:].min())
                prev_low = float(low.iloc[-5:-2].min())
                stabilized = recent_low >= prev_low * 0.98

            # 中期均线多头
            midterm_bullish = ma20 > ma60 * 0.99

            # 评分系统
            score = 0
            if 8 <= pullback_depth <= 20:
                score += 35
            elif 5 <= pullback_depth <= 25:
                score += 25

            if 2 <= trend_5 <= 15:
                score += 20
            elif trend_5 > 0:
                score += 10

            if 0.5 <= vol_ratio <= 0.85:
                score += 15
            elif 0.4 <= vol_ratio <= 1.0:
                score += 10

            if 30 <= position_pct <= 70:
                score += 15
            elif 20 <= position_pct <= 85:
                score += 10

            if midterm_bullish:
                score += 15

            if has_big_drop and stabilized:
                score += 10
            elif has_big_drop:
                score += 5

            if code in mainline_codes:
                score += 10

            if float(close.iloc[-1]) > float(close.iloc[-3]):
                score += 5

            if score >= 40:
                candidates.append({
                    "code": code,
                    "name": name,
                    "price": _safe_float(row[2]),
                    "quote_change": _safe_float(row[3]),
                    "score": score,
                    "trend_5": trend_5,
                    "trend_10": trend_10,
                    "trend_20": trend_20,
                    "pullback_depth": pullback_depth,
                    "vol_ratio": vol_ratio,
                    "position_pct": position_pct,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "ma60": ma60,
                    "max_drop_10": max_drop_10,
                    "has_big_drop": has_big_drop,
                    "stabilized": stabilized,
                    "midterm_bullish": midterm_bullish,
                    "in_mainline": code in mainline_codes,
                    "kdjk": _safe_float(row[4]),
                    "kdjd": _safe_float(row[5]),
                    "kdjj": _safe_float(row[6]),
                    "rsi_6": _safe_float(row[7]),
                    "cci": _safe_float(row[8]),
                    "macdh": _safe_float(row[9]),
                })

        except Exception as e:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    print("[华盛昌风格] 最终选出:", len(candidates))
    return candidates
