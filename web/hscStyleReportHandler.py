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


class HscStyleReportListHandler(webBase.AuthenticatedHandler):
    """华盛昌风格选股列表页面"""
    @gen.coroutine
    def get(self):
        self.render("hsc_style_report.html",
                    pythonStockVersion=common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))


class HscStyleReportApiHandler(webBase.AuthenticatedHandler):
    """DataTable服务端数据API"""
    @gen.coroutine
    def get(self):
        try:
            draw = int(self.get_argument("draw", default="1"))
            start = int(self.get_argument("start", default="0"))
            length = int(self.get_argument("length", default="10"))

            # 查询总数
            count_sql = "SELECT COUNT(1) as num FROM hsc_style_summary"
            total = common.select_count(count_sql)

            # 查询数据
            data_sql = "SELECT id, date, created_at FROM hsc_style_summary ORDER BY date DESC LIMIT %d, %d" % (start, length)
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
            print("HscStyleReportApiHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": []}, ensure_ascii=False))


class HscStyleReportDetailHandler(webBase.AuthenticatedHandler):
    """获取单份报告详情"""
    @gen.coroutine
    def get(self):
        report_id = self.get_argument("id", default=None)
        date = self.get_argument("date", default=None)

        try:
            if report_id:
                sql = "SELECT id, date, report_content, created_at FROM hsc_style_summary WHERE id = '%s'" % report_id
            elif date:
                sql = "SELECT id, date, report_content, created_at FROM hsc_style_summary WHERE date = '%s'" % date
            else:
                # 默认返回最新报告
                sql = "SELECT id, date, report_content, created_at FROM hsc_style_summary ORDER BY date DESC LIMIT 1"
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
            print("HscStyleReportDetailHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))


class HscStyleReportGenerateHandler(webBase.AuthenticatedHandler):
    """手动触发生成华盛昌风格选股"""
    @gen.coroutine
    def post(self):
        try:
            # 在后台线程中运行Job
            def run_job():
                try:
                    from jobs.hsc_style_job import stat_all
                    tmp_datetime = datetime.datetime.now()
                    hour = int(tmp_datetime.strftime("%H"))
                    if hour < 12:
                        tmp_datetime = tmp_datetime + datetime.timedelta(days=-1)
                    stat_all(tmp_datetime)
                except Exception as e:
                    print("后台生成华盛昌风格选股异常:", e)
                    traceback.print_exc()

            t = threading.Thread(target=run_job)
            t.daemon = True
            t.start()

            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": True, "message": "华盛昌风格选股生成任务已提交，请稍后刷新查看"}, ensure_ascii=False))
        except Exception as e:
            print("HscStyleReportGenerateHandler error:", e)
            traceback.print_exc()
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"success": False, "message": str(e)}, ensure_ascii=False))
