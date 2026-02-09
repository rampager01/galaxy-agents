"""Send alerts to Slack via incoming webhook."""

import logging

import httpx

log = logging.getLogger("sentinel.slack")

SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "warning": ":warning:",
    "info": ":large_blue_circle:",
    "resolved": ":white_check_mark:",
}


async def send_slack_alert(
    config,
    severity: str,
    title: str,
    message: str,
    timeout: float = 10.0,
) -> bool:
    """Send a formatted alert to Slack.

    Args:
        severity: One of "critical", "warning", "info", "resolved".
        title: Alert title.
        message: Alert body (can be multi-line).

    Returns:
        True if the message was sent successfully.
    """
    if not config.slack_webhook_url:
        log.warning("No Slack webhook configured — alert not sent: [%s] %s", severity, title)
        return False

    emoji = SEVERITY_EMOJI.get(severity, ":question:")
    color = {
        "critical": "#dc3545",
        "warning": "#ffc107",
        "info": "#0d6efd",
        "resolved": "#198754",
    }.get(severity, "#6c757d")

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{emoji} {title}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": message,
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Severity:* {severity.upper()} | *Source:* Galaxy Sentinel",
                            }
                        ],
                    },
                ],
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(config.slack_webhook_url, json=payload)
            resp.raise_for_status()
            log.info("Slack alert sent: [%s] %s", severity, title)
            return True
    except Exception:
        log.exception("Failed to send Slack alert: [%s] %s", severity, title)
        return False
