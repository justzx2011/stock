#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import os.path
import torndb
import tornado.escape
from tornado import gen
import tornado.httpserver
import tornado.ioloop
import tornado.options
import libs.common as common
import libs.stock_web_dic as stock_web_dic
import web.dataTableHandler as dataTableHandler
import web.dataEditorHandler as dataEditorHandler
import web.dataIndicatorsHandler as dataIndicatorsHandler
import web.morningReportHandler as morningReportHandler
import web.eveningReportHandler as eveningReportHandler
import web.meishiTechReportHandler as meishiTechReportHandler
import web.aiAgentHandler as aiAgentHandler
import web.loginHandler as loginHandler
import web.base as webBase
import pandas as pd
import numpy as np
import akshare as ak
import bokeh as bh

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            # 登录/登出
            (r"/login", loginHandler.LoginHandler),
            (r"/logout", loginHandler.LogoutHandler),
            # 设置路由
            (r"/", HomeHandler),
            (r"/stock/", HomeHandler),
            (r"/test_akshare", TestHandler),# 测试页面，做写js 测试。
            (r"/test2", Test2Handler),# 测试页面，做写js 测试。
            # 使用datatable 展示报表数据模块。
            (r"/stock/api_data", dataTableHandler.GetStockDataHandler),
            (r"/stock/data", dataTableHandler.GetStockHtmlHandler),
            # 数据修改dataEditor。
            (r"/data/editor", dataEditorHandler.GetEditorHtmlHandler),
            (r"/data/editor/save", dataEditorHandler.SaveEditorHandler),
            # 获得股票指标数据。
            (r"/stock/data/indicators", dataIndicatorsHandler.GetDataIndicatorsHandler),
            # A股选股晨报。
            (r"/stock/report", morningReportHandler.MorningReportListHandler),
            (r"/stock/report/api", morningReportHandler.MorningReportApiHandler),
            (r"/stock/report/detail", morningReportHandler.MorningReportDetailHandler),
            (r"/stock/report/generate", morningReportHandler.MorningReportGenerateHandler),
            # 尾盘选股报告。
            (r"/stock/evening_report", eveningReportHandler.EveningReportListHandler),
            (r"/stock/evening_report/api", eveningReportHandler.EveningReportApiHandler),
            (r"/stock/evening_report/detail", eveningReportHandler.EveningReportDetailHandler),
            (r"/stock/evening_report/generate", eveningReportHandler.EveningReportGenerateHandler),
            # 魅视科技式选股。
            (r"/stock/meishi_tech", meishiTechReportHandler.MeishiTechReportListHandler),
            (r"/stock/meishi_tech/api", meishiTechReportHandler.MeishiTechReportApiHandler),
            (r"/stock/meishi_tech/detail", meishiTechReportHandler.MeishiTechReportDetailHandler),
            (r"/stock/meishi_tech/generate", meishiTechReportHandler.MeishiTechReportGenerateHandler),
            # AI 数据分析师。
            (r"/stock/ai_agent", aiAgentHandler.AiAgentPageHandler),
            (r"/stock/ai_agent/chat", aiAgentHandler.AiAgentChatHandler),
            (r"/stock/ai_agent/config", aiAgentHandler.AiAgentConfigHandler),
            (r"/stock/ai_agent/test", aiAgentHandler.AiAgentTestHandler),
            (r"/stock/ai_agent/history", aiAgentHandler.AiAgentHistoryHandler),
            (r"/stock/ai_agent/favorites", aiAgentHandler.AiAgentFavoritesHandler),
        ]
        settings = dict(  # 配置
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            xsrf_cookies=False,  # True,
            # cookie加密
            cookie_secret="027bb1b670eddf0392cdda8709268a17b58b7",
            login_url="/login",
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)
        # Have one global connection to the blog DB across all handlers
        self.db = torndb.Connection(
            charset="utf8", max_idle_time=3600, connect_timeout=1000,
            host=common.MYSQL_HOST, database=common.MYSQL_DB,
            user=common.MYSQL_USER, password=common.MYSQL_PWD)


# 首页handler。
class HomeHandler(webBase.AuthenticatedHandler):
    @gen.coroutine
    def get(self):
        print("################## index.html ##################")
        pandasVersion = pd.__version__
        numpyVersion = np.__version__
        akshareVersion = ak.__version__
        bokehVersion = bh.__version__
        #stockstatsVersion = ss.__version__ # 没有这个函数，但是好久不更新了
        # https://github.com/jealous/stockstats
        self.render("index.html", pandasVersion=pandasVersion, numpyVersion=numpyVersion,
                    akshareVersion=akshareVersion, bokehVersion=bokehVersion,
                    stockstatsVersion="0.3.2",
                    pythonStockVersion = common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))
class TestHandler(webBase.BaseHandler):
    @gen.coroutine
    def get(self):
        self.render("test_akshare.html", entries="hello",
                    pythonStockVersion=common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))
class Test2Handler(webBase.BaseHandler):
    @gen.coroutine
    def get(self):
        self.render("test2.html", entries="hello",
                    pythonStockVersion=common.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))

def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    port = 9999
    http_server.listen(port)
    # tornado.options.options.logging = "debug"
    tornado.options.parse_command_line()

    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
