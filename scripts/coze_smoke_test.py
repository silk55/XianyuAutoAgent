"""Coze后端连通性冒烟测试：用真实配置发一条测试消息。

用法：
    python scripts/coze_smoke_test.py "测试消息"

需要.env中已配置 COZE_API_TOKEN / COZE_BOT_ID（可选 COZE_BASE_URL）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from loguru import logger

from context_manager import ChatContextManager
from coze_agent import CozeReplyBot


def main():
    load_dotenv()
    user_msg = sys.argv[1] if len(sys.argv) > 1 else "你好，这个还在吗？"

    with tempfile.TemporaryDirectory() as td:
        cm = ChatContextManager(db_path=os.path.join(td, "smoke.db"))
        bot = CozeReplyBot(context_manager=cm)

        logger.info(f"发送测试消息: {user_msg}")
        reply, intent = bot.generate_reply(
            user_msg,
            '{"title": "冒烟测试商品", "price_range": "¥88", "desc": "测试用"}',
            [],
            chat_id="smoke_test",
        )
        logger.success(f"✅ Coze连通正常 | intent={intent} | 回复: {reply}")
        logger.info(f"conversation映射: {cm.get_coze_conversation('smoke_test')}")


if __name__ == "__main__":
    main()
