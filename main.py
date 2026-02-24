# 标准库
import asyncio
import base64
import hashlib
import json
import logging
import os
import queue
import random
import re
import secrets
import string
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

# 第三方库
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from gptmail import GptMail

# 确保终端输出支持 UTF-8（防止 Windows 中文乱码）
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════════
# 配置文件管理
# ═══════════════════════════════════════════════════════
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

def load_config():
    """加载配置文件。"""
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 配置文件不存在: {CONFIG_PATH}")
        exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # token_dir / log_dir 支持相对路径（相对于脚本目录）
    for key, default in [("token_dir", "tokens"), ("log_dir", "logs")]:
        val = config.get(key, default)
        if not os.path.isabs(val):
            config[key] = os.path.join(SCRIPT_DIR, val)

    return config


cfg = load_config()

# ═══════════════════════════════════════════════════════
# 从配置中读取参数
# ═══════════════════════════════════════════════════════
TOKEN_DIR = cfg["token_dir"]
LOG_DIR = cfg.get("log_dir", os.path.join(SCRIPT_DIR, "logs"))
RUN_COUNT = cfg.get("run_count", 1)
RUN_INTERVAL = cfg.get("run_interval", 60)
HEADLESS = cfg.get("headless", False)
PROXY = cfg.get("proxy", None)
LOG_ENABLED = cfg.get("log_enabled", False)


# ═══════════════════════════════════════════════════════
# 日志系统
# ═══════════════════════════════════════════════════════
def setup_logging():
    logger = logging.getLogger("openai_reg")
    logger.setLevel(logging.INFO)

    if LOG_ENABLED:
        # 开启日志：写入文件（仅记 INFO 及以上）
        os.makedirs(LOG_DIR, exist_ok=True)
        log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
        print(f"📝 日志文件: {log_file}")
    else:
        # 关闭日志：不产生任何磁盘文件，起始完全静默
        logger.addHandler(logging.NullHandler())
        print("🔕 日志已关闭 (可在 config.json 中将 log_enabled 设为 true 开启)")

    # 封杀第三方库无孔不入的日志轰炸
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    # 全局日志降级为 WARNING 防止漏网
    logging.basicConfig(level=logging.WARNING)

    return logger


log = setup_logging()


# ═══════════════════════════════════════════════════════
# OpenAI OAuth 配置（固定值，一般不需要改）
# ═══════════════════════════════════════════════════════
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_ENDPOINT = "https://auth.openai.com/oauth/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"


# ═══════════════════════════════════════════════════════
# PKCE 工具函数
# ═══════════════════════════════════════════════════════
def generate_pkce_codes():
    verifier_bytes = secrets.token_bytes(32)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_state():
    return secrets.token_urlsafe(32)


def build_auth_url(code_challenge, state):
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


# ═══════════════════════════════════════════════════════
# 自动化工具函数
# ═══════════════════════════════════════════════════════
async def type_slowly(page, locator, text: str):
    """模拟真人打字：先清除、点击，再逐个字符输入带随机延迟"""
    await locator.clear()
    await locator.click()
    for char in text:
        # 输入单字符带 30~80 毫秒随机延时
        await locator.press_sequentially(char, delay=random.randint(30, 80))
        # 字符之间再停顿 10~50 毫秒
        await page.wait_for_timeout(random.randint(10, 50))

async def handle_cloudflare(page):
    """检测并主动点击 Cloudflare 验证码 (Just a moment / Ray ID)"""
    title = await page.title()
    if "Just a moment" not in title and "请稍候" not in title:
        return
    print("⚠️ 检测到 Cloudflare 验证盾牌，尝试突破...")
    await page.wait_for_timeout(3000)
    for frame in page.frames:
        try:
            cf_chk = frame.locator('.cf-turnstile-wrapper, #challenge-stage, input[type="checkbox"]').first
            if await cf_chk.count() > 0:
                print("🖱️ 尝试点击 CF 验证框...")
                await cf_chk.click()
                await page.wait_for_timeout(5000)
        except:
            pass

async def move_mouse_organically(page, locator):
    """模拟真实的轨迹（非瞬间转移）来移动鼠标到目标位置"""
    try:
        box = await locator.bounding_box(timeout=2000)
        if box:
            # 目标中心点加一点随机偏移
            target_x = box['x'] + box['width'] / 2 + random.uniform(-5, 5)
            target_y = box['y'] + box['height'] / 2 + random.uniform(-5, 5)
            
            # 当前鼠标位置 (粗略获取)
            start_x, start_y = random.randint(100, 500), random.randint(100, 500)
            
            # 分步骤滑动鼠标 (拟真曲线)
            steps = random.randint(5, 15)
            for i in range(1, steps + 1):
                partial_x = start_x + (target_x - start_x) * (i / steps) + random.uniform(-10, 10)
                partial_y = start_y + (target_y - start_y) * (i / steps) + random.uniform(-10, 10)
                await page.mouse.move(partial_x, partial_y)
                await page.wait_for_timeout(random.randint(10, 30))
                
            await page.mouse.move(target_x, target_y)
            await page.wait_for_timeout(random.randint(100, 300))
    except Exception as e:
        pass  # 失败则跳过鼠标滑动，不影响后续点击


# ═══════════════════════════════════════════════════════
# HTTP 回调服务器
# ═══════════════════════════════════════════════════════
oauth_result_queue = queue.Queue()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/auth/callback":
            query = parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            state_param = query.get("state", [None])[0]
            error_param = query.get("error", [None])[0]

            if error_param:
                print(f"❌ OAuth 回调收到错误: {error_param}")
                oauth_result_queue.put({"error": error_param})
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h1>授权失败: {error_param}</h1>".encode())
                return

            if not code:
                print("❌ 回调中缺少 code")
                oauth_result_queue.put({"error": "no_code"})
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>Missing authorization code</h1>")
                return

            print(f"✅ OAuth 回调收到 code (前8位: {code[:8]}...)")
            oauth_result_queue.put({"code": code, "state": state_param})

            self.send_response(302)
            self.send_header("Location", "/success")
            self.end_headers()

        elif parsed.path == "/success":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>授权成功</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
       display: flex; justify-content: center; align-items: center;
       height: 100vh; margin: 0;
       background: linear-gradient(135deg, #28a745 0%, #20c997 100%); }
.container { text-align: center; color: white; }
h1 { font-size: 2.5rem; margin-bottom: 1rem; }
p { font-size: 1.2rem; opacity: 0.9; }
.path { font-size: 0.9rem; opacity: 0.7; margin-top: 1.5rem; word-break: break-all; max-width: 600px; margin-left: auto; margin-right: auto; }
</style></head><body>
<div class="container">
  <h1>✅ 授权成功</h1>
  <p>您可以关闭此窗口并返回应用</p>
  <p class="path">Token 保存路径: """ + TOKEN_DIR.replace("\\", "/") + """</p>
</div></body></html>"""
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_oauth_server():
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    print(f"🌐 OAuth 回调服务器已启动: http://127.0.0.1:{CALLBACK_PORT}/auth/callback")
    return server


# ═══════════════════════════════════════════════════════
# Token 兑换
# ═══════════════════════════════════════════════════════
async def exchange_code_for_tokens(code, code_verifier):
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    print("🔄 正在用 authorization code 兑换 Token...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_ENDPOINT,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        print(f"❌ Token 兑换失败: HTTP {resp.status_code}")
        print(f"   响应: {resp.text[:500]}")
        return None
    token_data = resp.json()
    print("✅ Token 兑换成功！")
    return token_data


def save_tokens(email, token_data):
    os.makedirs(TOKEN_DIR, exist_ok=True)
    safe_email = email.replace("@", "_at_").replace(".", "_")
    filepath = os.path.join(TOKEN_DIR, f"{safe_email}.json")
    save_data = {
        "type": "codex",
        "email": email,
        "id_token": token_data.get("id_token", ""),
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_in": token_data.get("expires_in", 0),
        "token_type": token_data.get("token_type", ""),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Token 已保存到: {filepath}")
    return filepath


# ═══════════════════════════════════════════════════════
# 邮件验证码获取
# ═══════════════════════════════════════════════════════
async def get_verification_code(mail: GptMail, timeout=60):
    """通过 GptMail 轮询获取验证码。"""
    print("⏳ 等待验证码...")
    start = time.time()

    while time.time() - start < timeout:
        try:
            emails = await asyncio.to_thread(mail.fetch_emails)
            if emails:
                latest = emails[0]
                content = " ".join(str(v) for v in latest.values())
                match = re.search(r"\b(\d{6})\b", content)
                if match:
                    otp_code = match.group(1)
                    print(f"✅ 验证码: {otp_code} (邮箱: {mail.email})")
                    return otp_code
        except Exception as e:
            print(f"❌ 获取邮件验证码错误: {e}")

        await asyncio.sleep(2)
    return None


# ═══════════════════════════════════════════════════════
# 单次注册流程（接收 browser 参数，每轮创建新 context）
# ═══════════════════════════════════════════════════════
async def register_one(browser):
    """执行单次注册 + Token 获取。返回 True 表示成功。"""
    code_verifier, code_challenge = generate_pkce_codes()
    state = generate_state()
    auth_url = build_auth_url(code_challenge, state)

    print("=" * 60)
    print("🔐 开始新一轮注册流程")
    print("=" * 60)

    # 清空上一轮残留的回调结果
    while not oauth_result_queue.empty():
        try:
            oauth_result_queue.get_nowait()
        except queue.Empty:
            break

    # 每轮创建全新的浏览器上下文（= 无痕窗口，session/cookies 完全隔离）
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        viewport={"width": 1920 if HEADLESS else 600, "height": 1080 if HEADLESS else 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )

    # === 深度指纹伪装 (无论是否无头都注入，因为 Playwright 本身也有自动化痕迹) ===
    await context.add_init_script("""
        // 1. 擦除 webdriver 标记 (最核心)
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        
        // 2. 伪造 window.chrome (Playwright/Puppeteer 默认缺失这个对象)
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
        // 3. 伪造 WebGL 显卡渲染器 (防指纹检测)
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics';
            return getParameter.call(this, parameter);
        };
        
        // 4. 伪造浏览器插件列表 (真实浏览器至少有几个插件)
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                {name: 'Native Client', filename: 'internal-nacl-plugin'},
            ]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en-US', 'en']
        });
        
        // 5. 伪造 Permissions API (Cloudflare 会检查)
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
        
        // 6. 伪造 connection (网络类型)
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false,
            })
        });
        
        // 7. 伪造 hardwareConcurrency (CPU核心数)
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        
        // 8. 伪造 deviceMemory
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    """)

    page = await context.new_page()

    # 启用 stealth 插件 (自动处理更多底层指纹)
    await Stealth().apply_stealth_async(page)

    # 通过 GptMail 生成临时邮箱
    mail = GptMail()
    EMAIL = await asyncio.to_thread(mail.generate_email)
    NAME = "".join(random.choices(string.ascii_letters, k=random.randint(5, 8))).capitalize()
    log.info(f"开始注册: {EMAIL}")

    print(f"\n📋 本次注册信息:")
    print(f"   邮箱: {EMAIL}")
    print(f"   姓名: {NAME}")
    print(f"   生日: 2000年\n")

    try:
        # ——— Step 1: 打开授权 URL ———
        try:
            await page.goto(auth_url, wait_until="domcontentloaded")
        except Exception as goto_err:
            if "ERR_ABORTED" in str(goto_err) or "frame was detached" in str(goto_err):
                pass  # OAuth 跳转导致的无害中断，忽略继续
            else:
                raise
        print(f"📍 页面已加载，当前 URL: {page.url}")
        await page.wait_for_timeout(2000)

        await handle_cloudflare(page)

        # 轮询等待注册链接出现（最长 15 秒，解决首次 JS 渲染慢的问题）
        sign_up_link = None
        for _ in range(15):
            link_cn = page.get_by_role("link", name="注册")
            link_en = page.get_by_role("link", name="Sign up")
            if await link_cn.count() > 0:
                sign_up_link = link_cn
                break
            if await link_en.count() > 0:
                sign_up_link = link_en
                break
            await page.wait_for_timeout(1000)

        if sign_up_link:
            await move_mouse_organically(page, sign_up_link)
            await sign_up_link.click()
            print("📍 已点击注册链接")
        else:
            print(f"⚠️ 未找到注册链接，当前 URL: {page.url}")
            log.error(f"{EMAIL}: 未找到注册链接, URL={page.url}")
            return False

        await page.wait_for_timeout(1000)

        # ——— Step 2: 填写邮箱 ———
        retry_count = 0
        while retry_count < 3:
            email_input = page.get_by_role("textbox", name="电子邮件地址")
            if await email_input.count() == 0:
                email_input = page.get_by_role("textbox", name="Email address")
            if await email_input.count() == 0:
                email_input = page.locator('input[type="email"], input[name="email"]').first

            await type_slowly(page, email_input, EMAIL)
            print(f"✅ 邮箱已填入: {EMAIL}")

            continue_btn = page.get_by_role("button", name="继续", exact=True)
            if await continue_btn.count() == 0:
                continue_btn = page.get_by_role("button", name="Continue", exact=True)
            await move_mouse_organically(page, continue_btn)
            await continue_btn.click()

            hit_retry = False
            hit_existing = False
            for _ in range(30):
                # 检测1: 错误页面 → 重试
                retry_btn = page.get_by_role("button", name="重试")
                if await retry_btn.count() == 0:
                    retry_btn = page.get_by_role("button", name="Retry")
                if await retry_btn.count() > 0:
                    hit_retry = True
                    break
                # 检测2: 出现注册按钮 → 新邮箱，正常继续
                otp_check = page.get_by_role("button", name="使用一次性验证码注册")
                otp_check_en = page.get_by_role("button", name="Sign up with one-time code")
                if await otp_check.count() > 0 or await otp_check_en.count() > 0:
                    break
                # 检测3: 直接跳到验证码页面 → 邮箱已注册
                if "email-verification" in page.url:
                    hit_existing = True
                    break
                await page.wait_for_timeout(500)

            if hit_retry:
                print(f"⚠️ 检测到错误页面，第 {retry_count + 1} 次重试...")
                await retry_btn.click()
                await page.wait_for_timeout(2000)
                retry_count += 1
                continue
            if hit_existing:
                print(f"⚠️ 邮箱 {EMAIL} 已注册（直接跳到了登录验证页），跳过本轮")
                log.warning(f"{EMAIL}: 邮箱已注册，跳过")
                return False
            break

        # ——— Step 3: 邮箱验证 ———
        for attempt in range(3):
            # 兼容中英文的获取方式
            otp_btn = page.get_by_role("button", name="一次性验证")
            if await otp_btn.count() == 0:
                otp_btn = page.get_by_role("button", name="one-time code")
            if await otp_btn.count() == 0:
                otp_btn = page.locator("button:has-text('一次性'), button:has-text('one-time code')").first

            if await otp_btn.count() > 0 and await otp_btn.is_visible():
                print(f"🖱️ 第 {attempt+1} 次尝试点击 '使用一次性验证码注册' 按钮...")
                try:
                    await move_mouse_organically(page, otp_btn)
                    # 先尝试普通点击
                    await otp_btn.click(timeout=3000)
                except:
                    # 如果普通点击失败，使用 JS 强行触发点击底层事件
                    try:
                        await otp_btn.evaluate("node => node.click()")
                    except:
                        pass
            
            try:
                # 等待 URL 真正变为验证页，或者有专门的验证码框出现
                await page.wait_for_url("**/email-verification*", timeout=4000)
                break # 跳转成功，跳出循环
            except:
                # 如果没因为 url 跳出，尝试看看是不是验证码专用框已经刷出来了
                if await page.locator('input[name="code"], input[autocomplete="one-time-code"]').count() > 0:
                    break
        
        # 【暴力后备路线】如果点了依然死在当前密码页，强制直接跳转过去！
        if "email-verification" not in page.url and await page.locator('input[name="code"], input[autocomplete="one-time-code"]').count() == 0:
            print("⚠️ 按钮点击可能失效，强制执行页面跳转到 email-verification...")
            try:
                await page.goto("https://auth.openai.com/email-verification", timeout=8000)
            except:
                pass

        print(f"📍 页面验证准备好，当前 URL: {page.url}")

        otp_verified = False
        otp_retries = 0

        while not otp_verified and otp_retries < 5:
            otp = await get_verification_code(mail)
            if otp:
                otp_input = page.get_by_role("textbox", name="验证码")
                if await otp_input.count() == 0:
                    otp_input = page.get_by_role("textbox", name="Code")
                if await otp_input.count() == 0:
                    otp_input = page.locator('input[name="code"], input[autocomplete="one-time-code"]').first

                await type_slowly(page, otp_input, otp)

                submit_btn = page.get_by_role("button", name="继续")
                if await submit_btn.count() == 0:
                    submit_btn = page.get_by_role("button", name="Continue")
                await submit_btn.click()

                # 等待结果：可能跳转到 about-you，也可能显示 "已验证"
                for _ in range(12):  # 最长等 6 秒
                    await page.wait_for_timeout(500)
                    current_url = page.url

                    # 情况1: 已跳转到个人信息页面
                    if "about-you" in current_url:
                        print("📍 已跳转到个人信息页面")
                        otp_verified = True
                        break

                    # 情况2: 页面显示 "已验证" 文字（验证成功但未自动跳转）
                    page_text = await page.text_content("body") or ""
                    if "已验证" in page_text or "verified" in page_text.lower():
                        print("📍 邮箱已验证成功!")
                        otp_verified = True
                        # 尝试点击页面上可能存在的继续按钮
                        try:
                            any_btn = page.get_by_role("button", name="继续")
                            if await any_btn.count() == 0:
                                any_btn = page.get_by_role("button", name="Continue")
                            if await any_btn.count() > 0:
                                await move_mouse_organically(page, any_btn)
                                await any_btn.click()
                                await page.wait_for_timeout(2000)
                        except:
                            pass
                        break

                    # 情况3: 页面内其实已经出现了下一步（填写个人信息）的输入框
                    name_input = page.locator('input[name="name"], input[placeholder*="名"], input[type="text"]').first
                    if await name_input.count() > 0 and await name_input.is_visible():
                        print("📍 检测到个人信息输入框，已越过验证码阶段")
                        otp_verified = True
                        break

                    # 情况4: 跳到了其他非 email-verification 页面（需排除刚才那种情况被覆盖）
                    if "email-verification" not in current_url and "signup" not in current_url:
                        print(f"📍 已离开验证页面，当前 URL: {current_url}")
                        otp_verified = True
                        break

                if otp_verified:
                    break
                    
                # 【新增】检测是否有明显的错误提示，如果有错误提示说明验证码失效/错误
                error_msg = page.locator("text=需要填写验证码, text=验证码无效, text=验证码错误, text=code is invalid, text=incorrect").first
                if await error_msg.count() > 0 and await error_msg.is_visible():
                    print(f"⚠️ 页面提示验证码错误/无效！")
                else:
                    print(f"⚠️ 提交后未发生跳转，可能验证码错误或提交失败...")
                    
                print(f"🔄 第 {otp_retries + 1} 次重试...")
                
                # 为了下次能重新填入新的验证码，清空旧输入框
                try:
                    await otp_input.clear(timeout=1000)
                except:
                    pass
                otp_retries += 1
                await page.wait_for_timeout(2000) # 失败稍微等一下再重新获取，防刷屏
            else:
                print("⚠️ 未获取到验证码或超时，重试...")
                otp_retries += 1
                await page.wait_for_timeout(2000)

        if not otp_verified:
            print("❌ 验证码验证失败")
            log.error(f"{EMAIL}: 验证码验证失败")
            return False

        await page.wait_for_timeout(500)

        # ——— Step 4: 填写个人信息 ———
        name_input = page.locator('input[name="name"], input[placeholder*="名"], input[type="text"]').first
        
        # 为了防止前面检测后还没加载完全，这里稍等一下可见
        await name_input.wait_for(state="visible", timeout=10000)
        
        await type_slowly(page, name_input, NAME)
        print(f"✅ 已填写姓名: {NAME}")

        await page.wait_for_timeout(random.randint(300, 800))
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(random.randint(100, 300))
        await page.keyboard.type("2000", delay=random.randint(50, 150))
        print("✅ 已填写年份: 2000")

        for attempt in range(3):
            continue_btn = page.get_by_role("button", name="继续")
            if await continue_btn.count() == 0:
                continue_btn = page.get_by_role("button", name="Continue")
            
            if await continue_btn.count() > 0:
                try:
                    await continue_btn.click(timeout=3000)
                except:
                    try:
                        await continue_btn.evaluate("node => node.click()")
                    except:
                        pass
            # 等待最多 6 秒，检查是否自动跳走
            auto_navigated = False
            for _ in range(12):
                await page.wait_for_timeout(500)
                if "about-you" not in page.url:
                    auto_navigated = True
                    break
            
            if not auto_navigated:
                # 6秒内未动弹，强制跳到 consent 页面
                print(f"⚠️ 6秒内未检测到自动跳转，强制前往同意授权页面 (consent)...")
                try:
                    await page.goto("https://auth.openai.com/sign-in-with-chatgpt/codex/consent", timeout=8000)
                except:
                    pass
            
            # 到达 consent 页面则点击通过按钮
            if "consent" in page.url:
                consent_btn = page.locator('button:has-text("继续"), button:has-text("Continue"), button:has-text("Accept"), button:has-text("同意")').first
                if await consent_btn.count() > 0 and await consent_btn.is_visible():
                    try:
                        await consent_btn.click(force=True, timeout=3000)
                        print("✅ 已在 consent 页面点击最终通过按钮")
                    except:
                        pass
            
            if "consent" in page.url or "about-you" in page.url:
                await page.wait_for_timeout(1000)
            else:
                break  # 已跳走，结束循环

        print(f"\n🎉 注册表单已完成！ 邮箱: {EMAIL}")

        # ——— Step 5: 等待回调 ———
        print(">>> 等待 OAuth 回调 (最长 20 秒)...")

        try:
            result = oauth_result_queue.get(timeout=20)
        except queue.Empty:
            print(f"❌ OAuth 回调超时，当前 URL: {page.url}")
            debug_shot = os.path.join(LOG_DIR, f"timeout_{EMAIL}.png")
            os.makedirs(LOG_DIR, exist_ok=True)
            await page.screenshot(path=debug_shot)
            print(f"📸 已保存超时现场截图到: {debug_shot}")
            log.error(f"{EMAIL}: OAuth 回调超时, URL={page.url}")
            return False

        if "error" in result:
            print(f"❌ OAuth 回调错误: {result['error']}")
            return False

        auth_code = result["code"]
        returned_state = result.get("state", "")

        if returned_state != state:
            print(f"❌ State 不匹配!")
            return False

        print(f"✅ Authorization code 已获取")
        print(f"✅ State 校验通过")

        # ——— Step 6: 兑换 Token ———
        token_data = await exchange_code_for_tokens(auth_code, code_verifier)

        if token_data:
            filepath = save_tokens(EMAIL, token_data)
            print("\n" + "=" * 60)
            print("🎉🎉🎉 全流程完成！注册 + Token 获取成功！")
            print("=" * 60)
            print(f"📧 邮箱:          {EMAIL}")
            print(f"🔑 Access Token:  {token_data.get('access_token', '')[:40]}...")
            print(f"🔄 Refresh Token: {token_data.get('refresh_token', 'N/A')[:40]}...")
            print(f"💾 保存位置:      {filepath}")
            print("=" * 60)
            log.info(f"{EMAIL}: 注册+Token成功, 保存到 {filepath}")
            return True
        else:
            print("❌ Token 兑换失败")
            log.error(f"{EMAIL}: Token 兑换失败")
            return False

    finally:
        # 关闭本轮的 context（彻底清除 session/cookies）
        try:
            await context.close()
        except Exception:
            pass  # 已被关闭或异常中断，忽略


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════
async def main():
    print("\n" + "#" * 60)
    print(f"# 批量注册模式: 共 {'无限' if RUN_COUNT == 0 else RUN_COUNT} 次, 间隔 {RUN_INTERVAL} 秒")
    print(f"# 无头模式: {'是' if HEADLESS else '否'}")
    print("#" * 60)
    log.info(f"启动批量注册: 共 {'无限' if RUN_COUNT == 0 else RUN_COUNT} 次, 间隔 {RUN_INTERVAL}s, headless={HEADLESS}")

    # 全局只启动一次回调服务器（常驻）
    oauth_server = start_oauth_server()

    success_count = 0
    fail_count = 0

    try:
        async with async_playwright() as p:
            # === 设置启动参数绕过检测 ===
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--lang=zh-CN,zh;q=0.9,en;q=0.8",
                # 防止无休止地抢夺焦点、弹到最上面
                "--no-first-run",
                "--no-default-browser-check",
            ]
            
            # 伪无头模式：headless=True 时把窗口推到屏幕外，躲避最严的 Headless 检测
            if HEADLESS:
                args.extend([
                    "--window-position=-10000,-10000",
                    "--window-size=1920,1080",
                    "--start-maximized"
                ])
            else:
                args.extend([
                    "--window-position=50,50",
                    "--window-size=600,800"
                ])

            # 全局只启动一次浏览器（始终以有界面模式运行，避免 headless 指纹）
            browser_kwargs = {
                "headless": False,
                "args": args
            }
            if PROXY:
                print(f"🌐 使用代理服务器: {PROXY}")
                browser_kwargs["proxy"] = {"server": PROXY}

            browser = await p.chromium.launch(**browser_kwargs)

            i = 0
            while True:
                if RUN_COUNT != 0 and i >= RUN_COUNT:
                    break
                    
                print(f"\n{'='*60}")
                print(f"📌 第 {i+1}{f'/{RUN_COUNT}' if RUN_COUNT > 0 else ''} 轮注册")
                print(f"{'='*60}")

                try:
                    ok = await register_one(browser)
                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1
                except KeyboardInterrupt:
                    # Ctrl+C：干净退出，不刷屏
                    print("\n⏹️ 用户中断，正在退出...")
                    raise
                except Exception as e:
                    err_msg = str(e)
                    # 浏览器进程已死 → 不要无限重试，直接退出
                    if "Connection closed" in err_msg or "Target closed" in err_msg:
                        print(f"\n💀 浏览器进程已断开连接，退出主循环。原因: {err_msg[:80]}")
                        break
                    print(f"❌ 第 {i+1} 轮异常: {e}")
                    log.error(f"第 {i+1} 轮异常:\n{traceback.format_exc()}")
                    fail_count += 1

                if RUN_COUNT == 0 or i < RUN_COUNT - 1:
                    if RUN_INTERVAL > 0:
                        print(f"\n>>> 等待 {RUN_INTERVAL} 秒后开始第 {i+2} 轮...")
                        step = 10 if RUN_INTERVAL >= 10 else RUN_INTERVAL
                        for remaining in range(RUN_INTERVAL, 0, -step):
                            print(f"   剩余: {remaining}s")
                            await asyncio.sleep(min(step, remaining))
                            
                    print("   开始!")
                
                i += 1

            try:
                await browser.close()
            except Exception:
                pass  # 浏览器已被关闭，忽略

    finally:
        oauth_server.shutdown()
        print("🔒 OAuth 回调服务器已关闭")

    print(f"\n{'#'*60}")
    print(f"# 全部完成! 成功: {success_count}, 失败: {fail_count}")
    print(f"# Token 保存目录: {TOKEN_DIR}")
    if LOG_ENABLED:
        print(f"# 日志目录: {LOG_DIR}")
    print(f"{'#'*60}")
    log.info(f"全部完成: 成功={success_count}, 失败={fail_count}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Ctrl+C 静默退出