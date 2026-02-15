"""Main entry point: async scheduler loop for Galaxy Sentinel."""

import asyncio
import logging
import signal
import sys
from datetime import datetime

from shared.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sentinel")


async def run_tier0(config):
    """Run all Tier 0 threshold checks."""
    from agents.sentinel.checks import run_all_checks

    log.info("Running Tier 0 checks...")
    alerts = await run_all_checks(config)
    if alerts:
        log.warning("Tier 0 detected %d alert(s)", len(alerts))
        for alert in alerts:
            log.warning("  [%s] %s: %s", alert.severity, alert.check_name, alert.message)

        # Tier 2: investigate anomalies if AI is available
        if config.anthropic_api_key:
            from agents.sentinel.investigate import investigate_alerts

            await investigate_alerts(config, alerts)
        else:
            # No AI — send raw alerts to Slack
            if config.slack_webhook_url:
                from shared.tools.slack import send_slack_alert

                for alert in alerts:
                    await send_slack_alert(
                        config,
                        severity=alert.severity,
                        title=alert.check_name,
                        message=alert.message,
                    )
    else:
        log.info("All Tier 0 checks passed")


async def run_tier1_digest(config):
    """Run Tier 1 daily digest if it's the right time."""
    if not config.anthropic_api_key:
        log.info("Skipping digest — no LLM API key configured")
        return

    from agents.sentinel.digest import generate_digest

    log.info("Generating daily digest...")
    await generate_digest(config)
    log.info("Daily digest sent")


async def main():
    config = load_config()
    log.info("Galaxy Sentinel starting")
    log.info("Check interval: %ds, Digest hour: %d:00", config.check_interval_seconds, config.digest_hour)

    shutdown = asyncio.Event()

    def handle_signal():
        log.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    last_digest_date = None
    digest_attempts = 0
    max_digest_attempts = 3

    while not shutdown.is_set():
        try:
            await run_tier0(config)
        except Exception:
            log.exception("Tier 0 check run failed")

        # Check if it's time for daily digest
        now = datetime.now()
        if now.hour == config.digest_hour and last_digest_date != now.date():
            if digest_attempts < max_digest_attempts:
                try:
                    await run_tier1_digest(config)
                    last_digest_date = now.date()
                    digest_attempts = 0
                except Exception:
                    digest_attempts += 1
                    log.exception(
                        "Daily digest failed (attempt %d/%d)",
                        digest_attempts, max_digest_attempts,
                    )
                    if digest_attempts >= max_digest_attempts:
                        log.error("Giving up on daily digest after %d attempts", max_digest_attempts)
                        last_digest_date = now.date()
        else:
            digest_attempts = 0

        # Wait for next check interval or shutdown
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=config.check_interval_seconds)
        except asyncio.TimeoutError:
            pass

    log.info("Galaxy Sentinel stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
