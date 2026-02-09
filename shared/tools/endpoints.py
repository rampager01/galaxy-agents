"""HTTP endpoint health probes."""

import httpx


async def check_endpoint(
    url: str, timeout: float = 10.0, expected_status: int = 200
) -> dict:
    """Probe an HTTP(S) endpoint and return health status.

    Returns:
        Dict with keys: url, healthy, status_code, response_time_ms, error
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as client:
            resp = await client.get(url)
            return {
                "url": url,
                "healthy": resp.status_code == expected_status,
                "status_code": resp.status_code,
                "response_time_ms": round(resp.elapsed.total_seconds() * 1000),
                "error": None,
            }
    except httpx.TimeoutException:
        return {
            "url": url,
            "healthy": False,
            "status_code": None,
            "response_time_ms": None,
            "error": "timeout",
        }
    except Exception as e:
        return {
            "url": url,
            "healthy": False,
            "status_code": None,
            "response_time_ms": None,
            "error": str(e),
        }
