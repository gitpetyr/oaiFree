FROM python:3.12-slim

# Playwright 系统依赖 + Xvfb（因为浏览器始终以 headed 模式运行以绕过检测）
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    xauth \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖
RUN pip install --no-cache-dir httpx playwright playwright-stealth

# 安装 Chromium 及其系统依赖
RUN playwright install --with-deps chromium

COPY main.py gptmail.py config.json ./

# 预创建输出目录
RUN mkdir -p /app/tokens /app/logs

# 通过 xvfb-run 提供虚拟显示器，使 headed 模式的 Chromium 可以正常运行
ENTRYPOINT ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "python", "-u", "main.py"]
