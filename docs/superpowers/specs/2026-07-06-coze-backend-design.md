# Coze 回复后端设计

日期：2026-07-06
状态：已批准

## 目标

通过环境变量切换回复后端：`builtin`（现有多 Agent 流水线）或 `coze`（把买家消息直接转发给 Coze bot，用其回复）。默认 `builtin`，行为与现状完全一致。

## 决策记录

| 问题 | 决策 |
|------|------|
| 对话上下文归属 | Coze 管。每个闲鱼会话映射一个 Coze `conversation_id`，历史记忆由 Coze 维护，每次只转发当前消息。本地 SQLite 照常入库（人工接管/留痕不受影响），但不再喂给模型。 |
| 商品信息传递 | `custom_variables`。每次请求传 `{"item_info": 商品JSON, "bargain_count": "N"}`，bot prompt 用 `{{item_info}}` / `{{bargain_count}}` 占位。 |
| 本地保留的后处理 | ① 敏感词安全过滤（Coze 回复同样过 `safe_filter`）；② 议价次数统计（用 IntentRouter 的 price 关键词/正则规则判断，不走 LLM 分类）。 |
| 架构 | 鸭子类型对齐，不建抽象基类。`CozeReplyBot` 对齐现有契约 `generate_reply(user_msg, item_desc, context) -> (reply, intent)`，`handle_message` 零改动。第三个后端出现时再抽象。 |

## 环境变量

```
REPLY_BACKEND=builtin | coze        # 默认 builtin
COZE_API_TOKEN=pat_xxx               # Coze PAT 令牌（coze 模式必填）
COZE_BOT_ID=73428xxxx                # Coze bot id（coze 模式必填）
COZE_BASE_URL=https://api.coze.cn    # 海外版改 https://api.coze.com
```

## 组件

### `coze_agent.py` — `CozeReplyBot`（新文件）

- **API**：Coze v3 chat，非流式轮询：
  1. `POST {base}/v3/chat`，body 含 `bot_id`、`user_id`（用闲鱼 chat_id）、`conversation_id`（有映射时）、`additional_messages=[当前消息]`、`custom_variables`、`stream=false`、`auto_save_history=true`
  2. 轮询 `GET /v3/chat/retrieve` 直到 `status=completed`（间隔 0.5s，总超时 60s）
  3. `GET /v3/chat/message/list` 取 `type=answer` 的消息文本
- 用 requests 同步实现即可——`main.py` 已用 `asyncio.to_thread` 包裹 `generate_reply`，不阻塞事件循环。
- **会话映射**：`chat_id → conversation_id` 持久化到 SQLite 表 `coze_conversations`（重启不丢 Coze 侧记忆）。映射不存在时不传 conversation_id，让 Coze 创建，从响应中取回并保存。
- **intent**：调用 IntentRouter 的 price 规则（关键词+正则，纯本地），命中返回 `'price'`，否则 `'default'`；`main.py` 现有议价计数逻辑直接生效。
- **no_reply 约定**：Coze bot 输出 `-` 时返回 `("-", "no_reply")`，与内置后端一致。
- **错误处理**：HTTP 失败/超时/响应异常直接抛异常，由 `handle_message` 的 catch-all 记日志、不回复（与内置后端 LLM 失败行为一致）。

### 改动点

- `XianyuAgent.py`：`_safe_filter` 提为模块级函数 `safe_filter(text)`，两后端共用；IntentRouter 的 price 规则判断提为可独立调用（供 CozeReplyBot 复用）。
- `context_manager.py`：新表 `coze_conversations(chat_id TEXT PRIMARY KEY, conversation_id TEXT, last_updated)` + `get_coze_conversation` / `save_coze_conversation`。
- `main.py`：入口按 `REPLY_BACKEND` 实例化 `XianyuReplyBot` 或 `CozeReplyBot`；`ChatContextManager` 在 main 创建后注入 `XianyuLive`（原为内部自建）和 `CozeReplyBot`。coze 模式下缺 `COZE_API_TOKEN`/`COZE_BOT_ID` 时启动即报错退出。
- `.env.example`：补 4 个新变量（带注释说明默认 builtin）。

## 测试

- 单测（requests 打桩）：conversation 创建与复用、custom_variables 组装、`-` → no_reply、price 意图判断、安全过滤生效、轮询超时抛异常。
- 冒烟：提供一条命令的脚本，用真实 token 发一条测试消息验证连通性（需要用户的 Coze 配置）。

## 不做的事

- 不做流式（闲鱼 IM 协议无流式落点，见前期分析）。
- 不做抽象后端基类（两个实现，YAGNI）。
- 不改变 builtin 模式任何行为。
