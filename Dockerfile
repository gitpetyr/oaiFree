FROM python:3.12-slim

# Playwright 系统依赖 + Xvfb + Cloudflare WARP
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    xauth \
    fonts-noto-cjk \
    curl \
    gpg \
    lsb-release \
    && curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
       | gpg --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
       > /etc/apt/sources.list.d/cloudflare-client.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends cloudflare-warp \
    && apt-get purge -y curl lsb-release \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖
RUN pip install --no-cache-dir httpx playwright playwright-stealth

# 安装 Chromium 及其系统依赖
RUN playwright install --with-deps chromium

COPY main.py gptmail.py entrypoint.sh ./

# 预创建输出目录
RUN mkdir -p /app/tokens /app/logs

# 默认环境变量
ENV RUN_COUNT=0 \
    RUN_INTERVAL=0 \
    TOKEN_DIR=./tokens \
    LOG_DIR=./logs \
    HEADLESS=true \
    LOG_ENABLED=false \
    PROXY="" \
    WARP_ENABLED=false

ENTRYPOINT ["./entrypoint.sh"]
