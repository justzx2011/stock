#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import datetime
import libs.common as common

def analyze_stock(code, name, tmp_datetime):
    """分析单只股票的走势特征"""
    datetime_int = tmp_datetime.strftime("%Y%m%d")
    date_end = tmp_datetime.strftime("%Y-%m-%d")
    date_start = (tmp_datetime + datetime.timedelta(days=-60)).strftime("%Y-%m-%d")

    print(f"\n{'='*80}")
    print(f"分析股票: {name} ({code})")
    print(f"{'='*80}")

    # 获取K线数据
    kdf = common.get_hist_data_cache(code, date_start, date_end)
    if kdf is None or kdf.empty:
        print("无数据")
        return None

    kdf = kdf.sort_index()
    close = kdf["close"].astype(float)
    high = kdf["high"].astype(float)
    low = kdf["low"].astype(float)
    vol = kdf["volume"].astype(float)
    op = kdf["open"].astype(float)

    cur = float(close.iloc[-1])
    print(f"\n最新收盘: {cur}")

    # 均线
    ma5 = float(close.rolling(5, min_periods=3).mean().iloc[-1]) if len(close) >= 5 else cur
    ma10 = float(close.rolling(10, min_periods=5).mean().iloc[-1]) if len(close) >= 10 else cur
    ma20 = float(close.rolling(20, min_periods=10).mean().iloc[-1]) if len(close) >= 20 else cur
    ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1]) if len(close) >= 60 else cur

    print(f"\n均线系统:")
    print(f"  MA5: {ma5:.2f}")
    print(f"  MA10: {ma10:.2f}")
    print(f"  MA20: {ma20:.2f}")
    print(f"  MA60: {ma60:.2f}")

    # 均线排列
    ma_arrangement = ""
    if ma5 > ma10 > ma20 > ma60:
        ma_arrangement = "完美多头排列"
    elif ma5 > ma20 and ma20 > ma60 * 0.99:
        ma_arrangement = "温和多头排列"
    elif ma20 > ma60:
        ma_arrangement = "中期多头"
    else:
        ma_arrangement = "非多头排列"
    print(f"  排列: {ma_arrangement}")

    # 位置
    lb = min(len(close), 60)
    high_60 = float(high.iloc[-lb:].max())
    low_60 = float(low.iloc[-lb:].min())
    rng = high_60 - low_60
    position_pct = (cur - low_60) / rng * 100 if rng > 0 else 50
    print(f"\n60日位置: {position_pct:.1f}% (区间: {low_60:.2f} - {high_60:.2f})")

    # 近期趋势
    trend_5 = (cur - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) >= 6 else 0
    trend_10 = (cur - float(close.iloc[-11])) / float(close.iloc[-11]) * 100 if len(close) >= 11 else 0
    trend_20 = (cur - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) >= 21 else 0
    print(f"\n趋势涨幅:")
    print(f"  5日: {trend_5:.1f}%")
    print(f"  10日: {trend_10:.1f}%")
    print(f"  20日: {trend_20:.1f}%")

    # 回踩深度
    recent_peak = float(high.iloc[-10:].max())
    pullback_depth = (recent_peak - cur) / recent_peak * 100 if recent_peak > 0 else 0
    print(f"\n回踩: 近期高点 {recent_peak:.2f}, 回踩深度 {pullback_depth:.1f}%")

    # 量能
    vol_5 = float(vol.iloc[-5:].mean())
    vol_10 = float(vol.iloc[-10:-5].mean()) if len(vol) >= 10 else vol_5
    vol_ratio = vol_5 / vol_10 if vol_10 > 0 else 1.0
    print(f"\n量能:")
    print(f"  近5日平均: {vol_5:.0f}")
    print(f"  前5日平均: {vol_10:.0f}")
    print(f"  量能比: {vol_ratio:.2f}")

    # 近日K线形态
    print(f"\n近5日K线:")
    for i in range(min(5, len(kdf))):
        idx = -(i+1)
        d = kdf.index[idx].strftime("%Y-%m-%d")
        o = float(op.iloc[idx])
        c = float(close.iloc[idx])
        h = float(high.iloc[idx])
        l = float(low.iloc[idx])
        v = float(vol.iloc[idx])
        change = (c - o) / o * 100
        print(f"  {d}: 开{o:.2f} 收{c:.2f} 高{h:.2f} 低{l:.2f} 量{v:.0f} 涨跌{change:.1f}%")

    # 获取技术指标
    sql = """
        SELECT `latest_price`,`quote_change`,
               `kdjk`,`kdjd`,`kdjj`,`rsi_6`,`cci`,`macdh`
        FROM stock_data.guess_indicators_daily
        WHERE `date` = %s AND `code` = %s
    """
    rows = common.select(sql, (datetime_int, code))
    if rows:
        row = rows[0]
        print(f"\n技术指标:")
        print(f"  涨跌幅: {row[1]:.2f}%")
        print(f"  KDJ: K={row[2]:.1f} D={row[3]:.1f} J={row[4]:.1f}")
        print(f"  RSI6: {row[5]:.1f}")
        print(f"  CCI: {row[6]:.1f}")
        print(f"  MACDH: {row[7]:.2f}")

    return {
        "code": code,
        "name": name,
        "cur": cur,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "ma_arrangement": ma_arrangement,
        "position_pct": position_pct,
        "trend_5": trend_5,
        "trend_10": trend_10,
        "trend_20": trend_20,
        "pullback_depth": pullback_depth,
        "vol_ratio": vol_ratio,
        "high_60": high_60,
        "low_60": low_60
    }

def main(tmp_datetime):
    # 分析华盛昌
    analyze_stock("002980", "华盛昌", tmp_datetime)

if __name__ == "__main__":
    common.run_with_args(main)
