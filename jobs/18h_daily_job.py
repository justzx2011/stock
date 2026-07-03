#!/usr/local/bin/python3
# -*- coding: utf-8 -*-


import libs.common as common
import sys
import time
import pandas as pd
import numpy as np
from sqlalchemy.types import NVARCHAR
from sqlalchemy import inspect
import datetime
import akshare as ak
import traceback

# 600开头的股票是上证A股，属于大盘股
# 600开头的股票是上证A股，属于大盘股，其中6006开头的股票是最早上市的股票，
# 6016开头的股票为大盘蓝筹股；900开头的股票是上证B股；
# 000开头的股票是深证A股，001、002开头的股票也都属于深证A股，
# 其中002开头的股票是深证A股中小企业股票；
# 200开头的股票是深证B股；
# 300开头的股票是创业板股票；400开头的股票是三板市场股票。
def stock_a(code):
    # 包含全部 A 股：沪主板(600/601/603)、深主板(000/001/003)、中小板(002)、创业板(300/301)、科创板(688)、北交所(8/9/4)
    if (code.startswith('6') or code.startswith('0') or code.startswith('3') or
        code.startswith('8') or code.startswith('9') or code.startswith('4')):
        return True
    else:
        return False
# 过滤掉 st 股票。
def stock_a_filter_st(name):
    # print(code)
    # print(type(code))
    # 上证A股  # 深证A股
    if name.find("ST") == -1:
        return True
    else:
        return False

# 过滤价格，如果没有基本上是退市了。
def stock_a_filter_price(latest_price):
    # float 在 pandas 里面判断 空。
    if np.isnan(latest_price):
        return False
    else:
        return True

####### 3.pdf 方法。宏观经济数据
# 接口全部有错误。只专注股票数据。
def stat_all(tmp_datetime):

    datetime_str = (tmp_datetime).strftime("%Y-%m-%d")
    datetime_int = (tmp_datetime).strftime("%Y%m%d")
    print("datetime_str:", datetime_str)
    print("datetime_int:", datetime_int)

    # 股票列表 - 使用新浪 API（避免东方财富限流）
    try:
        print("[数据源] 使用 ak.stock_zh_a_spot() (新浪)")
        data = ak.stock_zh_a_spot()
        # 新浪返回的列: ['代码', '名称', '最新价', '涨跌额', '涨跌幅', '买入', '卖出', '昨收', '今开', '最高', '最低', '成交量', '成交额', '时间戳']
        # 映射到老版本期望的列名
        data = data.rename(columns={
            '代码': 'code', '名称': 'name', '最新价': 'latest_price',
            '涨跌额': 'ups_downs', '涨跌幅': 'quote_change',
            '成交量': 'volume', '成交额': 'turnover',
            '最高': 'high', '最低': 'low', '今开': 'open', '昨收': 'closed'
        })
        # 代码去掉市场前缀 (sh600519 → 600519, sz000001 → 000001)
        data['code'] = data['code'].str.replace(r'^(sh|sz|bj)', '', regex=True)

        # 补充缺失列（新浪 API 不提供这些字段，用默认值）
        data['amplitude'] = ((data['high'] - data['low']) / data['closed'] * 100).round(2)
        data['quantity_ratio'] = 0.0
        data['turnover_rate'] = 0.0
        data['pe_dynamic'] = 0.0
        data['pb'] = 0.0

        data = data.loc[data["code"].apply(stock_a)].loc[data["name"].apply(stock_a_filter_st)].loc[
            data["latest_price"].apply(stock_a_filter_price)]
        print(data)
        data['date'] = datetime_int  # 修改时间成为int类型。

        # 保护逻辑：检查 open > 0 的股票数量，如果太少说明市场未开盘
        open_count = len(data[data['open'] > 0])
        total_count = len(data)
        print("[检查] open > 0: %d, 总数: %d" % (open_count, total_count))
        
        if open_count < total_count * 0.1:
            print("[SKIP] 市场可能未开盘（open > 0 仅 %d/%d），跳过更新" % (open_count, total_count))
            return

        # 删除老数据。
        del_sql = " DELETE FROM `stock_zh_ah_name` where `date` = '%s' " % datetime_int
        common.insert(del_sql)

        data.set_index('code', inplace=True)
        # 只保留需要的列
        keep_cols = ['name', 'latest_price', 'quote_change', 'ups_downs', 'volume', 'turnover',
                     'amplitude', 'high', 'low', 'open', 'closed', 'quantity_ratio', 'turnover_rate',
                     'pe_dynamic', 'pb', 'date']
        data = data[[c for c in keep_cols if c in data.columns]]
        print(data)
        common.insert_db(data, "stock_zh_ah_name", True, "`date`,`code`")
    except Exception as e:
        print("error :", e)
        traceback.print_exc()



    # 龙虎榜-个股上榜统计
    # 接口: stock_sina_lhb_ggtj
    #
    # 目标地址: http://vip.stock.finance.sina.com.cn/q/go.php/vLHBData/kind/ggtj/index.phtml
    #
    # 描述: 获取新浪财经-龙虎榜-个股上榜统计
    #

    try:
        stock_sina_lhb_ggtj = ak.stock_lhb_ggtj_sina(symbol="5")
        print(stock_sina_lhb_ggtj)

        stock_sina_lhb_ggtj.columns = ['code', 'name', 'ranking_times', 'sum_buy', 'sum_sell', 'net_amount', 'buy_seat',
                                       'sell_seat']

        stock_sina_lhb_ggtj = stock_sina_lhb_ggtj.loc[stock_sina_lhb_ggtj["code"].apply(stock_a)].loc[
            stock_sina_lhb_ggtj["name"].apply(stock_a_filter_st)]

        stock_sina_lhb_ggtj.set_index('code', inplace=True)
        # data_sina_lhb.drop('index', axis=1, inplace=True)
        # 删除老数据。
        stock_sina_lhb_ggtj['date'] = datetime_int  # 修改时间成为int类型。

        # 删除老数据。
        del_sql = " DELETE FROM `stock_sina_lhb_ggtj` where `date` = '%s' " % datetime_int
        common.insert(del_sql)

        common.insert_db(stock_sina_lhb_ggtj, "stock_sina_lhb_ggtj", True, "`date`,`code`")

    except Exception as e:
        print("error :", e)


    # 每日统计
    # 接口: stock_dzjy_mrtj
    #
    # 目标地址: http://data.eastmoney.com/dzjy/dzjy_mrtj.aspx
    #
    # 描述: 获取东方财富网-数据中心-大宗交易-每日统计

    try:

        print("################ tmp_datetime : " + datetime_str)

        stock_dzjy_mrtj = ak.stock_dzjy_mrtj(start_date=datetime_int, end_date=datetime_int)
        print(stock_dzjy_mrtj)

        stock_dzjy_mrtj.columns = ['index', 'trade_date', 'code', 'name', 'quote_change', 'close_price', 'average_price',
                                   'overflow_rate', 'trade_number', 'sum_volume', 'sum_turnover',
                                   'turnover_market_rate']

        stock_dzjy_mrtj.set_index('code', inplace=True)
        # data_sina_lhb.drop('index', axis=1, inplace=True)
        # 删除老数据。
        stock_dzjy_mrtj['date'] = datetime_int  # 修改时间成为int类型。
        stock_dzjy_mrtj.drop('trade_date', axis=1, inplace=True)
        stock_dzjy_mrtj.drop('index', axis=1, inplace=True)

        # 数据保留2位小数
        try:
            stock_dzjy_mrtj = stock_dzjy_mrtj.loc[stock_dzjy_mrtj["code"].apply(stock_a)].loc[
                stock_dzjy_mrtj["name"].apply(stock_a_filter_st)]

            stock_dzjy_mrtj["average_price"] = stock_dzjy_mrtj["average_price"].round(2)
            stock_dzjy_mrtj["overflow_rate"] = stock_dzjy_mrtj["overflow_rate"].round(4)
            stock_dzjy_mrtj["turnover_market_rate"] = stock_dzjy_mrtj["turnover_market_rate"].round(6)
        except Exception as e:
            print("round error :", e)

        # 删除老数据。
        del_sql = " DELETE FROM `stock_dzjy_mrtj` where `date` = '%s' " % datetime_int
        common.insert(del_sql)

        print(stock_dzjy_mrtj)

        common.insert_db(stock_dzjy_mrtj, "stock_dzjy_mrtj", True, "`date`,`code`")

    except Exception as e:
        print("error :", e)

# main函数入口
if __name__ == '__main__':
    # 执行数据初始化。
    # 使用方法传递。
    tmp_datetime = common.run_with_args(stat_all)
