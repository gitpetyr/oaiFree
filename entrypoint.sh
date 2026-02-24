#!/bin/bash
set -e

# ── Cloudflare WARP ──────────────────────────────────
if [ "${WARP_ENABLED}" = "true" ]; then
    echo "🌐 启动 Cloudflare WARP..."

    # 启动 dbus（warp-svc 依赖 dbus）
    mkdir -p /run/dbus
    if [ -f /run/dbus/pid ]; then
        rm /run/dbus/pid
    fi
    dbus-daemon --system --nofork &
    sleep 1

    # 启动 warp-svc 守护进程
    warp-svc &
    sleep 3

    # 首次注册（已注册则跳过）
    if ! warp-cli --accept-tos registration show &>/dev/null; then
        echo "📝 注册 WARP..."
        warp-cli --accept-tos registration new
    fi

    # 设置为代理模式（SOCKS5 on 127.0.0.1:40000）
    warp-cli --accept-tos mode proxy
    warp-cli --accept-tos proxy port 40000
    warp-cli --accept-tos connect

    # 等待连接就绪
    for i in $(seq 1 15); do
        if warp-cli --accept-tos status 2>/dev/null | grep -q "Connected"; then
            echo "✅ WARP 已连接 (socks5://127.0.0.1:40000)"
            break
        fi
        echo "   等待 WARP 连接... ($i/15)"
        sleep 2
    done

    warp-cli --accept-tos status

    # 若用户未手动指定 PROXY，则自动使用 WARP
    if [ -z "${PROXY}" ]; then
        export PROXY="socks5://127.0.0.1:40000"
    fi
fi

# ── 启动虚拟显示器 ──
echo "🖥️  启动 Xvfb 虚拟显示器..."
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
sleep 1
export DISPLAY=:99

# ── 启动主程序 ──
exec python -u main.py
