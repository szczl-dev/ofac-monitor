"""
飞书推送模块
将监控结果推送到飞书群机器人
"""

import json
import logging
from datetime import datetime
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书消息推送器"""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or Config.FEISHU_WEBHOOK_URL
        if not self.webhook_url:
            raise ValueError("未配置飞书 Webhook URL")

    def send_test_message(self) -> bool:
        """发送测试消息，验证 Webhook 是否工作"""
        card = self._build_card(
            title="🧪 OFAC 监控系统 - 测试消息",
            template="blue",
            content=(
                "✅ **OFAC 制裁名单监控系统已就绪**\n\n"
                "如果您看到此消息，说明飞书 Webhook 配置正确。\n"
                f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "系统将每日自动检测 OFAC 制裁名单更新并推送通知。"
            ),
            note="OFAC Monitor System Test"
        )
        return self._send(card)

    def send_update_notification(self, summary: str) -> bool:
        """发送制裁名单更新通知"""
        # 截断过长的内容（飞书卡片有长度限制）
        if len(summary) > 15000:
            summary = summary[:14900] + "\n\n> ⚠️ 内容过长已截断，完整报告请查看日志"

        card = self._build_card(
            title="📋 OFAC 制裁名单更新通知",
            template="red",
            content=summary,
            note=f"OFAC 制裁名单每日监控 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self._send(card)

    def send_status_report(self, report: str) -> bool:
        """发送状态报告（首次运行/无变化）"""
        card = self._build_card(
            title="📊 OFAC 制裁名单状态报告",
            template="blue",
            content=report,
            note=f"OFAC 制裁名单每日监控 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self._send(card)

    def send_error_alert(self, error_msg: str) -> bool:
        """发送错误告警"""
        card = self._build_card(
            title="🚨 OFAC 监控系统异常",
            template="red",
            content=(
                f"**监控系统运行异常**\n\n"
                f"错误信息:\n```\n{error_msg}\n```\n\n"
                f"请检查系统日志获取更多详情。\n"
                f"发生时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            note="OFAC Monitor Error Alert"
        )
        return self._send(card)

    # ==================== 内部方法 ====================

    def _build_card(self, title: str, template: str,
                    content: str, note: str = "") -> dict:
        """构建飞书交互卡片消息体"""
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True,
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    },
                    "template": template,  # blue, red, green, yellow, purple
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content,
                        },
                    },
                ],
            },
        }

        if note:
            card["card"]["elements"].append({
                "tag": "hr",
            })
            card["card"]["elements"].append({
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": note,
                    },
                ],
            })

        return card

    def _send(self, card: dict) -> bool:
        """发送消息到飞书"""
        try:
            resp = requests.post(
                self.webhook_url,
                json=card,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()

            result = resp.json()
            if result.get("code") == 0:
                logger.info("飞书消息发送成功")
                return True
            else:
                logger.error(f"飞书返回错误: {result}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"飞书消息发送失败 (网络错误): {e}")
            return False
        except Exception as e:
            logger.error(f"飞书消息发送失败: {e}")
            return False
