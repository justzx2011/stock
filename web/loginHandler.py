#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import hashlib
import tornado.web
import tornado.escape
import web.base as webBase

# 硬编码用户（简单场景）
VALID_USER = "justzx"
VALID_PASS_HASH = hashlib.sha256("justzx123456".encode()).hexdigest()


class LoginHandler(webBase.BaseHandler):
    def get(self):
        next_url = self.get_argument("next", "/stock/")
        self.render("login.html", next_url=next_url, error="")

    def post(self):
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        next_url = self.get_argument("next", "/stock/")

        pass_hash = hashlib.sha256(password.encode()).hexdigest()

        if username == VALID_USER and pass_hash == VALID_PASS_HASH:
            self.set_secure_cookie("user", username, expires_days=7)
            self.redirect(next_url)
        else:
            self.render("login.html", next_url=next_url, error="用户名或密码错误")


class LogoutHandler(webBase.BaseHandler):
    def get(self):
        self.clear_cookie("user")
        self.redirect("/login")
