"""回复后端选择工厂测试（REPLY_BACKEND 环境变量切换）"""
import inspect
import os
import tempfile
import unittest

from context_manager import ChatContextManager


class BackendFactoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cm = ChatContextManager(db_path=os.path.join(self._tmpdir.name, "test.db"))
        # 备份并设置测试环境变量
        self._env_backup = {
            k: os.environ.get(k)
            for k in ("REPLY_BACKEND", "API_KEY", "COZE_API_TOKEN", "COZE_BOT_ID")
        }
        os.environ["API_KEY"] = "test_key"

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()


class TestCreateReplyBot(BackendFactoryTestCase):
    def test_default_is_builtin(self):
        from main import create_reply_bot
        from XianyuAgent import XianyuReplyBot
        os.environ.pop("REPLY_BACKEND", None)
        bot = create_reply_bot(self.cm)
        self.assertIsInstance(bot, XianyuReplyBot)

    def test_coze_backend(self):
        from main import create_reply_bot
        from coze_agent import CozeReplyBot
        os.environ["REPLY_BACKEND"] = "coze"
        os.environ["COZE_API_TOKEN"] = "pat_test"
        os.environ["COZE_BOT_ID"] = "bot_test"
        bot = create_reply_bot(self.cm)
        self.assertIsInstance(bot, CozeReplyBot)

    def test_coze_without_config_raises(self):
        from main import create_reply_bot
        os.environ["REPLY_BACKEND"] = "coze"
        os.environ.pop("COZE_API_TOKEN", None)
        os.environ.pop("COZE_BOT_ID", None)
        with self.assertRaises(ValueError):
            create_reply_bot(self.cm)

    def test_unknown_backend_raises(self):
        from main import create_reply_bot
        os.environ["REPLY_BACKEND"] = "dify"
        with self.assertRaises(ValueError):
            create_reply_bot(self.cm)


class TestBackendContract(unittest.TestCase):
    """两个后端的generate_reply契约对齐：(user_msg, item_desc, context, chat_id)"""

    def test_signatures_accept_chat_id(self):
        from XianyuAgent import XianyuReplyBot
        from coze_agent import CozeReplyBot
        for cls in (XianyuReplyBot, CozeReplyBot):
            params = inspect.signature(cls.generate_reply).parameters
            self.assertIn("chat_id", params, f"{cls.__name__}.generate_reply 缺少 chat_id 参数")


if __name__ == "__main__":
    unittest.main()
