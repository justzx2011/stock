#!/usr/local/bin/python
# -*- coding: utf-8 -*-

# apk add py-mysqldb or

import platform
import datetime
import time
import sys
import os
import re
import json
import urllib.request
import MySQLdb
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.types import NVARCHAR
from sqlalchemy import inspect
import pandas as pd
import traceback
import akshare as ak

# 使用环境变量获得数据库。兼容开发模式可docker模式。
MYSQL_HOST = os.environ.get('MYSQL_HOST') if (os.environ.get('MYSQL_HOST') != None) else "mysqldb"
MYSQL_USER = os.environ.get('MYSQL_USER') if (os.environ.get('MYSQL_USER') != None) else "root"
MYSQL_PWD = os.environ.get('MYSQL_PWD') if (os.environ.get('MYSQL_PWD') != None) else "mysqldb"
MYSQL_DB = os.environ.get('MYSQL_DB') if (os.environ.get('MYSQL_DB') != None) else "stock_data"

print("MYSQL_HOST :", MYSQL_HOST, ",MYSQL_USER :", MYSQL_USER, ",MYSQL_DB :", MYSQL_DB)
MYSQL_CONN_URL = "mysql+mysqldb://" + MYSQL_USER + ":" + MYSQL_PWD + "@" + MYSQL_HOST + ":3306/" + MYSQL_DB + "?charset=utf8mb4"
print("MYSQL_CONN_URL :", MYSQL_CONN_URL)

__version__ = "2.0.0"
# 每次发布时候更新。

def engine():
    engine = create_engine(MYSQL_CONN_URL, pool_size=10, max_overflow=20)
    return engine

def engine_to_db(to_db):
    MYSQL_CONN_URL_NEW = "mysql+mysqldb://" + MYSQL_USER + ":" + MYSQL_PWD + "@" + MYSQL_HOST + ":3306/" + to_db + "?charset=utf8mb4"
    engine = create_engine(MYSQL_CONN_URL_NEW, pool_size=10, max_overflow=20)
    return engine

# 通过数据库链接 engine。
def conn():
    try:
        db = MySQLdb.connect(MYSQL_HOST, MYSQL_USER, MYSQL_PWD, MYSQL_DB, charset="utf8")
        # db.autocommit = True
    except Exception as e:
        print("conn error :", e)
    db.autocommit(on=True)
    return db.cursor()


# 定义通用方法函数，插入数据库表，并创建数据库主键，保证重跑数据的时候索引唯一。
def insert_db(data, table_name, write_index, primary_keys):
    # 插入默认的数据库。
    insert_other_db(MYSQL_DB, data, table_name, write_index, primary_keys)


# 增加一个插入到其他数据库的方法。
def insert_other_db(to_db, data, table_name, write_index, primary_keys):
    # 定义engine
    engine_mysql = engine_to_db(to_db)
    # 使用 http://docs.sqlalchemy.org/en/latest/core/reflection.html
    # 使用检查检查数据库表是否有主键。
    insp = inspect(engine_mysql)
    col_name_list = data.columns.tolist()
    # 如果有索引，把索引增加到varchar上面。
    if write_index:
        # 插入到第一个位置：
        col_name_list.insert(0, data.index.name)
    print(col_name_list)
    data.to_sql(name=table_name, con=engine_mysql, schema=to_db, if_exists='append',
                dtype={col_name: NVARCHAR(length=255) for col_name in col_name_list}, index=write_index)

    # print(insp.get_pk_constraint(table_name))
    # print()
    # print(type(insp))
    # 判断是否存在主键
    if insp.get_pk_constraint(table_name)['constrained_columns'] == []:
        with engine_mysql.connect() as con:
            # 执行数据库插入数据。
            try:
                con.execute(sql_text('ALTER TABLE `%s` ADD PRIMARY KEY (%s);' % (table_name, primary_keys)))
                con.commit()
            except  Exception as e:
                print("################## ADD PRIMARY KEY ERROR :", e)




# 插入数据。
def insert(sql, params=()):
    with conn() as db:
        print("insert sql:" + sql)
        try:
            db.execute(sql, params)
        except  Exception as e:
            print("error :", e)


# 查询数据
def select(sql, params=()):
    with conn() as db:
        print("select sql:" + sql)
        try:
            db.execute(sql, params)
            result = db.fetchall()
            return result
        except  Exception as e:
            print("error :", e)
            return []


# 计算数量
def select_count(sql, params=()):
    with conn() as db:
        print("select sql:" + sql)
        try:
            db.execute(sql, params)
            result = db.fetchall()
            # 只有一个数组中的第一个数据
            if len(result) == 1:
                return int(result[0][0])
            else:
                return 0
        except  Exception as e:
            print("error :", e)
            return 0


# 通用函数。获得日期参数。
def run_with_args(run_fun):
    tmp_datetime_show = datetime.datetime.now()  # 修改成默认是当日执行 + datetime.timedelta()
    tmp_hour_int = int(tmp_datetime_show.strftime("%H"))
    if tmp_hour_int < 12 :
        # 判断如果是每天 中午 12 点之前运行，跑昨天的数据。
        tmp_datetime_show = (tmp_datetime_show + datetime.timedelta(days=-1))
    tmp_datetime_str = tmp_datetime_show.strftime("%Y-%m-%d %H:%M:%S.%f")
    print("\n######################### hour_int %d " % tmp_hour_int)
    str_db = "MYSQL_HOST :" + MYSQL_HOST + ", MYSQL_USER :" + MYSQL_USER + ", MYSQL_DB :" + MYSQL_DB
    print("\n######################### " + str_db + "  ######################### ")
    print("\n######################### begin run %s %s  #########################" % (run_fun, tmp_datetime_str))
    start = time.time()
    # 要支持数据重跑机制，将日期传入。循环次数
    if len(sys.argv) == 3:
        # python xxx.py 2017-07-01 10
        tmp_year, tmp_month, tmp_day = sys.argv[1].split("-")
        loop = int(sys.argv[2])
        tmp_datetime = datetime.datetime(int(tmp_year), int(tmp_month), int(tmp_day))
        for i in range(0, loop):
            # 循环插入多次数据，重复跑历史数据使用。
            # time.sleep(5)
            tmp_datetime_new = tmp_datetime + datetime.timedelta(days=i)
            try:
                run_fun(tmp_datetime_new)
            except Exception as e:
                print("error :", e)
                traceback.print_exc()
    elif len(sys.argv) == 2:
        # python xxx.py 2017-07-01
        tmp_year, tmp_month, tmp_day = sys.argv[1].split("-")
        tmp_datetime = datetime.datetime(int(tmp_year), int(tmp_month), int(tmp_day))
        try:
            run_fun(tmp_datetime)
        except Exception as e:
            print("error :", e)
            traceback.print_exc()
    else:
        # tmp_datetime = datetime.datetime.now() + datetime.timedelta(days=-1)
        try:
            run_fun(tmp_datetime_show)  # 使用当前时间
        except Exception as e:
            print("error :", e)
            traceback.print_exc()
    print("######################### finish %s , use time: %s #########################" % (
        tmp_datetime_str, time.time() - start))


# 设置基础目录，每次加载使用。
bash_stock_tmp = "/data/cache/hist_data_cache/%s/%s/"
if not os.path.exists(bash_stock_tmp):
    os.makedirs(bash_stock_tmp)  # 创建多个文件夹结构。
    print("######################### init tmp dir #########################")


# ============================================================================
# 限流控制：避免东方财富 API 反爬限流
# ============================================================================
_last_request_time = 0
_REQUEST_INTERVAL = 1.5        # 每次请求间隔（秒）
_BATCH_PAUSE_EVERY = 15        # 每 N 次请求暂停一次
_BATCH_PAUSE_SECONDS = 3       # 暂停秒数
_request_count = 0


def _rate_limit():
    """内置限流：固定间隔 + 定期暂停，防止触发东方财富反爬机制"""
    global _last_request_time, _request_count
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _REQUEST_INTERVAL:
        time.sleep(_REQUEST_INTERVAL - elapsed)
    _request_count += 1
    if _request_count % _BATCH_PAUSE_EVERY == 0:
        print("[rate_limit] 已请求 %d 次，暂停 %ds ..." % (_request_count, _BATCH_PAUSE_SECONDS))
        time.sleep(_BATCH_PAUSE_SECONDS)
    _last_request_time = time.time()


def gp_type_szsh(gp):
    """根据股票代码判断市场前缀（公开接口，供外部调用）"""
    if gp.find('60', 0, 3) == 0 or gp.find('688', 0, 4) == 0 or gp.find('900', 0, 4) == 0:
        return 'sh'
    elif gp.find('00', 0, 3) == 0 or gp.find('300', 0, 4) == 0 or gp.find('200', 0, 4) == 0:
        return 'sz'
    elif gp.find('8', 0, 1) == 0 or gp.find('9', 0, 1) == 0 or gp.find('4', 0, 1) == 0:
        return 'bj'
    return 'sh'

# 内部别名（兼容旧代码）
_gp_type_szsh = gp_type_szsh


def _fetch_kline_sina(code, date_start, date_end):
    """从新浪 API 获取日 K 线数据（替代东方财富，无严格限流）

    返回格式与老版本 ak.stock_zh_a_hist() 兼容：
    index=date, columns=['open','close','high','low','volume','amount',
                         'amplitude','quote_change','ups_downs','turnover']
    """
    prefix = _gp_type_szsh(code)
    symbol = prefix + code
    url = (
        "https://quotes.sina.cn/cn/api/jsonp.php/var%%20_%s=/"
        "CN_MarketDataService.getKLineData"
        "?symbol=%s&scale=240&ma=no&datalen=200"
    ) % (symbol, symbol)

    _rate_limit()

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible)',
            'Referer': 'https://finance.sina.com.cn'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8')

        match = re.search(r"\((\[.*?\])\)", raw, re.DOTALL)
        if not match:
            print("[Sina API] %s 返回数据为空" % code)
            return None

        data = json.loads(match.group(1))
        df = pd.DataFrame(data)

        # 统一列名
        df = df.rename(columns={'day': 'date'})
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
        df['volume'] = df['volume'].astype(float)

        # 日期过滤
        df['date'] = pd.to_datetime(df['date'])
        date_start_dt = pd.to_datetime(date_start)
        date_end_dt = pd.to_datetime(date_end)
        df = df[(df['date'] >= date_start_dt) & (df['date'] <= date_end_dt)]

        if df.empty:
            return None

        # 补充老版本 ak.stock_zh_a_hist() 返回的额外列（stockstats 不用，但保持兼容）
        df['amount'] = 0.0
        df['amplitude'] = ((df['high'] - df['low']) / df['close'] * 100).round(2)
        df['quote_change'] = 0.0
        df['ups_downs'] = 0.0
        df['turnover'] = 0.0

        # 计算涨跌幅
        df['quote_change'] = df['close'].pct_change().apply(lambda x: round(x * 100, 2) if pd.notna(x) else 0)
        df['ups_downs'] = df['close'].diff().round(2)

        df = df.set_index('date')
        # 返回与老版本兼容的列顺序
        return df[['open', 'close', 'high', 'low', 'volume', 'amount',
                    'amplitude', 'quote_change', 'ups_downs', 'turnover']]

    except Exception as e:
        print("[Sina API] 获取 %s 失败: %s" % (code, e))
        return None


# ============================================================================
# K 线数据获取（带缓存 + 新浪优先 + AkShare 降级）
# ============================================================================
def get_hist_data_cache(code, date_start, date_end):
    cache_dir = bash_stock_tmp % (date_end[0:7], date_end)
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    cache_file = cache_dir + "%s^%s.gzip.pickle" % (date_end, code)
    # 如果缓存存在就直接返回缓存数据。压缩方式。
    if os.path.isfile(cache_file):
        print("######### read from cache #########", cache_file)
        return pd.read_pickle(cache_file, compression="gzip")

    # ====== 缓存未命中：优先使用新浪 API（无限流），失败降级到 AkShare ======

    # 方式 1：新浪 API（推荐，无严格限流）
    print("######### [Sina] get data, write cache #########", code, date_start, date_end)
    try:
        stock = _fetch_kline_sina(code, date_start, date_end)
        if stock is not None and not stock.empty:
            stock.to_pickle(cache_file, compression="gzip")
            return stock
    except Exception as e:
        print("[Sina fallback] %s 异常: %s" % (code, e))

    # 方式 2：AkShare 东方财富 API（降级兜底，有限流风险）
    print("######### [AkShare fallback] get data #########", code, date_start, date_end)
    try:
        stock = ak.stock_zh_a_hist(symbol=code, start_date=date_start, end_date=date_end, adjust="")
        stock.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount',
                         'amplitude', 'quote_change', 'ups_downs', 'turnover']
        if stock is None:
            return None
        stock = stock.sort_index()
        stock.to_pickle(cache_file, compression="gzip")
        return stock
    except Exception as e:
        print("[AkShare] %s 获取失败: %s" % (code, e))
        return None
