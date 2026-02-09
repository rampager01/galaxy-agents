"""Query Loki via LogQL."""

import httpx


async def query_logs(
    config,
    logql: str,
    limit: int = 20,
    since: str = "5m",
    timeout: float = 15.0,
) -> list[dict]:
    """Query Loki for log entries.

    Args:
        logql: LogQL query string.
        limit: Maximum number of log lines to return.
        since: Time range to look back (e.g., "5m", "1h", "24h").

    Returns:
        List of log entries with timestamp and line.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{config.loki_url}/loki/api/v1/query_range",
            params={
                "query": logql,
                "limit": str(limit),
                "since": since,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    entries = []
    results = data.get("data", {}).get("result", [])
    for stream in results:
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            entries.append({
                "timestamp": ts,
                "labels": labels,
                "line": line,
            })
    # Sort by timestamp descending, limit
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[:limit]


def format_log_results(entries: list[dict]) -> str:
    """Format log entries into compact text for LLM consumption."""
    if not entries:
        return "no log entries found"
    lines = []
    for e in entries:
        ns = e["labels"].get("k8s_namespace_name", "?")
        pod = e["labels"].get("k8s_pod_name", "?")
        lines.append(f"[{ns}/{pod}] {e['line'][:200]}")
    return "\n".join(lines)
