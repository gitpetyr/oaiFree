# oaiFree

OpenAI 账号批量注册 & OAuth Token 自动获取工具。基于 Playwright 浏览器自动化，内置反检测策略，内置 Cloudflare WARP 代理。

## 功能

- 自动生成临时邮箱完成注册流程
- PKCE OAuth 2.0 授权码交换，获取 Access Token / Refresh Token
- 浏览器指纹伪装 & Cloudflare 验证盾牌自动突破
- 内置 Cloudflare WARP 代理 / 支持第三方代理
- 全部参数通过环境变量配置，无需挂载配置文件
- Token 自动保存为 JSON 文件

## 快速开始

### Docker Compose (推荐)

```bash
# 克隆仓库
git clone https://github.com/your-username/oaiFree.git
cd oaiFree

# 按需修改 docker-compose.yml 中的环境变量，然后启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

### Docker Run

```bash
docker run -d \
  --name oaifree \
  --shm-size=2g \
  -e HEADLESS=true \
  -e RUN_COUNT=0 \
  -v $(pwd)/tokens:/app/tokens \
  zhongxiaoma/oaifree:latest
```

#### 使用内置 WARP 代理

```bash
docker run -d \
  --name oaifree \
  --shm-size=2g \
  --cap-add=NET_ADMIN \
  -e WARP_ENABLED=true \
  -e HEADLESS=true \
  -v $(pwd)/tokens:/app/tokens \
  zhongxiaoma/oaifree:latest
```

#### 使用第三方代理

```bash
docker run -d \
  --name oaifree \
  --shm-size=2g \
  -e PROXY=socks5://your-proxy:1080 \
  -e HEADLESS=true \
  -v $(pwd)/tokens:/app/tokens \
  zhongxiaoma/oaifree:latest
```

### 本地运行

```bash
# 安装依赖
pip install httpx playwright playwright-stealth
playwright install --with-deps chromium

# 通过环境变量配置
export RUN_COUNT=5
export HEADLESS=true
python main.py

# 或使用传统 config.json（环境变量未设置时自动回退）
python main.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RUN_COUNT` | `0` | 注册次数，`0` 为无限循环 |
| `RUN_INTERVAL` | `0` | 每轮注册间隔（秒） |
| `TOKEN_DIR` | `./tokens` | Token 文件保存目录 |
| `HEADLESS` | `true` (Docker) / `false` (本地) | 伪无头模式（窗口移至屏幕外，绕过检测） |
| `LOG_ENABLED` | `false` | 是否启用日志文件记录 |
| `LOG_DIR` | `./logs` | 日志文件保存目录 |
| `PROXY` | _(空)_ | 第三方代理地址，如 `socks5://127.0.0.1:1080` |
| `WARP_ENABLED` | `false` | 启用内置 Cloudflare WARP 代理（Docker 专用） |

### 代理优先级

| WARP_ENABLED | PROXY | 行为 |
|:---:|:---:|:---|
| `false` | _(空)_ | 直连，不使用代理 |
| `false` | `socks5://...` | 使用第三方代理 |
| `true` | _(空)_ | 自动启动 WARP，使用 `socks5://127.0.0.1:40000` |
| `true` | `socks5://...` | 启动 WARP，但浏览器使用指定的第三方代理 |

> 使用 WARP 时需要 `--cap-add=NET_ADMIN` 或 `cap_add: [NET_ADMIN]`。

## Token 输出

每次注册成功后，Token 以 JSON 格式保存在 `tokens/` 目录下，包含 `access_token`、`refresh_token` 等字段。

## License

[MIT](LICENSE)
