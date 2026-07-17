"""容器内用无头真实 Chromium 获取闲鱼 token（替代纯 HTTP 重放）。

设计
----
纯 requests/curl_cffi 只是"静态 cookie + 手工签名"，拿不出浏览器运行时那套风控 JS
（um.js/无线保镖）产出的信号，账号被标记后持续弹 RGV587。这里在容器内常驻一个无头
Chromium：注入 cookie → 打开消息页 → 让页面自己那套 JS 去取 token → 抓包拿到 token，
同时导出被服务器刷新过的最新 cookie（_m_h5_tk / x5sec 等）交回主流程。

局限
----
无头容器里**没人能手动过滑块**。真实浏览器通常一开始就不弹滑块；万一仍弹（fetch 返回
None 且日志报验证），需在宿主机跑 scripts/fetch_token_browser.py（headful）手动过一次、
重铸 cookie 写回 .env，再重启容器。
"""
import asyncio
import os

from loguru import logger

TOKEN_API_MARK = "mtop.taobao.idlemessage.pc.login.token"


def parse_cookie_str(cookies_str: str):
    """'a=1; b=2' → Playwright add_cookies 结构。"""
    out = []
    for part in (cookies_str or "").split("; "):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            out.append({"name": name, "value": value, "domain": ".goofish.com", "path": "/"})
    return out


class BrowserTokenProvider:
    """常驻无头浏览器的 token 提供者。生命周期由 XianyuLive 管理。"""

    def __init__(self, cookies_str: str):
        self.cookies_str = cookies_str
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._captured = {}
        self._risk_hit = False
        self._lock = asyncio.Lock()

    async def start(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",                            # 容器内 root 运行必需
                "--disable-dev-shm-usage",                 # 规避容器 /dev/shm 过小
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        cookies = parse_cookie_str(self.cookies_str)
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info(f"[browser] 已注入 {len(cookies)} 个 cookie")
        self._page = await self._context.new_page()
        self._page.on("response", self._on_response)
        logger.info("[browser] 无头浏览器 token provider 已启动")

    async def _on_response(self, resp):
        if TOKEN_API_MARK not in resp.url:
            return
        try:
            body = await resp.json()
        except Exception:
            return
        if not isinstance(body, dict):
            return
        ret = body.get("ret", [])
        if any("SUCCESS::调用成功" in r for r in ret):
            token = (body.get("data") or {}).get("accessToken")
            if token:
                self._captured["token"] = token
                self._risk_hit = False
                logger.debug("[browser] 抓到 token")
        elif any(("RGV587" in r or "被挤爆" in r or "FAIL_SYS_USER_VALIDATE" in r) for r in ret):
            self._risk_hit = True
            logger.warning(f"[browser] 页面取 token 仍触发验证: {ret}")

    async def fetch_token_result(self, timeout: int = 60):
        """触发页面取 token 并等待抓包。

        返回与 XianyuApis.get_token 相同的结构 {"data": {"accessToken": ...}}，失败返回 None。
        """
        async with self._lock:
            if self._page is None:
                await self.start()
            self._captured.pop("token", None)
            self._risk_hit = False
            try:
                # 导航到消息页触发页面自身的 token 请求；已在该页则 reload
                await self._page.goto(
                    "https://www.goofish.com/im",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as e:
                logger.debug(f"[browser] 导航消息页异常（尝试 reload）: {e}")
                try:
                    await self._page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

            waited = 0
            while "token" not in self._captured and waited < timeout:
                await asyncio.sleep(1)
                waited += 1

            token = self._captured.get("token")
            if not token:
                if self._risk_hit:
                    logger.error(
                        "[browser] 无头浏览器取 token 触发滑块，容器内无法手动过。"
                        "请在宿主机运行 scripts/fetch_token_browser.py 手动过滑块并重铸 cookie，再重启容器。"
                    )
                else:
                    logger.warning(f"[browser] {timeout}s 内未抓到 token（可能未登录/cookie 失效）")
                return None
            return {"data": {"accessToken": token}}

    async def current_cookie_str(self) -> str:
        """导出浏览器上下文里刷新过的最新 cookie（按名去重）。"""
        if self._context is None:
            return self.cookies_str
        try:
            cookies = await self._context.cookies()
        except Exception:
            return self.cookies_str
        seen = {}
        for c in cookies:
            seen[c["name"]] = c["value"]
        return "; ".join(f"{k}={v}" for k, v in seen.items()) or self.cookies_str

    async def close(self):
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                res = closer()
                if res is not None:
                    await res
            except Exception:
                pass
