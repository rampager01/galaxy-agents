"""Tier 0: Threshold-based checks — no AI needed."""

import logging
from dataclasses import dataclass

from shared.tools.dns import check_dns
from shared.tools.endpoints import check_endpoint, check_endpoint_via_traefik
from shared.tools.logs import query_logs
from shared.tools.metrics import format_instant_results, query_metrics

log = logging.getLogger("sentinel.checks")


@dataclass
class Alert:
    check_name: str
    severity: str  # "critical" or "warning"
    message: str


# --- Node Health Checks ---


async def check_node_ready(config) -> list[Alert]:
    """Check that all expected nodes are Ready."""
    alerts = []
    data = await query_metrics(
        config,
        'kube_node_status_condition{condition="Ready",status="true"}',
    )
    results = data.get("result", [])
    ready_count = len(results)

    if ready_count < config.expected_node_count:
        ready_nodes = {r["metric"].get("node", "?") for r in results}
        missing = set(config.cluster_nodes) - ready_nodes
        alerts.append(Alert(
            check_name="Node Down",
            severity="critical",
            message=f"Only {ready_count}/{config.expected_node_count} nodes ready. "
                    f"Missing/not-ready: {', '.join(missing) or 'unknown'}",
        ))
    return alerts


async def check_cpu_load(config) -> list[Alert]:
    """Check 15-minute CPU load average across nodes."""
    alerts = []
    data = await query_metrics(
        config,
        '{__name__=~"system_cpu_load_average_15m|system\\\\.cpu\\\\.load_average\\\\.15m"}',
    )
    for r in data.get("result", []):
        node = r["metric"].get("k8s_node_name", r["metric"].get("node", "unknown"))
        try:
            load = float(r["value"][1])
        except (IndexError, ValueError):
            continue

        # mercury is control plane — lower threshold
        warning_threshold = 4.0 if "mercury" in node else 8.0
        critical_threshold = warning_threshold * 2

        if load > critical_threshold:
            alerts.append(Alert(
                check_name="CPU Load Critical",
                severity="critical",
                message=f"{node}: 15m load average {load:.1f} (threshold: {critical_threshold})",
            ))
        elif load > warning_threshold:
            alerts.append(Alert(
                check_name="CPU Load High",
                severity="warning",
                message=f"{node}: 15m load average {load:.1f} (threshold: {warning_threshold})",
            ))
    return alerts


async def check_memory(config) -> list[Alert]:
    """Check memory usage percentage across nodes."""
    alerts = []
    # OTel hostmetrics: system.memory.usage with state label
    data = await query_metrics(
        config,
        '{__name__=~"system_memory_usage|system\\\\.memory\\\\.usage"}',
    )
    # Group by node
    node_mem: dict[str, dict[str, float]] = {}
    for r in data.get("result", []):
        node = r["metric"].get("k8s_node_name", r["metric"].get("node", "unknown"))
        state = r["metric"].get("state", "")
        try:
            value = float(r["value"][1])
        except (IndexError, ValueError):
            continue
        node_mem.setdefault(node, {})[state] = value

    for node, states in node_mem.items():
        used = states.get("used", 0)
        free = states.get("free", 0)
        total = used + free
        if total == 0:
            continue
        pct = (used / total) * 100

        if pct > 95:
            alerts.append(Alert(
                check_name="Memory Critical",
                severity="critical",
                message=f"{node}: memory usage {pct:.0f}%",
            ))
        elif pct > 85:
            alerts.append(Alert(
                check_name="Memory High",
                severity="warning",
                message=f"{node}: memory usage {pct:.0f}%",
            ))
    return alerts


async def check_disk(config) -> list[Alert]:
    """Check disk usage percentage across nodes."""
    alerts = []
    data = await query_metrics(
        config,
        '{__name__=~"system_filesystem_usage|system\\\\.filesystem\\\\.usage"}',
    )
    node_disk: dict[str, dict[str, float]] = {}
    for r in data.get("result", []):
        node = r["metric"].get("k8s_node_name", r["metric"].get("node", "unknown"))
        state = r["metric"].get("state", "")
        mountpoint = r["metric"].get("mountpoint", "")
        # Only check root filesystem
        if mountpoint != "/":
            continue
        try:
            value = float(r["value"][1])
        except (IndexError, ValueError):
            continue
        key = f"{node}:{mountpoint}"
        node_disk.setdefault(key, {"node": node, "mount": mountpoint})[state] = value

    for key, info in node_disk.items():
        used = info.get("used", 0)
        free = info.get("free", 0)
        total = used + free
        if total == 0:
            continue
        pct = (used / total) * 100

        if pct > 90:
            alerts.append(Alert(
                check_name="Disk Critical",
                severity="critical",
                message=f"{info['node']}: disk usage {pct:.0f}% on {info['mount']}",
            ))
        elif pct > 80:
            alerts.append(Alert(
                check_name="Disk High",
                severity="warning",
                message=f"{info['node']}: disk usage {pct:.0f}% on {info['mount']}",
            ))
    return alerts


# --- Pod Health Checks ---


async def check_crashlooping(config) -> list[Alert]:
    """Check for pods with high restart counts in the last hour."""
    alerts = []
    data = await query_metrics(
        config,
        "increase(kube_pod_container_status_restarts_total[1h]) > 5",
    )
    for r in data.get("result", []):
        pod = r["metric"].get("pod", "unknown")
        ns = r["metric"].get("namespace", "unknown")
        try:
            restarts = float(r["value"][1])
        except (IndexError, ValueError):
            continue
        alerts.append(Alert(
            check_name="Pod CrashLooping",
            severity="warning",
            message=f"{ns}/{pod}: {restarts:.0f} restarts in the last hour",
        ))
    return alerts


async def check_stuck_pods(config) -> list[Alert]:
    """Check for pods stuck in Pending/Failed/Unknown phase."""
    alerts = []
    data = await query_metrics(
        config,
        'kube_pod_status_phase{phase=~"Pending|Failed|Unknown"} == 1',
    )
    for r in data.get("result", []):
        pod = r["metric"].get("pod", "unknown")
        ns = r["metric"].get("namespace", "unknown")
        phase = r["metric"].get("phase", "unknown")
        alerts.append(Alert(
            check_name="Stuck Pod",
            severity="warning",
            message=f"{ns}/{pod}: stuck in {phase}",
        ))
    return alerts


# --- Service Health Checks ---


async def check_endpoints(config) -> list[Alert]:
    """Probe external HTTP(S) endpoints via Traefik."""
    alerts = []
    for ep in config.probe_endpoints:
        result = await check_endpoint_via_traefik(
            host=ep["host"],
            traefik_ip=config.traefik_ip,
        )
        if not result["healthy"]:
            error_detail = result["error"] or f"HTTP {result['status_code']}"
            alerts.append(Alert(
                check_name=f"{ep['name']} Unreachable",
                severity=ep["severity"],
                message=f"{result['url']}: {error_detail}",
            ))
    return alerts


async def check_internal_health(config) -> list[Alert]:
    """Check internal service health endpoints."""
    alerts = []
    for svc in config.internal_health_checks:
        result = await check_endpoint(svc["url"])
        if not result["healthy"]:
            error_detail = result["error"] or f"HTTP {result['status_code']}"
            alerts.append(Alert(
                check_name=f"{svc['name']} Unhealthy",
                severity=svc["severity"],
                message=f"{svc['url']}: {error_detail}",
            ))
    return alerts


async def check_dns_resolution(config) -> list[Alert]:
    """Check DNS resolution via Technitium."""
    alerts = []
    for check in config.dns_checks:
        result = await check_dns(
            query=check["query"],
            server=config.dns_server,
            expected=check.get("expected"),
        )
        if not result["resolved"]:
            alerts.append(Alert(
                check_name=f"DNS {check['name']} Failed",
                severity=check["severity"],
                message=f"Cannot resolve {check['query']} via {config.dns_server}: {result['error']}",
            ))
        elif not result["matches_expected"]:
            alerts.append(Alert(
                check_name=f"DNS {check['name']} Mismatch",
                severity=check["severity"],
                message=f"{check['query']} resolved to {result['addresses']}, "
                        f"expected {check.get('expected')}",
            ))
    return alerts


# --- Log-Based Checks ---


async def check_oom_kills(config) -> list[Alert]:
    """Check for OOMKilled events in logs."""
    entries = await query_logs(
        config,
        '{k8s_namespace_name=~".+"} |~ "OOMKilled|Out of memory"',
        limit=5,
        since="5m",
    )
    if entries:
        alerts = [Alert(
            check_name="OOM Kill Detected",
            severity="warning",
            message=f"{len(entries)} OOM event(s) in the last 5 minutes:\n"
                    + "\n".join(
                        f"  {e['labels'].get('k8s_namespace_name', '?')}/{e['labels'].get('k8s_pod_name', '?')}"
                        for e in entries[:5]
                    ),
        )]
        return alerts
    return []


async def check_flux_errors(config) -> list[Alert]:
    """Check for Flux reconciliation errors."""
    entries = await query_logs(
        config,
        '{k8s_namespace_name="flux-system"} |~ "error|reconciliation failed"',
        limit=5,
        since="5m",
    )
    if entries:
        return [Alert(
            check_name="Flux Errors",
            severity="warning",
            message=f"{len(entries)} Flux error(s) in the last 5 minutes:\n"
                    + "\n".join(e["line"][:150] for e in entries[:3]),
        )]
    return []


async def check_db_fatal(config) -> list[Alert]:
    """Check for PostgreSQL FATAL/PANIC errors."""
    entries = await query_logs(
        config,
        '{k8s_namespace_name="database"} |~ "FATAL|PANIC"',
        limit=5,
        since="5m",
    )
    if entries:
        return [Alert(
            check_name="Database Fatal Error",
            severity="critical",
            message=f"{len(entries)} FATAL/PANIC error(s) in database:\n"
                    + "\n".join(e["line"][:150] for e in entries[:3]),
        )]
    return []


# --- Run All Checks ---


ALL_CHECKS = [
    check_node_ready,
    check_cpu_load,
    check_memory,
    check_disk,
    check_crashlooping,
    check_stuck_pods,
    check_endpoints,
    check_internal_health,
    check_dns_resolution,
    check_oom_kills,
    check_flux_errors,
    check_db_fatal,
]


async def run_all_checks(config) -> list[Alert]:
    """Run all Tier 0 checks and return combined alerts."""
    all_alerts = []
    for check_fn in ALL_CHECKS:
        try:
            alerts = await check_fn(config)
            all_alerts.extend(alerts)
        except Exception:
            log.exception("Check %s failed", check_fn.__name__)
            all_alerts.append(Alert(
                check_name=f"Check Error: {check_fn.__name__}",
                severity="warning",
                message=f"Check {check_fn.__name__} raised an exception",
            ))
    return all_alerts
