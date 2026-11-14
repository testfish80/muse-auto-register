"""
chat-tempmail.com 临时邮箱 API 封装

文档: https://chat-tempmail.com/en/api-docs/llms.txt
所有请求带 X-API-Key 头。注册 muse.ai 用的验证码邮件就靠它收。

纯 HTTP，不依赖浏览器。
"""

import random
import re
import string
import time

from curl_cffi import requests as cr

BASE = "https://chat-tempmail.com"

# expiryTime 可选值: 3600000 (1小时) | 86400000 (1天) | 259200000 (3天) | 0 (永久)
EXPIRY_1H = 3600000
EXPIRY_1D = 86400000
EXPIRY_3D = 259200000
EXPIRY_PERMANENT = 0

OTP_PATTERNS = [
    # 优先匹配带上下文关键词的 4-8 位数字（中英日韩都覆盖）
    r"(?:code|passcode|password|otp|pin|verify|verification|auth|認証|验证|驗證|認證|인증|확인|코드)"
    r"[^0-9]{0,32}?([0-9]{4,8})",
    # 兜底：正文里的第一组 4-8 位数字
    r"\b([0-9]{4,8})\b",
]


class TempMailError(RuntimeError):
    """chat-tempmail.com API 调用失败"""


class TempMail:
    """一个实例对应一个 API Key（即一个 chat-tempmail.com 账号）"""

    def __init__(self, api_key, base_url=BASE, timeout=30, proxy=None):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = cr.Session(timeout=timeout,
                            proxies={"http": proxy, "https": proxy} if proxy else None)
        self.s.headers.update({"X-API-Key": api_key})

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    # ---------- 基础请求 ----------

    def _req(self, method, path, json_body=None, params=None):
        r = self.s.request(method, self.base + path, json=json_body, params=params)
        if r.status_code >= 400:
            raise TempMailError(f"{method} {path} {r.status_code}: {r.text[:160]}")
        try:
            return r.json() or {}
        except Exception:
            return {}

    # ---------- 域名 / 邮箱 ----------

    def domains(self):
        """可用域名列表，如 ["chat-tempmail.com", ...]"""
        return self._req("GET", "/api/email/domains").get("domains") or []

    def create(self, name=None, domain=None, expiry=EXPIRY_1H):
        """
        创建临时邮箱。返回 {"id": ..., "email": ...}
        name/domain 留空则自动生成 / 自动取第一个可用域名。
        """
        domain = domain or (self.domains() or [None])[0]
        if not domain:
            raise TempMailError("没有可用域名")
        name = name or _random_local()
        data = self._req("POST", "/api/emails/generate",
                         {"name": name, "expiryTime": expiry, "domain": domain})
        if not data.get("email"):
            raise TempMailError(f"create 返回异常: {data}")
        return data

    def list_emails(self, cursor=None):
        return self._req("GET", "/api/emails", params={"cursor": cursor} if cursor else None)

    def delete(self, email_id):
        return self._req("DELETE", f"/api/emails/{email_id}").get("success") is True

    # ---------- 邮件 ----------

    def messages(self, email_id, cursor=None):
        return self._req("GET", f"/api/emails/{email_id}",
                         params={"cursor": cursor} if cursor else None)

    def message_detail(self, email_id, message_id):
        return self._req("GET", f"/api/emails/{email_id}/{message_id}").get("message") or {}

    def delete_message(self, email_id, message_id):
        return self._req("DELETE", f"/api/emails/{email_id}/{message_id}").get("success") is True

    # ---------- 等验证码 ----------

    def wait_otp(self, email_id, sender_contains=None, subject_contains=None,
                 timeout=180, poll_interval=5, since_ms=None, patterns=None):
        """
        轮询收件箱，从新邮件里提取验证码。返回 (otp, message)。
        只认 since_ms 之后收到的邮件，避免把旧邮件的码当成本次的。
        """
        since = since_ms if since_ms is not None else int(time.time() * 1000)
        pats = patterns or OTP_PATTERNS
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.messages(email_id)
            for m in data.get("messages") or []:
                if m.get("received_at", 0) < since:
                    continue
                if sender_contains and sender_contains.lower() not in \
                        (m.get("from_address") or "").lower():
                    continue
                if subject_contains and subject_contains.lower() not in \
                        (m.get("subject") or "").lower():
                    continue
                detail = self.message_detail(email_id, m["id"])
                otp = extract_otp(detail.get("content") or "",
                                  detail.get("html") or "",
                                  detail.get("subject") or "",
                                  patterns=pats)
                if otp:
                    return otp, detail
            time.sleep(poll_interval)
        raise TempMailError(f"等待验证码超时({timeout}s)")

    # ---------- 分享链接 ----------

    def create_share(self, email_id, expiry_hours=0, password=None):
        """公开只读收件箱链接，方便把邮箱扔给团队/客户"""
        body = {"expiryHours": expiry_hours}
        if password:
            body["password"] = password
        return self._req("POST", f"/api/emails/{email_id}/share", body)

    def list_shares(self, email_id):
        return self._req("GET", f"/api/emails/{email_id}/share").get("shares") or []


def extract_otp(*texts, patterns=None):
    """从多段文本里提取验证码。关键词优先，匹配不到再退回第一组 4-8 位数字。"""
    pats = patterns or OTP_PATTERNS
    for p in pats:
        for text in texts:
            if not text:
                continue
            m = re.search(p, text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1)
    return None


def _random_local(length=10):
    letters = string.ascii_lowercase + string.digits
    return "".join(random.choice(letters) for _ in range(length))


if __name__ == "__main__":
    # 极简自检: python -m musepy.tempmail <API_KEY>
    import os
    import sys

    key = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TEMPMAIL_API_KEY")
    if not key:
        print("用法: python -m musepy.tempmail <API_KEY>   # 或设 TEMPMAIL_API_KEY")
        sys.exit(1)
    tm = TempMail(key)
    print("domains =", tm.domains())
    box = tm.create()
    print("created =", box)
    tm.close()
