#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

"""
低开高走选股策略
================

在大盘低开的情况下，寻找可能走出低开高走趋势的股票。

选股逻辑：
1. 前期强势（连续上涨、趋势向上）
2. 近期有缩量回踩（蓄力）
3. 技术指标健康（KDJ、RSI位置合适）
4. 均线多头排列
5. 量能配合好
"""

import sys
import datetime
import libs.common as common
import libs.buy_point as buy_point


def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        # 简单检查是否是有效数字
        if f != f or f == float('inf') or f == float('-inf'):
            return default
        return f
    except Exception:
        return default


def select_low_open_high_close_candidates(tmp_datetime, limit=10):
    """
    选出可能低开高走的股票。

    核心逻辑：
    1. 先从技术面选出强势且有回踩蓄力的股票
    2. 优先考虑主线板块、近期活跃的股票
    3. 结合量价关系综合评分
    """
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    print(f"[低开高走] 选股日期: {datetime_int}")

    # 第一步：获取主线股票池（给主线加分）
    mainline_codes = buy_point.get_mainline_codes(tmp_datetime)
    print(f"[低开高走] 主线股票数: {len(mainline_codes)}")

    # 第二步：从技术指标预筛选合适的候选
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macdh`,
               `volume`,`turnover`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s
            AND latest_price > 0
            AND volume > 0
            -- 技术位置合适：不过热也不过冷
            AND kdjj BETWEEN 30 AND 100
            AND rsi_6 BETWEEN 30 AND 80
            -- 优先考虑有点涨幅但没大涨的（有上涨动力）
            AND quote_change BETWEEN -3 AND 7
        ORDER BY volume DESC
        LIMIT 200
    """
    try:
        rows = common.select(sql, (datetime_int,)) or []
    except Exception as e:
        print(f"[低开高走] 查询异常: {e}")
        return []

    print(f"[低开高走] 初筛候选数: {len(rows)}")

    # 第三步：对每只股票进行详细分析和评分
    candidates = []
    date_end = tmp_datetime.strftime("%Y-%m-%d")
    date_start = (tmp_datetime + datetime.timedelta(days=-60)).strftime("%Y-%m-%d")

    for row in rows:
        code = str(row[0])
        name = row[1] if row[1] else ""
        price = _safe_float(row[2])
        quote_change = _safe_float(row[3])
        kdjk = _safe_float(row[4])
        kdjd = _safe_float(row[5])
        kdjj = _safe_float(row[6])
        rsi_6 = _safe_float(row[7])
        cci = _safe_float(row[8])
        macdh = _safe_float(row[9])

        try:
            # 获取历史K线
            kdf = common.get_hist_data_cache(code, date_start, date_end)
            if kdf is None or kdf.empty or len(kdf) < 20:
                continue

            kdf = kdf.sort_index()
            close = kdf["close"].astype(float)
            high = kdf["high"].astype(float)
            low = kdf["low"].astype(float)
            vol = kdf["volume"].astype(float)
            op = kdf["open"].astype(float)

            if len(close) < 10:
                continue

            cur = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) >= 2 else cur

            # ====== 计算技术指标 ======

            # 1. 均线
            ma5 = float(close.rolling(5, min_periods=3).mean().iloc[-1])
            ma10 = float(close.rolling(10, min_periods=5).mean().iloc[-1])
            ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1])
            ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else ma20

            # 2. 均线排列判断
            ma_up_trend = (ma5 > ma10 > ma20 > ma60)  # 完美多头排列
            ma_mild_up = (ma5 > ma20 and ma20 > ma60 * 0.99)  # 温和多头

            # 3. 近期趋势（5日、10日）
            trend_5 = (cur - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) >= 6 else 0
            trend_10 = (cur - float(close.iloc[-11])) / float(close.iloc[-11]) * 100 if len(close) >= 11 else 0

            # 4. 回踩情况（从近期高点回踩的幅度）
            recent_peak = float(high.iloc[-10:].max())
            pullback_depth = (recent_peak - cur) / recent_peak * 100 if recent_peak > 0 else 0

            # 5. 量能变化
            vol_5 = float(vol.iloc[-5:].mean())
            vol_10 = float(vol.iloc[-10:-5].mean()) if len(vol) >= 10 else vol_5
            vol_ratio = vol_5 / vol_10 if vol_10 > 0 else 1.0

            # 6. 是否在均线上方
            above_ma5 = cur > ma5
            above_ma20 = cur > ma20
            near_ma5 = abs(cur - ma5) / ma5 < 0.03 if ma5 > 0 else False

            # 7. 昨日K线形态（是否有下影线、是否收回）
            yesterday_ok = False
            if len(close) >= 2:
                y_op = float(op.iloc[-2])
                y_cl = float(close.iloc[-2])
                y_hi = float(high.iloc[-2])
                y_lo = float(low.iloc[-2])
                # 昨日有下影线且收盘价在高位（抵抗性下跌）
                lower_shadow = (y_cl - y_lo) / (y_hi - y_lo) if (y_hi - y_lo) > 0 else 0
                yesterday_ok = lower_shadow > 0.4 and y_cl > y_op

            # 8. 位置（60日分位）
            lb = min(len(close), 60)
            high_60 = float(high.iloc[-lb:].max())
            low_60 = float(low.iloc[-lb:].min())
            rng = high_60 - low_60
            position_pct = (cur - low_60) / rng * 100 if rng > 0 else 50

            # ====== 评分系统（总分100）======
            score = 0

            # 1. 均线排列（25分）
            if ma_up_trend:
                score += 25
            elif ma_mild_up:
                score += 18
            elif ma20 > ma60:
                score += 10
            else:
                score += 3

            # 2. 趋势健康度（20分）
            if 0 <= trend_5 <= 10 and -2 <= trend_10 <= 15:
                score += 20  # 温和上涨最健康
            elif trend_5 > 0 and trend_10 > 0:
                score += 12
            elif trend_5 > -5:
                score += 5
            else:
                score += 0

            # 3. 回踩蓄力（20分）
            if 2 <= pullback_depth <= 8 and vol_ratio < 0.9:
                score += 20  # 适度回踩+缩量=完美蓄力
            elif 1 <= pullback_depth <= 12:
                score += 14
            elif pullback_depth < 2:
                score += 8  # 没怎么回踩，但也还行
            else:
                score += 2

            # 4. 量能配合（15分）
            if 0.7 <= vol_ratio <= 1.3:
                score += 15  # 量能稳定
            elif 0.5 <= vol_ratio <= 1.5:
                score += 10
            else:
                score += 4

            # 5. 技术指标位置（10分）
            if 50 <= kdjd <= 80 and 40 <= rsi_6 <= 70:
                score += 10  # 健康位置
            elif 40 <= kdjd <= 90 and 30 <= rsi_6 <= 80:
                score += 6
            else:
                score += 2

            # 6. 昨日形态（5分）
            if yesterday_ok:
                score += 5
            elif quote_change > -2:
                score += 2

            # 7. 主线加分（+10分，额外）
            mainline_bonus = 10 if code in mainline_codes else 0

            # 8. 位置加分（避免追高）
            position_bonus = 0
            if 30 <= position_pct <= 70:
                position_bonus = 8  # 中间位置最安全
            elif 20 <= position_pct <= 85:
                position_bonus = 4

            total_score = score + mainline_bonus + position_bonus

            # ====== 判断是否符合"低开高走"潜力 ======
            has_potential = False
            potential_reason = []

            # 基础条件
            if total_score >= 60:
                has_potential = True
                potential_reason.append("综合评分达标")

            if ma_mild_up or ma_up_trend:
                potential_reason.append("均线多头排列")

            if 1 <= pullback_depth <= 10:
                potential_reason.append("适度回踩蓄力")

            if code in mainline_codes:
                potential_reason.append("属主线板块")

            # 只有有潜力的才加入结果
            if has_potential or total_score >= 55:
                candidates.append({
                    "code": code,
                    "name": name,
                    "price": round(cur, 2),
                    "quote_change": round(quote_change, 2),
                    "score": total_score,
                    "trend_5": round(trend_5, 1),
                    "trend_10": round(trend_10, 1),
                    "pullback_depth": round(pullback_depth, 1),
                    "vol_ratio": round(vol_ratio, 2),
                    "position_pct": round(position_pct, 1),
                    "ma5": round(ma5, 2),
                    "ma10": round(ma10, 2),
                    "ma20": round(ma20, 2),
                    "ma60": round(ma60, 2),
                    "above_ma5": above_ma5,
                    "above_ma20": above_ma20,
                    "kdjk": round(kdjk, 1),
                    "kdjd": round(kdjd, 1),
                    "kdjj": round(kdjj, 1),
                    "rsi_6": round(rsi_6, 1),
                    "cci": round(cci, 1),
                    "macdh": round(macdh, 2),
                    "in_mainline": code in mainline_codes,
                    "yesterday_ok": yesterday_ok,
                    "reason": " + ".join(potential_reason)
                })

        except Exception as e:
            print(f"[低开高走] 处理异常 {code}: {e}")
            continue

    # 按评分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    print(f"[低开高走] 最终选出 {len(candidates)} 只候选")
    return candidates


def print_report(candidates, top_n=5):
    """打印选股报告"""
    print("\n" + "=" * 100)
    print("低开高走选股报告")
    print("=" * 100)
    print(f"\n共选出 {len(candidates)} 只候选股票，以下是 TOP{top_n}：\n")

    for i, c in enumerate(candidates[:top_n], 1):
        print(f"{i}. [{c['code']}] {c['name']}")
        print(f"   现价: {c['price']}  涨跌幅: {c['quote_change']}%")
        print(f"   综合评分: {c['score']}")
        print(f"   5日趋势: {c['trend_5']}%  10日趋势: {c['trend_10']}%")
        print(f"   回踩深度: {c['pullback_depth']}%  量能比: {c['vol_ratio']}")
        print(f"   60日位置: {c['position_pct']}%")
        print(f"   均线: MA5={c['ma5']} MA10={c['ma10']} MA20={c['ma20']} MA60={c['ma60']}")
        print(f"   技术指标: KDJ({c['kdjk']},{c['kdjd']},{c['kdjj']}) RSI={c['rsi_6']} CCI={c['cci']} MACDH={c['macdh']}")
        print(f"   主线上: {'是' if c['in_mainline'] else '否'}")
        print(f"   逻辑: {c['reason']}")
        print()

    print("=" * 100)
    print("\n操作建议：")
    print("1. 大盘低开时观察这些股票的开盘情况")
    print("2. 如果开盘后有承接、不继续大跌，可以关注")
    print("3. 优先考虑主线板块的股票")
    print("4. 注意设置止损位（建议在 MA20 或近期低点下方）")
    print("=" * 100)


def main(tmp_datetime):
    candidates = select_low_open_high_close_candidates(tmp_datetime, limit=20)
    print_report(candidates, top_n=5)
    return candidates


if __name__ == "__main__":
    common.run_with_args(main)
