#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

from tornado import gen
import tornado.web
import web.base as webBase
import libs.common as common
import json
import datetime
import traceback
import threading


class EveningReportListHandler(webBase.BaseHandler):
    """报告列表页面"""
    @gen.coroutine
    def get(self):
        self.render("evening_report.html",
                    pythonStockVersion=common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))


class EveningReportApiHandler(webBase.BaseHandler):
    """DataTable服务端数据API"""
    @gen.coroutine
    def get(self):
        try:
            draw = int(self.get_argument("draw", default="1"))
            start = int(self.get_argument("start", default="0"))
            length = int(self.get_argument("length", default="10"))

            # 查询总数
            count_sql = "SELECT count(1) as num FROM stock_evening_report"
            total = common.select_count(count_sql)

            # 查询数据
            data_sql = "SELECT id, date, created_at FROM stock_evening_report ORDER BY date DESC LIMIT %d, %d" % (start, length)
            rows = common.select(data_sql)

            data_list = []
            if rows:
                for row in rows:
                    data_list.append({
                        "id": row[0],
                        "date": row[1],
                        "created_at": str(row[2]) if row[2] else ""
                    })

            result = {
                "draw": draw,
                "recordsTotal": total,
                "recordsFiltered": total,
                "data": data_list
            }
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            print("EveningReportApiHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": []}, ensure_ascii=False))


class EveningReportDetailHandler(webBase.BaseHandler):
    """获取单份报告详情"""
    @gen.coroutine
    def get(self):
        report_id = self.get_argument("id", default=None)
        date = self.get_argument("date", default=None)

        try:
            if report_id:
                sql = "SELECT id, date, report_content, created_at FROM stock_evening_report WHERE id = '%s'" % report_id
            elif date:
                sql = "SELECT id, date, report_content, created_at FROM stock_evening_report WHERE date = '%s'" % date
            else:
                # 默认返回最新报告
                sql = "SELECT id, date, report_content, created_at FROM stock_evening_report ORDER BY date DESC LIMIT 1"
            rows = common.select(sql)

            if rows and len(rows) > 0:
                row = rows[0]
                result = {
                    "success": True,
                    "data": {
                        "id": row[0],
                        "date": row[1],
                        "report_content": row[2],
                        "created_at": str(row[3]) if row[3] else ""
                    }
                }
            else:
                result = {"success": False, "message": "未找到报告"}

            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            print("EveningReportDetailHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))


class EveningReportGenerateHandler(webBase.BaseHandler):
    """手动触发生成尾盘选股报告"""
    @gen.coroutine
    def post(self):
        try:
            # 在后台线程中运行Job
            def run_job():
                try:
                    from jobs.evening_report_job import stat_all
                    tmp_datetime = datetime.datetime.now()
                    hour = int(tmp_datetime.strftime("%H"))
                    if hour < 12:
                        tmp_datetime = tmp_datetime + datetime.timedelta(days=-1)
                    stat_all(tmp_datetime)
                except Exception as e:
                    print("后台生成尾盘报告异常:", e)
                    traceback.print_exc()

            t = threading.Thread(target=run_job)
            t.daemon = True
            t.start()

            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": True, "message": "尾盘选股报告生成任务已提交，请稍后刷新查看"}, ensure_ascii=False))
        except Exception as e:
            print("EveningReportGenerateHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))
