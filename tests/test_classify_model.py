"""意图分类模型独立配置（CLASSIFY_MODEL_NAME）测试"""
import os
import unittest

from XianyuAgent import ClassifyAgent, DefaultAgent, PriceAgent, TechAgent, safe_filter


class FakeClient:
    """捕获 chat.completions.create 参数的假客户端"""

    def __init__(self, reply="default"):
        self.captured = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.captured.append(kwargs)

                class _Msg:
                    content = reply

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class ClassifyModelTestCase(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            k: os.environ.get(k) for k in ("MODEL_NAME", "CLASSIFY_MODEL_NAME")
        }
        os.environ["MODEL_NAME"] = "qwen-max"

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _gen(self, agent_cls, client):
        agent = agent_cls(client, "system prompt", safe_filter)
        agent.generate(user_msg="在吗", item_desc="商品", context="")
        return client.captured[-1]["model"]

    def test_classify_uses_dedicated_model_when_set(self):
        os.environ["CLASSIFY_MODEL_NAME"] = "qwen-turbo"
        self.assertEqual(self._gen(ClassifyAgent, FakeClient()), "qwen-turbo")

    def test_classify_falls_back_to_model_name(self):
        os.environ.pop("CLASSIFY_MODEL_NAME", None)
        self.assertEqual(self._gen(ClassifyAgent, FakeClient()), "qwen-max")

    def test_other_agents_unaffected_by_classify_model(self):
        os.environ["CLASSIFY_MODEL_NAME"] = "qwen-turbo"
        for cls in (DefaultAgent, PriceAgent, TechAgent):
            with self.subTest(agent=cls.__name__):
                self.assertEqual(self._gen(cls, FakeClient()), "qwen-max")


if __name__ == "__main__":
    unittest.main()
