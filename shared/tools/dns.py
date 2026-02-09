"""DNS resolution checks via Technitium."""

import socket

import dns.resolver


def _resolve_server(server: str) -> str:
    """Resolve a server hostname to an IP if needed (dnspython requires IPs)."""
    try:
        socket.inet_aton(server)
        return server  # Already an IP
    except OSError:
        info = socket.getaddrinfo(server, 53, socket.AF_INET)
        return info[0][4][0]


async def check_dns(
    query: str, server: str, expected: str | None = None, timeout: float = 5.0
) -> dict:
    """Resolve a DNS query against a specific server.

    Args:
        query: Domain name to resolve.
        server: DNS server IP or hostname.
        expected: If set, verify the result matches this IP.

    Returns:
        Dict with keys: query, server, resolved, addresses, matches_expected, error
    """
    server_ip = _resolve_server(server)
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server_ip]
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
