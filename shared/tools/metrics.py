"""Query VictoriaMetrics via PromQL."""

import httpx


async def query_metrics(config, promql: str, timeout: float = 10.0) -> dict:
    """Execute an instant PromQL query against VictoriaMetrics.

    Returns the parsed JSON response with result type and values.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{config.victoria_metrics_url}/api/v1/query",
            params={"query": promql},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {})


async def query_metrics_range(
    config, promql: str, start: str, end: str, step: str = "5m", timeout: float = 30.0
) -> dict:
    """Execute a range PromQL query against VictoriaMetrics.

    Args:
        start/end: RFC3339 or Unix timestamp strings.
        step: Query resolution step (e.g., "5m", "1h").
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{config.victoria_metrics_url}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {})


def format_instant_results(data: dict) -> str:
    """Format instant query results into a compact string for LLM consumption."""
    results = data.get("result", [])
    if not results:
        return "no data"
    lines = []
    for r in results:
        labels = r.get("metric", {})
        value = r.get("value", [None, "?"])[1]
        label_str = ", ".join(f"{k}={v}" for k, v in labels.items() if k != "__name__")
        name = labels.get("__name__", "")
        if label_str:
            lines.append(f"{name}{{{label_str}}} = {value}")
        else:
            lines.append(f"{name} = {value}")
    return "\n".join(lines)
