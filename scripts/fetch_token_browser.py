"""用真实浏览器获取闲鱼 token 并刷新 Cookie（宿主机运行，非容器内）。

为什么要这个脚本
----------------
纯 HTTP 重放（requests/curl_cffi）拿不出浏览器运行时那套风控 JS（um.js/无线保镖）
产出的加密信号，账号被点名后会持续弹 RGV587。这里用 Playwright 驱动**真实 Chrome**，
让页面自己那套 JS 去取 token；滑块出现时你在弹出的窗口里手动过，然后脚本把浏览器
现场铸造的最新 Cookie（含刷新过的 _m_h5_tk / x5sec 等）写回 .env，再交给现有 bot 使用。

用法
----
    # 一次性准备（宿主机，macOS/glibc Linux；Alpine 容器内不支持 Playwright）
    pip install playwright
    playwright install chromium        # 或用系统 Chrome：见下方 channel

    python scripts/fetch_token_browser.py

流程：脚本打开一个浏览器窗口 → 若未登录请扫码登录 → 点开「消息」页触发取 token
→ 若弹滑块当场手动过 → 抓到 token 后自动把最新 Cookie 写回 .env 并退出。

环境变量（可选）：
    HEADLESS=1          无头模式（不推荐，滑块/扫码需要你看得见）
    BROWSER_CHANNEL=chrome   用系统安装的 Chrome（默认用 Playwright 自带 chromium）
    CAPTURE_TIMEOUT=300 等待抓到 token 的秒数上限
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from loguru import logger

TOKEN_API_MARK = "mtop.taobao.idlemessage.pc.login.token"
ENV_PATH = os.path.join(os.getcwd(), ".env")


def parse_cookie_str(cookies_str: str):
    """把 'a=1; b=2' 解析成 Playwright add_cookies 需要的结构。"""
    out = []
    for part in (cookies_str or "").split("; "):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": ".goofish.com",
            "path": "/",
        })
    return out


def write_cookies_to_env(cookie_str: str) -> None:
    """把新的 COOKIES_STR 写回 .env（不存在则追加）。"""
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(f"COOKIES_STR={cookie_str}\n")
        logger.success("已创建 .env 并写入 COOKIES_STR")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if "COOKIES_STR=" in content:
        content = re.sub(r"COOKIES_STR=.*", f"COOKIES_STR={cookie_str}", content)
    else:
        content = content.rstrip("\n") + f"\nCOOKIES_STR={cookie_str}\n"
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    logger.success("已把最新 Cookie 写回 .env 的 COOKIES_STR")


async def run() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("未安装 Playwright。请先在宿主机执行: pip install playwright && playwright install chromium")
        return 1

    load_dotenv()
    existing_cookie = os.getenv("COOKIES_STR", "")
    headless = os.getenv("HEADLESS", "0") == "1"
    channel = os.getenv("BROWSER_CHANNEL", "").strip() or None
    timeout = int(os.getenv("CAPTURE_TIMEOUT", "300"))

    captured = {}  # {"token": ..., "raw": ...}

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": headless,
            # 降低自动化痕迹；真实节奏/JS 仍由页面本身产生
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if channel:
            launch_kwargs["channel"] = channel
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            logger.error(f"启动浏览器失败: {e}")
            logger.error("若指定了系统 Chrome 失败，可去掉 BROWSER_CHANNEL 用自带 chromium，或先 playwright install chromium")
            return 1

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        if existing_cookie and "your_cookies_here" not in existing_cookie:
            cookies = parse_cookie_str(existing_cookie)
            if cookies:
                await context.add_cookies(cookies)
                logger.info(f"已注入 .env 中的 {len(cookies)} 个 Cookie（可能免扫码）")

        page = await context.new_page()

        async def on_response(resp):
            if TOKEN_API_MARK not in resp.url:
                return
            try:
                body = await resp.json()
            except Exception:
                return
            ret = body.get("ret", []) if isinstance(body, dict) else []
            if any("SUCCESS::调用成功" in r for r in ret):
                token = (body.get("data") or {}).get("accessToken")
                if token:
                    captured["token"] = token
                    captured["raw"] = body
                    logger.success("✅ 已从页面抓到 token")
            elif any(("RGV587" in r or "被挤爆" in r or "FAIL_SYS_USER_VALIDATE" in r) for r in ret):
                logger.warning(f"⚠️ 页面取 token 时仍触发验证: {ret} —— 请在浏览器窗口里手动过滑块，然后重新点开消息页")

        page.on("response", on_response)

        logger.info("正在打开闲鱼首页...")
        try:
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"首页加载超时（可忽略，继续）: {e}")

        print("\n" + "=" * 60)
        print("请在弹出的浏览器窗口里完成以下操作：")
        print("  1) 若未登录 → 扫码登录闲鱼")
        print("  2) 点开右上角「消息」，进入聊天页（这会触发页面去取 token）")
        print("  3) 若弹出滑块 → 手动拖过")
        print(f"脚本会自动等待并抓取 token（最多 {timeout} 秒）...")
        print("=" * 60 + "\n")

        # 主动尝试导航到消息页触发取 token（路由变化时页面会自行请求）
        try:
            await page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        waited = 0
        while "token" not in captured and waited < timeout:
            await asyncio.sleep(2)
            waited += 2

        if "token" not in captured:
            logger.error(f"超时未抓到 token（{timeout}s）。可能没点开消息页或滑块未过。可重跑并加大 CAPTURE_TIMEOUT。")
            await browser.close()
            return 1

        # 现场导出最新 Cookie（含刷新过的 _m_h5_tk / x5sec 等）
        all_cookies = await context.cookies()
        # 按名去重，保留最后一个
        seen = {}
        for c in all_cookies:
            seen[c["name"]] = c["value"]
        cookie_str = "; ".join(f"{k}={v}" for k, v in seen.items())

        await browser.close()

    write_cookies_to_env(cookie_str)
    logger.success("完成。token 已通过真实浏览器取得，最新 Cookie 已写回 .env。")
    logger.info("接下来：make restart（用新 Cookie 重启容器），再 make logs 观察是否还报 RGV587。")
    logger.info(f"（本次抓到的 token 前 12 位：{captured['token'][:12]}… ，仅供确认；bot 会用新 Cookie 自行刷新）")
    return 0


def main():
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>")
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        logger.warning("已取消")
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
