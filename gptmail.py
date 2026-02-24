import re
import time
import httpx


class GptMail:
    """通过 mail.chatgpt.org.uk 接收临时邮件并提取验证码。"""

    BASE_URL = "https://mail.chatgpt.org.uk/api"
    HEADERS = {"referer": "https://mail.chatgpt.org.uk/"}

    def __init__(self):
        self.email = None

    def generate_email(self):
        """生成一个临时邮箱地址。返回邮箱字符串。"""
        resp = httpx.get(
            f"{self.BASE_URL}/generate-email",
            headers=self.HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"生成邮箱失败: {data}")
        self.email = data["data"]["email"]
        return self.email

    def fetch_emails(self):
        """获取当前邮箱的所有邮件列表。

        Returns:
            list[dict]: 邮件列表，每封邮件包含以下字段：
                - id (str): 邮件唯一标识
                - email_address (str): 收件地址
                - from_address (str): 发件人地址
                - subject (str): 邮件主题
                - content (str): 纯文本正文
                - html_content (str): HTML 正文
                - has_html (bool): 是否包含 HTML 内容
                - timestamp (int): Unix 时间戳
                - raw_size (int): 原始邮件大小
                - created_at (str): ISO 8601 创建时间
        """
        if not self.email:
            raise RuntimeError("尚未生成邮箱，请先调用 generate_email()")
        resp = httpx.get(
            f"{self.BASE_URL}/emails",
            params={"email": self.email},
            headers=self.HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"获取邮件失败: {data}")
        return data["data"].get("emails", [])
