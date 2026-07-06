import os
import time
from typing import Dict, List, Optional

import requests
from loguru import logger

from XianyuAgent import safe_filter, match_price_intent, extract_bargain_count


class CozeReplyBot:
    """Coze回复后端：把买家消息直接转发给Coze bot，用其回复。

    与内置XianyuReplyBot对齐同一调用契约：
        generate_reply(user_msg, item_desc, context, chat_id) -> (reply, intent)

    - 对话记忆由Coze维护：每个闲鱼会话映射一个Coze conversation_id（持久化到SQLite），
      每次只转发当前消息。
    - 商品信息与议价次数通过custom_variables传入，bot prompt里用
      {{item_info}} / {{bargain_count}} 占位。
    - intent用本地关键词规则判断（price/default），供main.py议价计数逻辑使用。
    """

    # Coze chat的非终态，见 https://www.coze.cn/docs/developer_guides/chat_v3
    _PENDING_STATUSES = {"created", "in_progress"}

    def __init__(self, context_manager, api_token=None, bot_id=None, base_url=None,
                 poll_interval=0.5, timeout=60):
        self.api_token = api_token or os.getenv("COZE_API_TOKEN")
        self.bot_id = bot_id or os.getenv("COZE_BOT_ID")
        self.base_url = (base_url or os.getenv("COZE_BASE_URL", "https://api.coze.cn")).rstrip('/')

        if not self.api_token or not self.bot_id:
            raise ValueError("Coze模式需要配置 COZE_API_TOKEN 和 COZE_BOT_ID")

        self.context_manager = context_manager
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.last_intent = None

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        })

    def generate_reply(self, user_msg: str, item_desc: str, context: Optional[List[Dict]] = None,
                       chat_id: Optional[str] = None) -> tuple:
        """生成回复，返回 (回复内容, 意图)"""
        intent = 'price' if match_price_intent(user_msg) else 'default'
        bargain_count = extract_bargain_count(context or [])

        chat_data = self._create_chat(user_msg, item_desc, bargain_count, chat_id)
        chat_data = self._wait_for_completion(chat_data)
        answer = self._fetch_answer(chat_data)

        if answer == "-":
            logger.info("Coze bot判定无需回复")
            self.last_intent = 'no_reply'
            return "-", 'no_reply'

        self.last_intent = intent
        return safe_filter(answer), intent

    def _create_chat(self, user_msg: str, item_desc: str, bargain_count: int, chat_id: str):
        """发起对话。已有conversation映射则复用，否则让Coze新建并保存映射"""
        conversation_id = (
            self.context_manager.get_coze_conversation(chat_id) if chat_id else None
        )

        payload = {
            "bot_id": self.bot_id,
            "user_id": str(chat_id or "xianyu_user"),
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {"role": "user", "content": user_msg, "content_type": "text"}
            ],
            "custom_variables": {
                "item_info": item_desc,
                "bargain_count": str(bargain_count),
            },
        }
        params = {}
        if conversation_id:
            params["conversation_id"] = conversation_id

        resp = self.session.post(f"{self.base_url}/v3/chat", json=payload,
                                 params=params, timeout=30)
        chat_data = self._check(resp)["data"]

        # 保存/更新conversation映射（首次由Coze创建）
        new_conversation_id = chat_data.get("conversation_id")
        if chat_id and new_conversation_id and new_conversation_id != conversation_id:
            self.context_manager.save_coze_conversation(chat_id, new_conversation_id)

        return chat_data

    def _wait_for_completion(self, chat_data):
        """轮询chat状态直到completed，失败/超时抛异常"""
        deadline = time.time() + self.timeout
        conversation_id = chat_data.get("conversation_id")

        while True:
            status = chat_data.get("status")
            if status == "completed":
                return chat_data
            if status not in self._PENDING_STATUSES:
                raise RuntimeError(
                    f"Coze chat状态异常: {status}, last_error={chat_data.get('last_error')}"
                )
            if time.time() >= deadline:
                raise TimeoutError(f"Coze回复超时（{self.timeout}s）")

            time.sleep(self.poll_interval)
            resp = self.session.get(
                f"{self.base_url}/v3/chat/retrieve",
                params={
                    "chat_id": chat_data["id"],
                    "conversation_id": conversation_id,
                },
                timeout=30,
            )
            chat_data = self._check(resp)["data"]
            # retrieve响应可能不含conversation_id，补回以便后续取消息
            chat_data.setdefault("conversation_id", conversation_id)

    def _fetch_answer(self, chat_data) -> str:
        """取回type=answer的回复消息"""
        resp = self.session.get(
            f"{self.base_url}/v3/chat/message/list",
            params={
                "chat_id": chat_data["id"],
                "conversation_id": chat_data.get("conversation_id"),
            },
            timeout=30,
        )
        messages = self._check(resp)["data"]

        answer = next(
            (m.get("content", "") for m in messages if m.get("type") == "answer"),
            None,
        )
        if answer is None:
            raise RuntimeError(f"Coze未返回answer消息: {messages}")
        return answer.strip()

    @staticmethod
    def _check(resp):
        """校验HTTP状态与Coze业务code"""
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Coze API错误: code={data.get('code')}, msg={data.get('msg')}")
        return data
