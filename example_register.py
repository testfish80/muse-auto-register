"""
muse.ai 全自动注册 + 邀请码兑换（临时邮箱版）

邮箱由 chat-tempmail.com (https://chat-tempmail.com) 提供，
验证码自动收取，全程无需人工输入。

运行前请填写 config.json（见 config.example.json）。
"""

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from musepy import caa, falco, tempmail
from musepy.client import MuseClient, MuseError, random_dob


def load_config(path="config.json"):
    if not os.path.exists(path):
        raise SystemExit(f"缺少配置文件 {path}，请从 config.example.json 复制并填写")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    cfg = load_config()

    api_key = cfg.get("tempmail_api_key") or os.environ.get("TEMPMAIL_API_KEY")
    if not api_key:
        raise SystemExit(
            "缺少临时邮箱 API Key：请在 config.json 填 tempmail_api_key，"
            "或设置环境变量 TEMPMAIL_API_KEY。\n"
            "API Key 在 https://chat-tempmail.com 的 Profile 页面创建。"
        )

    proxy = cfg.get("proxy") or None
    invite_code = cfg.get("invite_code") or ""
    expiry = int(cfg.get("tempmail_expiry", tempmail.EXPIRY_1H))
    poll_timeout = int(cfg.get("otp_timeout", 180))
    mail_domain = cfg.get("tempmail_domain") or None

    # ---- 1. 生成临时邮箱 ----
    print("[1] 创建临时邮箱 (chat-tempmail.com)")
    tm = tempmail.TempMail(api_key, proxy=proxy)
    since_ms = int(time.time() * 1000)  # 只认这之后到达的邮件
    box = tm.create(domain=mail_domain, expiry=expiry)
    email = box["email"]
    print(f"    邮箱 = {email}")
    print(f"    在线收件箱: https://chat-tempmail.com  (邮箱 id: {box['id']})")

    # ---- 2. muse.ai 注册流程 ----
    c = MuseClient(proxy=proxy, impersonate=cfg.get("impersonate", "chrome131"),
                   lang=cfg.get("lang", "ja"))

    print("[2] 打开首页 / 重启注册流程")
    c.open_home()
    c.restart()

    # ---- 可选的漏斗埋点 ----
    funnel = caa.Caa(c.fp, c.lang)
    if cfg.get("send_caa", True):
        funnel.page_shown(c.s)
        time.sleep(2)
        funnel.focus(c.s)
        time.sleep(0.5)
        funnel.continue_clicked(c.s)
        funnel.registration_start(c.s)

    print("[3] 发送验证码")
    c.send_otp(email)

    print("[4] 等待验证码邮件 (自动收取)")
    try:
        code, msg = tm.wait_otp(
            box["id"], timeout=poll_timeout,
            sender_contains=cfg.get("otp_sender_contains"),
            subject_contains=cfg.get("otp_subject_contains"),
            since_ms=since_ms,
        )
    except tempmail.TempMailError as e:
        print(f"    失败: {e}")
        print("    提示: 可登录 https://chat-tempmail.com 手动查看该邮箱收件箱")
        raise
    print(f"    验证码 = {code}  (来自 {(msg.get('from_address') or '').strip()})")

    if cfg.get("send_caa", True):
        funnel.otp_typed(c.s)

    print("[5] 校验验证码")
    c.confirm_otp(code)

    first = cfg.get("first_name") or "三郎"    # 名
    last = cfg.get("last_name") or "近藤"      # 姓
    dob = cfg.get("date_of_birth") or random_dob()

    if cfg.get("send_caa", True):
        funnel.otp_success(c.s)
        funnel.birthday_start(c.s)
        time.sleep(3)
        funnel.birthday_next(c.s)

    print(f"[6] 完成注册  {last} {first}  {dob}")
    data = c.register_completion(dob, first, last)
    uid = (data.get("auth_meta_session") or {}).get("frl_account_id")
    print(f"    account_id = {uid}")

    if cfg.get("send_caa", True):
        funnel.create_success(c.s, uid)
        funnel.login_success(c.s, uid)

    # ---- falco 埋点（可选）----
    if cfg.get("send_falco", True):
        falco.send(c.s, c.fp, c.lang, str(uuid.uuid4()), [
            falco.perf("https://muse.ai/"),
            falco.interaction("https://muse.ai/", "page_load", "main_app", "page_load"),
        ])

    print("[7] 激活会话（accept-tos）")
    for _ in range(2):
        c.accept_tos()
        if c.is_live():
            break
        time.sleep(1.2)
    print(f"    invite = {'200 活号' if c.is_live() else '401/403 无 hatch 权限'}")

    print("[8] 唤醒 VM")
    vm = c.wake_vm()
    print(f"    vm_id = {vm}")

    c.stateful_sharing()

    print(f"[9] 兑换邀请码  {invite_code or '(未配置)'}")
    if invite_code:
        ok, reason = c.redeem(invite_code)
        print(f"    redeem = {ok} / {reason}")
        if ok:
            own = c.scratch_code()
            print(f"[10] 自己的邀请码 = {own}")
        else:
            print("    失败原因映射: already_redeemed=本账号已用过码  "
                  "used_up=该码已达使用上限  invalid=码不存在")
    else:
        print("    跳过兑换")

    # ---- 收尾 ----
    if cfg.get("keep_mailbox", False):
        print(f"\n邮箱已保留 ({email})，可在 https://chat-tempmail.com 继续查看")
    else:
        print("\n提示: 临时邮箱默认 1 小时过期；如需长期收信可调大 tempmail_expiry")

    sess = c.session_cookie()
    print(f"hatch_sess 长度 = {len(sess or '')}")
    tm.close()
    c.close()


if __name__ == "__main__":
    try:
        main()
    except (MuseError, tempmail.TempMailError) as e:
        print(f"失败: {e}")
        sys.exit(1)
