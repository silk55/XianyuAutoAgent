"""ChatContextManager 的 Coze 会话映射测试"""
import os
import tempfile
import unittest

from context_manager import ChatContextManager


class TestCozeConversationMapping(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cm = ChatContextManager(db_path=os.path.join(self._tmpdir.name, "test.db"))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.cm.get_coze_conversation("chat_x"))

    def test_save_and_get_roundtrip(self):
        self.cm.save_coze_conversation("chat_1", "conv_abc")
        self.assertEqual(self.cm.get_coze_conversation("chat_1"), "conv_abc")

    def test_save_overwrites_existing(self):
        self.cm.save_coze_conversation("chat_1", "conv_old")
        self.cm.save_coze_conversation("chat_1", "conv_new")
        self.assertEqual(self.cm.get_coze_conversation("chat_1"), "conv_new")

    def test_mapping_survives_reopen(self):
        """重启进程后映射仍在（持久化到SQLite）"""
        self.cm.save_coze_conversation("chat_1", "conv_abc")
        cm2 = ChatContextManager(db_path=self.cm.db_path)
        self.assertEqual(cm2.get_coze_conversation("chat_1"), "conv_abc")


if __name__ == "__main__":
    unittest.main()
