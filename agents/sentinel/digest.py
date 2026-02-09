"""Tier 1: Daily health digest — pre-collect data, summarize with AI."""

import logging
from datetime import datetime, timedelta

from shared.llm.provider import LLMProvider
from shared.tools.endpoints import check_endpoint_via_traefik
from shared.tools.logs import query_logs
from shared.tools.metrics import query_metrics
from shared.tools.slack import send_slack_alert

log = logging.getLogger("sentinel.digest")

DIGEST_SYSTEM_PROMPT = """\
You are Galaxy Sentinel, a monitoring AI for a Kubernetes homelab cluster called "Galaxy".
You receive a 24-hour health summary and produce a concise, human-friendly daily digest.

Rules:
- Be concise — aim for 5-10 lines
- Lead with overall status (healthy / degraded / critical)
- Highlight anything unusual or trending in the wrong direction
- Use Slack mrkdwn formatting (*bold*, _italic_, `code`)
- End with one actionable recommendation if anything needs attention
- If everything looks good, say so briefly
"""


async def _collect_24h_summary(config) -> str:
    """Pre-collect 24h metrics into a compact text summary."""
    lines = []

    # Node count
    try:
        data = await query_metrics(
            config, 'kube_node_status_condition{condition="Ready",status="true"}'
        )
        ready = len(data.get("result", []))
        lines.append(f"NODES: {ready}/{config.expected_node_count} ready")
    except Exception as e:
        lines.append(f"NODES: query failed ({e})")

    # CPU load averages
    try:
        data = await query_metrics(
            config,
            '{__name__=~"system_cpu_load_average_15m|system\\\\.cpu\\\\.load_average\\\\.15m"}',
        )
        cpu_parts = []
        for r in data.get("result", []):
            node = r["metric"].get("k8s_node_name", r["metric"].get("node", "?"))
            val = r["value"][1]
            cpu_parts.append(f"{node}={val}")
        lines.append(f"CPU_LOAD_15M: {', '.join(cpu_parts)}")
    except Exception as e:
        lines.append(f"CPU_LOAD: query failed ({e})")

    # Memory usage
    try:
        data = await query_metrics(
            config,
            '{__name__=~"system_memory_usage|system\\\\.memory\\\\.usage"}',
        )
        node_mem: dict[str, dict[str, float]] = {}
        for r in data.get("result", []):
            node = r["metric"].get("k8s_node_name", r["metric"].get("node", "?"))
            state = r["metric"].get("state", "")
            val = float(r["value"][1])
            node_mem.setdefault(node, {})[state] = val

        mem_parts = []
        for node, states in sorted(node_mem.items()):
            used = states.get("used", 0)
            free = states.get("free", 0)
            total = used + free
            pct = (used / total * 100) if total > 0 else 0
            mem_parts.append(f"{node}={pct:.0f}%")
        lines.append(f"MEMORY: {', '.join(mem_parts)}")
    except Exception as e:
        lines.append(f"MEMORY: query failed ({e})")

    # Disk usage
    try:
        data = await query_metrics(
            config,
            '{__name__=~"system_filesystem_usage|system\\\\.filesystem\\\\.usage",mountpoint="/"}',
        )
        node_disk: dict[str, dict[str, float]] = {}
        for r in data.get("result", []):
            node = r["metric"].get("k8s_node_name", r["metric"].get("node", "?"))
            state = r["metric"].get("state", "")
            val = float(r["value"][1])
            node_disk.setdefault(node, {})[state] = val

        disk_parts = []
        for node, states in sorted(node_disk.items()):
            used = states.get("used", 0)
            free = states.get("free", 0)
            total = used + free
            pct = (used / total * 100) if total > 0 else 0
            disk_parts.append(f"{node}={pct:.0f}%")
        lines.append(f"DISK: {', '.join(disk_parts)}")
    except Exception as e:
        lines.append(f"DISK: query failed ({e})")

    # Pod status
    try:
        data = await query_metrics(config, "kube_pod_status_phase")
        phase_counts: dict[str, int] = {}
        for r in data.get("result", []):
            phase = r["metric"].get("phase", "unknown")
            val = float(r["value"][1])
            if val > 0:
                phase_counts[phase] = phase_counts.get(phase, 0) + int(val)
        parts = [f"{phase}={count}" for phase, count in sorted(phase_counts.items())]
        lines.append(f"PODS: {', '.join(parts)}")
    except Exception as e:
        lines.append(f"PODS: query failed ({e})")

    # Error logs in last 24h by namespace
    try:
        entries = await query_logs(
            config,
            '{k8s_namespace_name=~".+"} |~ "error|Error|ERROR"',
            limit=100,
            since="24h",
        )
        ns_counts: dict[str, int] = {}
        for e in entries:
            ns = e["labels"].get("k8s_namespace_name", "unknown")
            ns_counts[ns] = ns_counts.get(ns, 0) + 1
        parts = [f"{ns}={count}" for ns, count in sorted(ns_counts.items())]
        lines.append(f"ERRORS_24H: {', '.join(parts) or 'none'}")
    except Exception as e:
        lines.append(f"ERRORS_24H: query failed ({e})")

    # Endpoint health
    try:
        ep_parts = []
        for ep in config.probe_endpoints:
            result = await check_endpoint_via_traefik(
                host=ep["host"], traefik_url=config.traefik_url,
            )
            if result["healthy"]:
                ep_parts.append(f"{ep['name']}=ok({result['response_time_ms']}ms)")
            else:
                ep_parts.append(f"{ep['name']}=FAIL({result['error'] or result['status_code']})")
        lines.append(f"ENDPOINTS: {', '.join(ep_parts)}")
    except Exception as e:
        lines.append(f"ENDPOINTS: check failed ({e})")

    return "\n".join(lines)


async def generate_digest(config):
    """Generate and send the daily health digest."""
    summary = await _collect_24h_summary(config)
    log.info("Collected 24h summary:\n%s", summary)

    llm = LLMProvider(config)
    response = await llm.complete(
        system=DIGEST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Here is today's cluster health data:\n\n{summary}"}],
        max_tokens=500,
    )

    digest_text = response["content"]
    log.info("AI digest:\n%s", digest_text)

    await send_slack_alert(
        config,
        severity="info",
        title="Daily Health Digest",
        message=digest_text,
    )
