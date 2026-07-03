#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import re
import traceback
from tornado import gen
import tornado.web
import web.base as webBase
import libs.common as common
import libs.llm_client as llm_client

# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `ai_agent_config` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `config_key` VARCHAR(50) NOT NULL,
    `config_value` TEXT NOT NULL,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_key` (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS `ai_agent_history` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `question` TEXT NOT NULL,
    `sql_text` TEXT,
    `analysis` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_FAVORITES_SQL = """
CREATE TABLE IF NOT EXISTS `ai_agent_favorites` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `question` TEXT NOT NULL,
    `sql_text` TEXT,
    `analysis` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 初始化建表（延迟到首次请求时执行，避免模块加载时打印误导性日志）
_table_inited = False

def _ensure_table():
    global _table_inited
    if _table_inited:
        return
    _table_inited = True
    try:
        cursor = common.conn()
        cursor.execute(CREATE_TABLE_SQL)
        cursor.execute(CREATE_HISTORY_SQL)
        cursor.execute(CREATE_FAVORITES_SQL)
        cursor.close()
    except Exception as e:
        print("[AI Agent] 建表异常（可能已存在）:", e)


def get_config(key, default=""):
    """从数据库读取配置"""
    try:
        rows = common.select(
            "SELECT config_value FROM ai_agent_config WHERE config_key = %s", (key,)
        )
        if rows and len(rows) > 0:
            return rows[0][0]
    except Exception as e:
        print("[AI Agent] 读取配置异常:", e)
    return default


def set_config(key, value):
    """保存配置到数据库"""
    try:
        common.insert(
            "INSERT INTO ai_agent_config (config_key, config_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE config_value = %s",
            (key, value, value)
        )
    except Exception as e:
        print("[AI Agent] 保存配置异常:", e)
        traceback.print_exc()


def validate_sql(sql):
    """SQL 安全校验：仅允许 SELECT"""
    if not sql:
        return False, "SQL 为空"
    sql_upper = sql.strip().upper()
    # 禁止非 SELECT 操作
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "EXEC", "EXECUTE"]
    for keyword in forbidden:
        if re.search(r'\b' + keyword + r'\b', sql_upper):
            return False, "禁止执行 %s 操作，仅允许查询" % keyword
    # 自动追加 LIMIT
    if "LIMIT" not in sql_upper:
        sql = sql.rstrip(";") + " LIMIT 50"
    return True, sql


# ============================================================
# Handler: AI 分析师页面
# ============================================================
class AiAgentPageHandler(webBase.BaseHandler):
    @gen.coroutine
    def get(self):
        _ensure_table()
        model_name = get_config("model_name", "")
        token = get_config("api_token", "")
        has_token = len(token) > 0
        self.render("ai_agent.html",
                    model_name=model_name,
                    has_token=has_token,
                    pythonStockVersion=common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))


# ============================================================
# Handler: 聊天 API
# ============================================================
class AiAgentChatHandler(webBase.BaseHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(self.request.body.decode("utf-8"))
            message = body.get("message", "").strip()
            history = body.get("history", [])
        except Exception as e:
            self.write(json.dumps({"error": "请求格式错误"}, ensure_ascii=False))
            return

        if not message:
            self.write(json.dumps({"error": "请输入问题"}, ensure_ascii=False))
            return

        # 读取配置
        api_token = get_config("api_token", "")
        model_name = get_config("model_name", "")
        api_url = get_config("api_url", llm_client.DEFAULT_API_URL)

        if not api_token or not model_name:
            self.write(json.dumps({
                "error": "请先在页面设置中配置 API Token 和模型名称"
            }, ensure_ascii=False))
            return

        # Step 1: 生成 SQL
        sql, llm_err = llm_client.generate_sql(api_url, api_token, model_name, message, history)
        if not sql:
            self.write(json.dumps({
                "error": "AI 无法生成查询语句: %s" % (llm_err or "请检查 Token 和模型配置"),
                "sql": ""
            }, ensure_ascii=False))
            return

        # Step 2: SQL 安全校验
        is_valid, result = validate_sql(sql)
        if not is_valid:
            self.write(json.dumps({
                "error": result,
                "sql": sql
            }, ensure_ascii=False))
            return
        sql = result

        # Step 3: 执行 SQL（超时 10 秒）
        columns = []
        rows = []
        cursor = None
        try:
            cursor = common.conn()
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [list(row) for row in cursor.fetchall()]
        except Exception as e:
            print("[AI Agent] SQL执行异常:", e)
            self.write(json.dumps({
                "error": "SQL 执行失败: %s" % str(e)[:200],
                "sql": sql
            }, ensure_ascii=False))
            return
        finally:
            if cursor:
                cursor.close()

        # Step 4: AI 分析结果
        analysis = llm_client.analyze_results(
            api_url, api_token, model_name,
            message, sql, columns, rows, history
        )

        # 返回结果
        self.write(json.dumps({
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "analysis": analysis
        }, ensure_ascii=False, default=str))


# ============================================================
# Handler: 配置管理
# ============================================================
class AiAgentConfigHandler(webBase.BaseHandler):
    def get(self):
        """获取当前配置（token 脱敏）"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        token = get_config("api_token", "")
        model_name = get_config("model_name", "")
        api_url = get_config("api_url", llm_client.DEFAULT_API_URL)

        # token 脱敏：只显示末4位
        masked_token = ""
        if token:
            masked_token = "****" + token[-4:] if len(token) > 4 else "****"

        self.write(json.dumps({
            "api_token": masked_token,
            "model_name": model_name,
            "api_url": api_url,
            "has_token": len(token) > 0
        }, ensure_ascii=False))

    def post(self):
        """保存配置"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(self.request.body.decode("utf-8"))
        except Exception:
            self.write(json.dumps({"success": False, "message": "请求格式错误"}, ensure_ascii=False))
            return

        api_token = body.get("api_token", "").strip()
        model_name = body.get("model_name", "").strip()
        api_url = body.get("api_url", "").strip()

        if api_token:
            set_config("api_token", api_token)
        if model_name:
            set_config("model_name", model_name)
        if api_url:
            set_config("api_url", api_url)

        self.write(json.dumps({
            "success": True,
            "message": "配置已保存"
        }, ensure_ascii=False))


# ============================================================
# Handler: 大模型可用性检测
# ============================================================
class AiAgentTestHandler(webBase.BaseHandler):
    def post(self):
        """发送测试请求验证 LLM 可用性"""
        self.set_header("Content-Type", "application/json; charset=utf-8")

        api_token = get_config("api_token", "")
        model_name = get_config("model_name", "")
        api_url = get_config("api_url", llm_client.DEFAULT_API_URL)

        if not api_token:
            self.write(json.dumps({
                "success": False, "message": "未配置 API Token"
            }, ensure_ascii=False))
            return
        if not model_name:
            self.write(json.dumps({
                "success": False, "message": "未配置模型名称"
            }, ensure_ascii=False))
            return

        messages = [{"role": "user", "content": "请回复OK"}]
        result, err = llm_client._call_llm(api_url, api_token, model_name, messages, temperature=0)

        if result:
            self.write(json.dumps({
                "success": True,
                "message": "连接成功，模型响应: %s" % result[:100]
            }, ensure_ascii=False))
        else:
            self.write(json.dumps({
                "success": False,
                "message": "连接失败: %s" % (err or "未知错误")
            }, ensure_ascii=False))


# ============================================================
# Handler: 聊天历史（服务端存储）
# ============================================================
class AiAgentHistoryHandler(webBase.BaseHandler):
    def get(self):
        """获取最近10条历史记录"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            rows = common.select(
                "SELECT id, question, sql_text, analysis, "
                "DATE_FORMAT(created_at, '%%m/%%d %%H:%%i') as time "
                "FROM ai_agent_history ORDER BY id DESC LIMIT 10"
            )
            items = []
            for r in (rows or []):
                items.append({
                    "id": r[0], "question": r[1],
                    "sql": r[2] or "", "analysis": r[3] or "",
                    "time": r[4] or ""
                })
            self.write(json.dumps(items, ensure_ascii=False, default=str))
        except Exception as e:
            print("[AI Agent] 读取历史异常:", e)
            self.write(json.dumps([], ensure_ascii=False))

    def post(self):
        """保存一条历史记录"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(self.request.body.decode("utf-8"))
            question = body.get("question", "")
            sql_text = body.get("sql", "")
            analysis = body.get("analysis", "")
            common.insert(
                "INSERT INTO ai_agent_history (question, sql_text, analysis) VALUES (%s, %s, %s)",
                (question, sql_text, analysis)
            )
            # 只保留最近50条
            common.insert("DELETE FROM ai_agent_history WHERE id NOT IN "
                          "(SELECT id FROM (SELECT id FROM ai_agent_history ORDER BY id DESC LIMIT 50) t)")
            self.write(json.dumps({"success": True}, ensure_ascii=False))
        except Exception as e:
            print("[AI Agent] 保存历史异常:", e)
            self.write(json.dumps({"success": False}, ensure_ascii=False))


# ============================================================
# Handler: 收藏（服务端存储）
# ============================================================
class AiAgentFavoritesHandler(webBase.BaseHandler):
    def get(self):
        """获取全部收藏"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            rows = common.select(
                "SELECT id, question, sql_text, analysis, "
                "DATE_FORMAT(created_at, '%%m/%%d %%H:%%i') as time "
                "FROM ai_agent_favorites ORDER BY id DESC"
            )
            items = []
            for r in (rows or []):
                items.append({
                    "id": r[0], "question": r[1],
                    "sql": r[2] or "", "analysis": r[3] or "",
                    "time": r[4] or ""
                })
            self.write(json.dumps(items, ensure_ascii=False, default=str))
        except Exception as e:
            print("[AI Agent] 读取收藏异常:", e)
            self.write(json.dumps([], ensure_ascii=False))

    def post(self):
        """添加收藏"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(self.request.body.decode("utf-8"))
            question = body.get("question", "")
            sql_text = body.get("sql", "")
            analysis = body.get("analysis", "")
            # 去重
            existing = common.select(
                "SELECT id FROM ai_agent_favorites WHERE question = %s", (question,)
            )
            if existing:
                self.write(json.dumps({"success": True, "message": "已存在"}, ensure_ascii=False))
                return
            common.insert(
                "INSERT INTO ai_agent_favorites (question, sql_text, analysis) VALUES (%s, %s, %s)",
                (question, sql_text, analysis)
            )
            self.write(json.dumps({"success": True}, ensure_ascii=False))
        except Exception as e:
            print("[AI Agent] 添加收藏异常:", e)
            self.write(json.dumps({"success": False}, ensure_ascii=False))

    def delete(self):
        """删除收藏"""
        self.set_header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(self.request.body.decode("utf-8"))
            fav_id = body.get("id")
            if fav_id:
                common.insert("DELETE FROM ai_agent_favorites WHERE id = %s", (fav_id,))
            self.write(json.dumps({"success": True}, ensure_ascii=False))
        except Exception as e:
            print("[AI Agent] 删除收藏异常:", e)
            self.write(json.dumps({"success": False}, ensure_ascii=False))
