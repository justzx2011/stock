#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import libs.common as common

# 查询最新的数据日期
sql = "SELECT MAX(date) FROM stock_data.guess_indicators_daily"
result = common.select(sql)
print(f"最新数据日期: {result[0][0] if result else '无数据'}")

# 查询一下有多少条数据
sql2 = "SELECT COUNT(1) FROM stock_data.guess_indicators_daily WHERE date = %s"
if result and result[0][0]:
    count = common.select_count(sql2, (result[0][0],))
    print(f"该日期股票数: {count}")
