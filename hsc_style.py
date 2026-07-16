#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

"""
基于华盛昌(002980)走势特征选股

华盛昌特征总结:
1. 经历较深回调 (回踩深度 12.2%)
2. 近期开始反弹 (5日趋势 +5.2%)
3. 量能萎缩 (量能比 0.71)
4. 60日位置中等 (51.7%)
5. 均线: MA20 > MA60 (中期多头)
6. 先大跌后企稳反弹
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
        return f
    except Exception:
        return default

def select_hsc_style(tmp_datetime, limit=200):
    """基于华盛昌特征选股"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    print(f"[华盛昌风格选股日期: {datetime_int}")

    # 第一步: 获取主线股票
    mainline_codes = buy_point.get_mainline_codes(tmp_datetime)
    print(f"主线股票数: {len(mainline_codes)}")

    # 第二步: 从技术指标预筛选
    sql = """
        SELECT `code`,`name`,`latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macdh`,
               `volume`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s
            AND latest_price > 0
            AND volume > 0
        ORDER BY volume DESC
        LIMIT %s
    """
    try:
        rows = common.select(sql, (datetime_int, limit)) or []
    except Exception as e:
        print(f"查询异常: {e}")
        return []

    print(f"初筛候选数: {len(rows)}")

    # 第三步: 详细分析每只股票
    candidates = []
    date_end = tmp_datetime.strftime("%Y-%m-%d")
    date_start = (tmp_datetime + datetime.timedelta(days=-60)).strftime("%Y-%m-%d")

    for row in rows:
        code = str(row[0])
        name = row[1] if row[1] else ""
        price = _safe_float(row[2])

        try:
            # 获取K线数据
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

            # === 计算特征指标 ===

            # 1. 均线
            ma5 = float(close.rolling(5, min_periods=3).mean().iloc[-1])
            ma10 = float(close.rolling(10, min_periods=5).mean().iloc[-1])
            ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1])
            ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else ma20

            # 2. 趋势
            trend_5 = (cur - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) >= 6 else 0
            trend_10 = (cur - float(close.iloc[-11])) / float(close.iloc[-11]) * 100 if len(close) >= 11 else 0
            trend_20 = (cur - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) >= 21 else 0

            # 3. 回踩深度 (从20日高点)
            peak_20 = float(high.iloc[-20:].max())
            pullback_depth = (peak_20 - cur) / peak_20 * 100 if peak_20 > 0 else 0

            # 4. 量能
            vol_5 = float(vol.iloc[-5:].mean())
            vol_10 = float(vol.iloc[-10:-5].mean()) if len(vol) >= 10 else vol_5
            vol_ratio = vol_5 / vol_10 if vol_10 > 0 else 1.0

            # 5. 60日位置
            lb = min(len(close), 60)
            high_60 = float(high.iloc[-lb:].max())
            low_60 = float(low.iloc[-lb:].min())
            rng = high_60 - low_60
            position_pct = (cur - low_60) / rng * 100 if rng > 0 else 50

            # 6. 近日是否有大跌 (10日内跌幅 > 8%)
            has_big_drop = False
            max_drop_10 = 0
            for i in range(min(10, len(close)-1)):
                idx = -(i+1)
                prev_close = float(close.iloc[idx])
                prev_high = float(high.iloc[idx-1]) if idx-1 >= -len(close) else prev_close
                drop = (prev_high - prev_close) / prev_high * 100
                if drop > max_drop_10:
                    max_drop_10 = drop
                if drop > 8:
                    has_big_drop = True

            # 7. 近期是否企稳反弹 (近3日低点不创新低，且有阳线)
            stabilized = False
            if len(close) >= 5:
                recent_low = float(low.iloc[-3:].min())
                prev_low = float(low.iloc[-5:-2].min())
                stabilized = recent_low >= prev_low * 0.98
            has_rebound = float(close.iloc[-1]) > float(close.iloc[-3])

            # 8. 均线结构
            midterm_bullish = ma20 > ma60 * 0.99

            # === 评分系统 ===
            score = 0
            reasons = []

            # 1. 回踩深度 (35分) - 华盛昌是12.2%
            if 8 <= pullback_depth <= 20:
                score += 35
                reasons.append("深度回调8-20%")
            elif 5 <= pullback_depth <= 25:
                score += 25
                reasons.append("适度回调5-25%")

            # 2. 近期反弹 (20分) - 华盛昌5日+5.2%
            if 2 <= trend_5 <= 15:
                score += 20
                reasons.append("5日反弹2-15%")
            elif trend_5 > 0:
                score += 10
                reasons.append("5日正收益")

            # 3. 量能萎缩 (15分) - 华盛昌0.71
            if 0.5 <= vol_ratio <= 0.85:
                score += 15
                reasons.append("缩量调整")
            elif 0.4 <= vol_ratio <= 1.0:
                score += 10
                reasons.append("量能稳定")

            # 4. 位置适中 (15分) - 华盛昌51.7%
            if 30 <= position_pct <= 70:
                score += 15
                reasons.append("位置适中30-70%")
            elif 20 <= position_pct <= 85:
                score += 10
                reasons.append("位置合理")

            # 5. 中期多头 (15分)
            if midterm_bullish:
                score += 15
                reasons.append("中期均线多头")

            # 6. 有大跌后企稳 (10分) - 额外加分
            if has_big_drop and stabilized:
                score += 10
                reasons.append("大跌后企稳")
            elif has_big_drop:
                score += 5
                reasons.append("有大跌")

            # 7. 主线加分 (+10)
            if code in mainline_codes:
                score += 10
                reasons.append("主线板块")

            # 8. 近期反弹确认
            if has_rebound:
                score += 5
                reasons.append("近期反弹")

            # 只有评分达标才加入
            if score >= 40:
                candidates.append({
                    "code": code,
                    "name": name,
                    "price": cur,
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
                    "in_mainline": code in mainline_codes,
                    "reasons": " + ".join(reasons)
                })

        except Exception as e:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    print(f"最终选出 {len(candidates)} 只候选")
    return candidates

def print_report(candidates, top_n=5):
    print("\n" + "=" * 100)
    print("华盛昌(002980)风格选股报告")
    print("=" * 100)
    print("\n选股特征:")
    print("  ✅ 经历较深回调 (8-20%)")
    print("  ✅ 近期开始反弹 (5日2-15%)")
    print("  ✅ 量能萎缩 (0.5-0.85)")
    print("  ✅ 60日位置适中 (30-70%)")
    print("  ✅ 中期均线多头 (MA20 > MA60)")
    print("  ✅ 大跌后企稳反弹")

    print(f"\n共选出 {len(candidates)} 只候选，TOP{top_n}：\n")

    for i, c in enumerate(candidates[:top_n], 1):
        print(f"{i}. [{c['code']}] {c['name']}")
        print(f"   现价: {c['price']:.2f}  涨跌幅: {c['quote_change']:.2f}%")
        print(f"   综合评分: {c['score']}")
        print(f"   趋势: 5日{c['trend_5']:.1f}% 10日{c['trend_10']:.1f}% 20日{c['trend_20']:.1f}%")
        print(f"   回踩深度: {c['pullback_depth']:.1f}%  10日内最大跌幅: {c['max_drop_10']:.1f}%")
        print(f"   量能比: {c['vol_ratio']:.2f}  60日位置: {c['position_pct']:.1f}%")
        print(f"   均线: MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}")
        print(f"   主线上: {'是' if c['in_mainline'] else '否'}  大跌后企稳: {'是' if c['stabilized'] else '否'}")
        print(f"   逻辑: {c['reasons']}")
        print()

    print("=" * 100)

def main(tmp_datetime):
    candidates = select_hsc_style(tmp_datetime)
    print_report(candidates, top_n=5)
    return candidates

if __name__ == "__main__":
    common.run_with_args(main)
