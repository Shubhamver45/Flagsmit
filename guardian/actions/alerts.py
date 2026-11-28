"""
Slack and Generic Webhook Alert Dispatcher.
Formats human-readable alerts with exact reasons and numerical evidence.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, Optional
import requests

from guardian.audit_log import AuditEntry

logger = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(
        self,
        slack_webhook_url: Optional[str] = None,
        generic_webhook_url: Optional[str] = None,
        session: Optional[Any] = None,
    ):
        self.slack_webhook_url = slack_webhook_url
        self.generic_webhook_url = generic_webhook_url
        self.session = session or requests.Session()

    def dispatch(self, entry: AuditEntry) -> Dict[str, Any]:
        """
        Dispatches alert to configured Slack and Generic webhook endpoints.
        """
        results = {}

        if self.slack_webhook_url:
            success = self._send_slack(entry)
            results["slack"] = success
            if success:
                print(f"📢 [Slack Alert] Slack alert sent successfully for {entry.decision}")
            else:
                print(f"⚠️  [Slack Alert] Failed to send Slack alert for {entry.decision}")
        else:
            print("ℹ️  [Slack Alert] Slack alert skipped (no webhook set)")

        if self.generic_webhook_url:
            results["generic"] = self._send_generic(entry)

        return results

    def _send_slack(self, entry: AuditEntry) -> bool:
        emoji_map = {
            "ROLLBACK": "🚨",
            "AWAIT_APPROVAL": "⏳",
            "PROMOTED": "🎉",
            "ADVANCE": "🚀",
            "HOLD": "ℹ️",
        }
        emoji = emoji_map.get(entry.decision, "📢")
        header_text = f"{emoji} Guardian Alert: {entry.decision} for `{entry.flag_name}`"

        fields = [
            {"type": "mrkdwn", "text": f"*Flag:*\n`{entry.flag_name}`"},
            {"type": "mrkdwn", "text": f"*Decision:*\n*{entry.decision}*"},
            {"type": "mrkdwn", "text": f"*Transition:*\n`{entry.from_state}` ➔ `{entry.to_state}`"},
            {"type": "mrkdwn", "text": f"*Timestamp:*\n{entry.timestamp}"},
        ]

        numbers_text = "\n".join([f"• *{k}*: `{v}`" for k, v in entry.numbers.items()]) or "None"

        payload = {
            "text": f"{header_text}: {entry.reason}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": header_text, "emoji": True},
                },
                {"type": "section", "fields": fields},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Reason:*\n>{entry.reason}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Numerical Evidence:*\n{numbers_text}",
                    },
                },
            ],
        }

        if entry.decision == "AWAIT_APPROVAL":
            payload["blocks"].append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve 100%"},
                            "style": "primary",
                            "action_id": "guardian_approve",
                            "value": entry.flag_name,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Rollback to 0%"},
                            "style": "danger",
                            "action_id": "guardian_rollback",
                            "value": entry.flag_name,
                        },
                    ],
                }
            )

        try:
            resp = self.session.post(self.slack_webhook_url, json=payload, timeout=5)
            if resp.status_code >= 400:
                logger.error(f"Slack webhook failed: {resp.status_code} - {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to post to Slack webhook: {e}")
            return False

    def _send_generic(self, entry: AuditEntry) -> bool:
        payload = {
            "event": "guardian.decision",
            "flag_name": entry.flag_name,
            "decision": entry.decision,
            "from_state": entry.from_state,
            "to_state": entry.to_state,
            "reason": entry.reason,
            "numbers": entry.numbers,
            "timestamp": entry.timestamp,
        }
        try:
            resp = self.session.post(self.generic_webhook_url, json=payload, timeout=5)
            if resp.status_code >= 400:
                logger.error(f"Generic webhook failed: {resp.status_code} - {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to post to generic webhook: {e}")
            return False
