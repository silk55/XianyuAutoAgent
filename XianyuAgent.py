import re
from typing import List, Dict
import os
from openai import OpenAI
from loguru import logger


# ---------- 模块级共用逻辑（内置后端与Coze后端共用） ----------

# 价格意图规则（纯本地判断，不走LLM）
PRICE_KEYWORDS = ['便宜', '价', '砍价', '少点']
PRICE_PATTERNS = [r'\d+元', r'能少\d+']


def safe_filter(text: str) -> str:
    """安全过滤模块：命中疑似站外引流词时整条替换为安全提醒"""
    blocked_phrases = ["微信", "QQ", "支付宝", "银行卡", "线下"]
    return "[安全提醒]请通过平台沟通" if any(p in text for p in blocked_phrases) else text


def match_price_intent(user_msg: str) -> bool:
    """用关键词/正则规则判断是否为议价消息"""
    text_clean = re.sub(r'[^\w一-龥]', '', user_msg)
    if any(kw in text_clean for kw in PRICE_KEYWORDS):
        return True
    return any(re.search(p, text_clean) for p in PRICE_PATTERNS)


def extract_bargain_count(context: List[Dict]) -> int:
    """从上下文的system消息中提取议价次数，没有则返回0"""
    for msg in context:
        if msg['role'] == 'system' and '议价次数' in msg['content']:
            match = re.search(r'议价次数[:：]\s*(\d+)', msg['content'])
            if match:
                return int(match.group(1))
    return 0


class XianyuReplyBot:
    def __init__(self):
        # 初始化OpenAI客户端
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        self._init_system_prompts()
        self._init_agents()
        self.router = IntentRouter(self.agents['classify'])
        self.last_intent = None  # 记录最后一次意图


    def _init_agents(self):
        """初始化各领域Agent"""
        self.agents = {
            'classify':ClassifyAgent(self.client, self.classify_prompt, safe_filter),
            'price': PriceAgent(self.client, self.price_prompt, safe_filter),
            'tech': TechAgent(self.client, self.tech_prompt, safe_filter),
            'default': DefaultAgent(self.client, self.default_prompt, safe_filter),
        }

    def _init_system_prompts(self):
        """初始化各Agent专用提示词，优先加载用户自定义文件，否则使用Example默认文件"""
        prompt_dir = "prompts"
        
        def load_prompt_content(name: str) -> str:
            """尝试加载提示词文件"""
            # 优先尝试加载 target.txt
            target_path = os.path.join(prompt_dir, f"{name}.txt")
            if os.path.exists(target_path):
                file_path = target_path
            else:
                # 尝试默认提示词 target_example.txt
                file_path = os.path.join(prompt_dir, f"{name}_example.txt")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                logger.debug(f"已加载 {name} 提示词，路径: {file_path}, 长度: {len(content)} 字符")
                return content

        try:
            # 加载分类提示词
            self.classify_prompt = load_prompt_content("classify_prompt")
            # 加载价格提示词
            self.price_prompt = load_prompt_content("price_prompt")
            # 加载技术提示词
            self.tech_prompt = load_prompt_content("tech_prompt")
            # 加载默认提示词
            self.default_prompt = load_prompt_content("default_prompt")
                
            logger.info("成功加载所有提示词")
        except Exception as e:
            logger.error(f"加载提示词时出错: {e}")
            raise

    def format_history(self, context: List[Dict]) -> str:
        """格式化对话历史，返回完整的对话记录"""
        # 过滤掉系统消息，只保留用户和助手的对话
        user_assistant_msgs = [msg for msg in context if msg['role'] in ['user', 'assistant']]
        return "\n".join([f"{msg['role']}: {msg['content']}" for msg in user_assistant_msgs])

    def generate_reply(self, user_msg: str, item_desc: str, context: List[Dict],
                       chat_id: str = None) -> tuple:
        """生成回复主流程，返回 (回复内容, 意图)。

        chat_id为后端契约参数（Coze后端用于会话映射），内置后端不使用。
        意图通过返回值传递而非只存在self.last_intent上，
        避免多条消息并发处理时读到别的会话的意图。
        """
        # 记录用户消息
        # logger.debug(f'用户所发消息: {user_msg}')
        
        formatted_context = self.format_history(context)
        # logger.debug(f'对话历史: {formatted_context}')
        
        # 1. 路由决策
        detected_intent = self.router.detect(user_msg, item_desc, formatted_context)



        # 2. 获取对应Agent

        internal_intents = {'classify'}  # 定义不对外开放的Agent

        if detected_intent == 'no_reply':
            # 无需回复的情况
            logger.info(f'意图识别完成: no_reply - 无需回复')
            self.last_intent = 'no_reply'
            return "-", 'no_reply'  # 返回特殊标记，表示无需回复
        elif detected_intent in self.agents and detected_intent not in internal_intents:
            agent = self.agents[detected_intent]
            intent = detected_intent
        else:
            agent = self.agents['default']
            intent = 'default'
        logger.info(f'意图识别完成: {intent}')
        self.last_intent = intent  # 保存当前意图（并发场景请使用返回值）

        # 3. 获取议价次数
        bargain_count = extract_bargain_count(context)
        logger.info(f'议价次数: {bargain_count}')

        # 4. 生成回复
        reply = agent.generate(
            user_msg=user_msg,
            item_desc=item_desc,
            context=formatted_context,
            bargain_count=bargain_count
        )
        return reply, intent
    
    def reload_prompts(self):
        """重新加载所有提示词"""
        logger.info("正在重新加载提示词...")
        self._init_system_prompts()
        self._init_agents()
        logger.info("提示词重新加载完成")


class IntentRouter:
    """意图路由决策器"""

    def __init__(self, classify_agent):
        self.rules = {
            'tech': {  # 技术类优先判定
                'keywords': ['参数', '规格', '型号', '连接', '对比'],
                'patterns': [
                    r'和.+比'
                ]
            },
            # price规则见模块级 PRICE_KEYWORDS / PRICE_PATTERNS（与Coze后端共用）
        }
        self.classify_agent = classify_agent

    def detect(self, user_msg: str, item_desc, context) -> str:
        """三级路由策略（技术优先）"""
        text_clean = re.sub(r'[^\w\u4e00-\u9fa5]', '', user_msg)
        
        # 1. 技术类关键词优先检查
        if any(kw in text_clean for kw in self.rules['tech']['keywords']):
            # logger.debug(f"技术类关键词匹配: {[kw for kw in self.rules['tech']['keywords'] if kw in text_clean]}")
            return 'tech'
            
        # 2. 技术类正则优先检查
        for pattern in self.rules['tech']['patterns']:
            if re.search(pattern, text_clean):
                # logger.debug(f"技术类正则匹配: {pattern}")
                return 'tech'

        # 3. 价格类检查（规则与Coze后端共用）
        if match_price_intent(user_msg):
            return 'price'

        # 4. 大模型兜底
        # logger.debug("使用大模型进行意图分类")
        llm_intent = self.classify_agent.generate(
            user_msg=user_msg,
            item_desc=item_desc,
            context=context
        )
        # 归一化：模型输出可能带空白/大小写/多余文字，未命中已知类别时落到default
        normalized = (llm_intent or '').strip().lower()
        if normalized not in {'price', 'tech', 'default', 'no_reply'}:
            logger.warning(f"意图分类输出异常: {llm_intent!r}，回退为default")
            return 'default'
        return normalized


class BaseAgent:
    """Agent基类"""

    def __init__(self, client, system_prompt, safety_filter):
        self.client = client
        self.system_prompt = system_prompt
        self.safety_filter = safety_filter

    def _get_model(self) -> str:
        """本Agent使用的模型，子类可覆盖"""
        return os.getenv("MODEL_NAME", "qwen-max")

    def generate(self, user_msg: str, item_desc: str, context: str, bargain_count: int = 0) -> str:
        """生成回复模板方法"""
        messages = self._build_messages(user_msg, item_desc, context)
        response = self._call_llm(messages)
        return self.safety_filter(response)

    def _build_messages(self, user_msg: str, item_desc: str, context: str) -> List[Dict]:
        """构建消息链"""
        return [
            {"role": "system", "content": f"【商品信息】{item_desc}\n【你与客户对话历史】{context}\n{self.system_prompt}"},
            {"role": "user", "content": user_msg}
        ]

    def _call_llm(self, messages: List[Dict], temperature: float = 0.4) -> str:
        """调用大模型"""
        response = self.client.chat.completions.create(
            model=self._get_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=500,
            top_p=0.8
        )
        return response.choices[0].message.content


class PriceAgent(BaseAgent):
    """议价处理Agent"""

    def generate(self, user_msg: str, item_desc: str, context: str, bargain_count: int=0) -> str:
        """重写生成逻辑"""
        dynamic_temp = self._calc_temperature(bargain_count)
        messages = self._build_messages(user_msg, item_desc, context)
        messages[0]['content'] += f"\n▲当前议价轮次：{bargain_count}"

        response = self.client.chat.completions.create(
            model=self._get_model(),
            messages=messages,
            temperature=dynamic_temp,
            max_tokens=500,
            top_p=0.8
        )
        return self.safety_filter(response.choices[0].message.content)

    def _calc_temperature(self, bargain_count: int) -> float:
        """动态温度策略"""
        return min(0.3 + bargain_count * 0.15, 0.9)


class TechAgent(BaseAgent):
    """技术咨询Agent"""
    def generate(self, user_msg: str, item_desc: str, context: str, bargain_count: int=0) -> str:
        """重写生成逻辑"""
        messages = self._build_messages(user_msg, item_desc, context)
        # messages[0]['content'] += "\n▲知识库：\n" + self._fetch_tech_specs()

        response = self.client.chat.completions.create(
            model=self._get_model(),
            messages=messages,
            temperature=0.4,
            max_tokens=500,
            top_p=0.8,
            extra_body={
                "enable_search": True,
            }
        )

        return self.safety_filter(response.choices[0].message.content)


    # def _fetch_tech_specs(self) -> str:
    #     """模拟获取技术参数（可连接数据库）"""
    #     return "功率：200W@8Ω\n接口：XLR+RCA\n频响：20Hz-20kHz"


class ClassifyAgent(BaseAgent):
    """意图识别Agent"""

    def _get_model(self) -> str:
        """分类是纯判别任务，可单独配小模型（如qwen-turbo）压时延；未配置时回落MODEL_NAME"""
        return os.getenv("CLASSIFY_MODEL_NAME") or os.getenv("MODEL_NAME", "qwen-max")


class DefaultAgent(BaseAgent):
    """默认处理Agent"""

    def _call_llm(self, messages: List[Dict], temperature: float = 0.7) -> str:
        """默认回复使用较高温度"""
        return super()._call_llm(messages, temperature=temperature)