#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import urllib.request
import traceback

# 火山引擎 API（OpenAI 兼容格式）
DEFAULT_API_URL = "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"

# 数据库 Schema 描述（供 LLM 理解表结构）
DB_SCHEMA = """
数据库: stock_data

## 表1: stock_zh_ah_name（每日A股行情数据）
| 列名 | 类型 | 含义 |
|------|------|------|
| date | VARCHAR(8) | 日期 YYYYMMDD |
| code | VARCHAR(10) | 股票代码（如600519） |
| name | VARCHAR(50) | 股票名称 |
| latest_price | DECIMAL | 最新价 |
| quote_change | DECIMAL | 涨跌幅(%) |
| ups_downs | DECIMAL | 涨跌额 |
| volume | DECIMAL | 成交量 |
| turnover | DECIMAL | 成交额 |
| amplitude | DECIMAL | 振幅(%) |
| high | DECIMAL | 最高价 |
| low | DECIMAL | 最低价 |
| open | DECIMAL | 今开 |
| closed | DECIMAL | 昨收 |
| quantity_ratio | DECIMAL | 量比 |
| turnover_rate | DECIMAL | 换手率(%) |
| pe_dynamic | DECIMAL | 动态市盈率 |
| pb | DECIMAL | 市净率 |

## 表2: guess_indicators_daily（每日技术指标全量数据）
| 列名 | 类型 | 含义 |
|------|------|------|
| date | VARCHAR(8) | 日期 YYYYMMDD |
| code | VARCHAR(10) | 股票代码 |
| name | VARCHAR(50) | 股票名称 |
| latest_price | DECIMAL | 最新价 |
| quote_change | DECIMAL | 涨跌幅(%) |
| kdjk | DECIMAL | KDJ-K值 |
| kdjd | DECIMAL | KDJ-D值 |
| kdjj | DECIMAL | KDJ-J值 |
| rsi_6 | DECIMAL | RSI(6日) |
| rsi_12 | DECIMAL | RSI(12日) |
| cci | DECIMAL | CCI指标 |
| macd | DECIMAL | MACD |
| macdh | DECIMAL | MACD柱 |
| macds | DECIMAL | MACD信号线 |
| boll | DECIMAL | 布林中轨 |
| boll_ub | DECIMAL | 布林上轨 |
| boll_lb | DECIMAL | 布林下轨 |
| volume | DECIMAL | 成交量 |
| turnover | DECIMAL | 成交额 |

## 表3: guess_indicators_lite_buy_daily（每日猜想买入候选）
列同 guess_indicators_daily，含 kdjj/rsi_6/cci。

## 表4: guess_indicators_lite_sell_daily（每日猜想卖出候选）
列同 guess_indicators_lite_buy_daily。

## 表5: stock_sina_lhb_ggtj（龙虎榜个股上榜）
| 列名 | 含义 |
|------|------|
| date | 日期 |
| code | 代码 |
| name | 名称 |
| ranking_times | 上榜次数 |
| sum_buy | 累积购买额 |
| sum_sell | 累积卖出额 |
| net_amount | 净额 |

## 表6: stock_morning_report（A股选股晨报）
| 列名 | 含义 |
|------|------|
| date | 日期 YYYYMMDD |
| report_content | Markdown格式报告内容 |
| created_at | 创建时间 |

## 表7: stock_evening_report（尾盘选股报告）
列同 stock_morning_report。

注意事项:
- date 字段格式为 YYYYMMDD（如 20260630），查询时注意格式
- 查询最新日期数据: WHERE date = (SELECT MAX(date) FROM 表名)
- 涨跌幅 quote_change 单位是百分比，如 5.2 表示涨 5.2%
"""

SQL_SYSTEM_PROMPT = """你是一个专业的 SQL 生成助手。根据用户的自然语言问题，生成 MySQL SELECT 查询语句。

规则:
1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/ALTER
2. 如果用户没有指定 LIMIT，默认加 LIMIT 50
3. 日期字段 date 格式为 YYYYMMDD 字符串，如 '20260630'
4. 查询最新数据用: WHERE date = (SELECT MAX(date) FROM 表名)
5. 只返回 SQL，不要解释，不要 markdown 代码块标记

数据库结构:
""" + DB_SCHEMA

ANALYSIS_SYSTEM_PROMPT = """你是一个专业的A股数据分析师。根据查询结果，用简洁专业的中文进行分析解读。

规则:
1. 用 Markdown 格式输出
2. 重点突出关键数据和趋势
3. 如有投资参考意义，给出简要建议
4. 提醒风险
5. 控制在 300 字以内
"""


def _call_llm(api_url, api_token, model, messages, temperature=0.1):
    """调用 LLM API（OpenAI 兼容格式）
    返回: (result_str, error_str) — 成功时 error 为 None，失败时 result 为 None
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_token
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2000
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            msg = body["choices"][0]["message"]
            # 兼容 Agent Plan 思考模型：content 可能为空，实际内容在 reasoning_content
            content = (msg.get("content") or "").strip()
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"].strip()
            return content, None
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")[:500]
        except Exception:
            pass
        err_msg = "HTTP %d: %s" % (e.code, err_body)
        print("[LLM] API HTTP错误:", err_msg)
        return None, err_msg
    except Exception as e:
        err_msg = str(e)[:300]
        print("[LLM] API调用异常:", err_msg)
        traceback.print_exc()
        return None, err_msg


def generate_sql(api_url, api_token, model, question, history=None):
    """根据自然语言问题生成 SQL
    返回: (sql_str, error_str) — 成功时 error 为 None
    """
    messages = [{"role": "system", "content": SQL_SYSTEM_PROMPT}]

    # 添加历史对话（只保留用户消息中的问题部分）
    if history:
        for msg in history[-6:]:  # 最多保留最近6轮
            if msg.get("role") == "user":
                messages.append({"role": "user", "content": msg.get("content", "")})
            elif msg.get("role") == "assistant" and msg.get("sql"):
                messages.append({"role": "assistant", "content": msg.get("sql", "")})

    messages.append({"role": "user", "content": question})

    result, err = _call_llm(api_url, api_token, model, messages, temperature=0.0)
    if result is None:
        return None, err

    # 清理可能的 markdown 代码块标记
    result = result.strip()
    if result.startswith("```"):
        lines = result.split("\n")
        result = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return result.strip(), None


def analyze_results(api_url, api_token, model, question, sql, columns, rows, history=None):
    """根据查询结果生成分析"""
    # 构造结果摘要
    rows_text = ""
    if rows:
        header = " | ".join(str(c) for c in columns)
        rows_text = header + "\n"
        for row in rows[:30]:  # 最多传30行给LLM
            rows_text += " | ".join(str(v) for v in row) + "\n"
        if len(rows) > 30:
            rows_text += "... (共%d行，仅显示前30行)" % len(rows)
    else:
        rows_text = "(无数据)"

    user_content = "问题: %s\n\n执行的SQL:\n%s\n\n查询结果(%d行):\n%s" % (
        question, sql, len(rows) if rows else 0, rows_text
    )

    messages = [{"role": "system", "content": ANALYSIS_SYSTEM_PROMPT}]

    if history:
        for msg in history[-4:]:
            if msg.get("role") == "user":
                messages.append({"role": "user", "content": msg.get("content", "")[:200]})
            elif msg.get("role") == "assistant" and msg.get("analysis"):
                messages.append({"role": "assistant", "content": msg.get("analysis", "")[:200]})

    messages.append({"role": "user", "content": user_content})

    result, err = _call_llm(api_url, api_token, model, messages, temperature=0.3)
    if result:
        return result
    return "数据分析暂时不可用: %s" % (err or "未知错误")
