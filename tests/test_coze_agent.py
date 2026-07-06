"""CozeReplyBot 测试（HTTP 层打桩，不发真实请求）"""
import json
import os
import tempfile
import unittest

import requests

from context_manager import ChatContextManager
from coze_agent import CozeReplyBot


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeCozeSession:
    """模拟 Coze v3 chat API 的三个端点"""

    def __init__(self, create_resp=None, retrieve_resps=None, message_list_resp=None):
        self.calls = []  # [(method, url, kwargs)]
        self.create_resp = create_resp or {
            "code": 0,
            "data": {"id": "chatapi_1", "conversation_id": "conv_new", "status": "in_progress"},
        }
        self.retrieve_resps = retrieve_resps if retrieve_resps is not None else [
            {"code": 0, "data": {"id": "chatapi_1", "status": "completed"}},
        ]
        self.message_list_resp = message_list_resp or {
            "code": 0,
            "data": [
                {"type": "verbose", "content": "{}"},
                {"type": "answer", "content": "亲，这个可以88包邮"},
                {"type": "follow_up", "content": "还有什么问题吗"},
            ],
        }

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return FakeResponse(self.create_resp)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if "retrieve" in url:
            return FakeResponse(self.retrieve_resps.pop(0))
        return FakeResponse(self.message_list_resp)

    # --- 断言辅助 ---
    def create_call(self):
        return next(c for c in self.calls if c[0] == "post")

    def create_payload(self):
        return self.create_call()[2]["json"]

    def create_params(self):
        return self.create_call()[2].get("params", {})


class CozeBotTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cm = ChatContextManager(db_path=os.path.join(self._tmpdir.name, "test.db"))

    def tearDown(self):
        self._tmpdir.cleanup()

    def make_bot(self, fake_session):
        bot = CozeReplyBot(
            context_manager=self.cm,
            api_token="pat_test",
            bot_id="bot_test",
            poll_interval=0,
            timeout=5,
        )
        bot.session = fake_session
        return bot


class TestConversationMapping(CozeBotTestCase):
    def test_first_message_creates_conversation_and_saves_mapping(self):
        fake = FakeCozeSession()
        bot = self.make_bot(fake)

        bot.generate_reply("在吗", "商品信息", [], chat_id="chat_1")

        # 首次调用不带conversation_id
        self.assertNotIn("conversation_id", fake.create_params())
        # Coze返回的conversation_id被持久化
        self.assertEqual(self.cm.get_coze_conversation("chat_1"), "conv_new")

    def test_reuses_existing_conversation(self):
        self.cm.save_coze_conversation("chat_1", "conv_existing")
        fake = FakeCozeSession(create_resp={
            "code": 0,
            "data": {"id": "chatapi_1", "conversation_id": "conv_existing", "status": "in_progress"},
        })
        bot = self.make_bot(fake)

        bot.generate_reply("在吗", "商品信息", [], chat_id="chat_1")

        self.assertEqual(fake.create_params().get("conversation_id"), "conv_existing")


class TestRequestAssembly(CozeBotTestCase):
    def test_sends_custom_variables_with_item_info_and_bargain_count(self):
        fake = FakeCozeSession()
        bot = self.make_bot(fake)
        context = [{"role": "system", "content": "议价次数: 2"}]

        bot.generate_reply("能便宜点吗", "【商品】iPhone 12", context, chat_id="chat_1")

        payload = fake.create_payload()
        self.assertEqual(payload["custom_variables"]["item_info"], "【商品】iPhone 12")
        self.assertEqual(payload["custom_variables"]["bargain_count"], "2")
        self.assertEqual(payload["bot_id"], "bot_test")
        self.assertFalse(payload["stream"])
        self.assertTrue(payload["auto_save_history"])
        # 只转发当前消息
        self.assertEqual(len(payload["additional_messages"]), 1)
        self.assertEqual(payload["additional_messages"][0]["content"], "能便宜点吗")


class TestReplyBehavior(CozeBotTestCase):
    def test_normal_reply_with_default_intent(self):
        bot = self.make_bot(FakeCozeSession())
        reply, intent = bot.generate_reply("在吗", "商品", [], chat_id="c1")
        self.assertEqual(reply, "亲，这个可以88包邮")
        self.assertEqual(intent, "default")

    def test_price_message_returns_price_intent(self):
        bot = self.make_bot(FakeCozeSession())
        _, intent = bot.generate_reply("能便宜点吗", "商品", [], chat_id="c1")
        self.assertEqual(intent, "price")

    def test_dash_answer_means_no_reply(self):
        fake = FakeCozeSession(message_list_resp={
            "code": 0, "data": [{"type": "answer", "content": " - "}],
        })
        bot = self.make_bot(fake)
        reply, intent = bot.generate_reply("你是AI吗", "商品", [], chat_id="c1")
        self.assertEqual(reply, "-")
        self.assertEqual(intent, "no_reply")

    def test_safe_filter_applied_to_answer(self):
        fake = FakeCozeSession(message_list_resp={
            "code": 0, "data": [{"type": "answer", "content": "加我微信详聊"}],
        })
        bot = self.make_bot(fake)
        reply, _ = bot.generate_reply("在吗", "商品", [], chat_id="c1")
        self.assertEqual(reply, "[安全提醒]请通过平台沟通")

    def test_polls_until_completed(self):
        fake = FakeCozeSession(retrieve_resps=[
            {"code": 0, "data": {"id": "chatapi_1", "status": "in_progress"}},
            {"code": 0, "data": {"id": "chatapi_1", "status": "in_progress"}},
            {"code": 0, "data": {"id": "chatapi_1", "status": "completed"}},
        ])
        bot = self.make_bot(fake)
        reply, _ = bot.generate_reply("在吗", "商品", [], chat_id="c1")
        self.assertEqual(reply, "亲，这个可以88包邮")
        retrieve_calls = [c for c in fake.calls if "retrieve" in c[1]]
        self.assertEqual(len(retrieve_calls), 3)


class TestErrorHandling(CozeBotTestCase):
    def test_failed_status_raises(self):
        fake = FakeCozeSession(retrieve_resps=[
            {"code": 0, "data": {"id": "chatapi_1", "status": "failed",
                                 "last_error": {"code": 5000, "msg": "internal error"}}},
        ])
        bot = self.make_bot(fake)
        with self.assertRaises(RuntimeError):
            bot.generate_reply("在吗", "商品", [], chat_id="c1")

    def test_api_error_code_raises(self):
        fake = FakeCozeSession(create_resp={"code": 4000, "msg": "bad request"})
        bot = self.make_bot(fake)
        with self.assertRaises(RuntimeError):
            bot.generate_reply("在吗", "商品", [], chat_id="c1")

    def test_poll_timeout_raises(self):
        endless = [{"code": 0, "data": {"id": "chatapi_1", "status": "in_progress"}}] * 1000
        fake = FakeCozeSession(retrieve_resps=endless)
        bot = CozeReplyBot(
            context_manager=self.cm, api_token="t", bot_id="b",
            poll_interval=0, timeout=0,  # 立即超时
        )
        bot.session = fake
        with self.assertRaises(TimeoutError):
            bot.generate_reply("在吗", "商品", [], chat_id="c1")

    def test_missing_config_raises(self):
        env_backup = {k: os.environ.pop(k, None) for k in ("COZE_API_TOKEN", "COZE_BOT_ID")}
        try:
            with self.assertRaises(ValueError):
                CozeReplyBot(context_manager=self.cm)
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
