"""DNS resolution checks via Technitium."""

import dns.resolver


async def check_dns(
    query: str, server: str, expected: str | None = None, timeout: float = 5.0
) -> dict:
    """Resolve a DNS query against a specific server.

    Args:
        query: Domain name to resolve.
        server: DNS server IP address.
        expected: If set, verify the result matches this IP.

    Returns:
        Dict with keys: query, server, resolved, addresses, matches_expected, error
    """
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server]
    resolver.lifetime = timeout

    try:
        answers = resolver.resolve(query, "A")
        addresses = sorted(str(rdata) for rdata in answers)
        matches = expected is None or expected in addresses
        return {
            "query": query,
            "server": server,
            "resolved": True,
            "addresses": addresses,
            "matches_expected": matches,
            "error": None,
        }
    except dns.resolver.NXDOMAIN:
        return {
            "query": query,
            "server": server,
            "resolved": False,
            "addresses": [],
            "matches_expected": False,
            "error": "NXDOMAIN",
        }
    except dns.resolver.NoAnswer:
        return {
            "query": query,
            "server": server,
            "resolved": False,
            "addresses": [],
            "matches_expected": False,
            "error": "no answer",
        }
    except dns.exception.Timeout:
        return {
            "query": query,
            "server": server,
            "resolved": False,
            "addresses": [],
            "matches_expected": False,
            "error": "timeout",
        }
    except Exception as e:
        return {
            "query": query,
            "server": server,
            "resolved": False,
            "addresses": [],
            "matches_expected": False,
            "error": str(e),
        }
