# 基础镜像：官方 Playwright(Python) 镜像，自带 Chromium + 全部系统依赖。
# 原因：纯 HTTP 重放拿不出浏览器风控 JS 的信号，容易被 mtop 判定为机器人(RGV587)；
# 改用容器内真实 Chromium(无头) 去取 token，让页面自己那套 JS 产出可信信号。
# 注意：Playwright 不支持 Alpine(musl)，故用 Debian(jammy)。
#
# 国内加速：默认走 DaoCloud 的 MCR 镜像代理拉取（微软 MCR 不受 docker registry-mirror 加速）。
# 若该代理不可用/想用官方源：--build-arg BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.47.0-jammy
ARG BASE_IMAGE=mcr.m.daocloud.io/playwright/python:v1.47.0-jammy
FROM ${BASE_IMAGE}

LABEL maintainer="coderxiu<coderxiu@qq.com>"
LABEL description="闲鱼AI客服机器人（浏览器取token版）"
LABEL version="3.0"

ENV TZ=Asia/Shanghai \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 国内加速：pip 换腾讯云源（公网通用）。可用 --build-arg PIP_INDEX_URL= 覆盖：
#   - 腾讯云服务器内网构建更快：https://mirrors.tencentyun.com/pypi/simple
#   - 回官方源：https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

# 确保 chromium 与已安装的 playwright 版本匹配（基础镜像已带，通常是快速校验）
RUN playwright install chromium

# 时区
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 运行所需目录
RUN mkdir -p data prompts

# 复制示例提示词并重命名为正式文件
COPY prompts/classify_prompt_example.txt prompts/classify_prompt.txt
COPY prompts/price_prompt_example.txt prompts/price_prompt.txt
COPY prompts/tech_prompt_example.txt prompts/tech_prompt.txt
COPY prompts/default_prompt_example.txt prompts/default_prompt.txt

# 复制必要源码
COPY main.py XianyuAgent.py XianyuApis.py context_manager.py coze_agent.py browser_token.py ./
COPY utils/ utils/

CMD ["python", "main.py"]
