"""XianyuAgent 模块级共用函数测试（Coze 后端与内置后端共用）"""
import unittest

from XianyuAgent import safe_filter, match_price_intent, extract_bargain_count


class TestSafeFilter(unittest.TestCase):
    def test_blocks_sensitive_words(self):
        self.assertEqual(safe_filter("加我微信聊"), "[安全提醒]请通过平台沟通")
        self.assertEqual(safe_filter("支付宝转账吧"), "[安全提醒]请通过平台沟通")

    def test_passes_normal_text(self):
        self.assertEqual(safe_filter("包邮，明天发货"), "包邮，明天发货")


class TestMatchPriceIntent(unittest.TestCase):
    def test_matches_price_keywords(self):
        self.assertTrue(match_price_intent("能便宜点吗"))
        self.assertTrue(match_price_intent("能少10块不"))

    def test_matches_price_patterns(self):
        self.assertTrue(match_price_intent("100元卖不卖"))

    def test_no_match_for_normal_message(self):
        self.assertFalse(match_price_intent("你好，在吗"))
        self.assertFalse(match_price_intent("什么时候发货"))


class TestExtractBargainCount(unittest.TestCase):
    def test_extracts_count_from_system_message(self):
        context = [
            {"role": "user", "content": "便宜点"},
            {"role": "system", "content": "议价次数: 3"},
        ]
        self.assertEqual(extract_bargain_count(context), 3)

    def test_returns_zero_when_absent(self):
        self.assertEqual(extract_bargain_count([{"role": "user", "content": "hi"}]), 0)
        self.assertEqual(extract_bargain_count([]), 0)


if __name__ == "__main__":
    unittest.main()
