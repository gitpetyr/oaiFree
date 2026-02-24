#!/bin/bash
set -e

# ── Cloudflare WARP ──────────────────────────────────
if [ "${WARP_ENABLED}" = "true" ]; then
    echo "🌐 启动 Cloudflare WARP..."

    # 启动 warp-svc 守护进程
    warp-svc &
    sleep 2

    # 首次注册（已注册则跳过）
    if ! warp-cli registration show &>/dev/null; then
        echo "📝 注册 WARP..."
        warp-cli registration new --accept-tos
    fi

    # 设置为代理模式（SOCKS5 on 127.0.0.1:40000）
    warp-cli mode proxy
    warp-cli connect
    sleep 2

    # 等待连接就绪
    for i in $(seq 1 30); do
        if warp-cli status 2>/dev/null | grep -q "Connected"; then
            echo "✅ WARP 已连接 (socks5://127.0.0.1:40000)"
            break
        fi
        echo "   等待 WARP 连接... ($i/30)"
        sleep 1
    done

    # 若用户未手动指定 PROXY，则自动使用 WARP
    if [ -z "${PROXY}" ]; then
        export PROXY="socks5://127.0.0.1:40000"
    fi
fi

# ── 启动主程序（xvfb-run 提供虚拟显示器） ──
exec xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" \
    python -u main.py
