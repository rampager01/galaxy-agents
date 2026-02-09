"""Environment-based configuration for galaxy-agents."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    # Cluster endpoints (in-cluster defaults)
    victoria_metrics_url: str = field(
        default_factory=lambda: os.getenv(
            "VICTORIA_METRICS_URL",
            "http://victoria-metrics-single-server.monitoring.svc.cluster.home:8428",
        )
    )
    loki_url: str = field(
        default_factory=lambda: os.getenv(
            "LOKI_URL",
            "http://loki.monitoring.svc.cluster.home:3100",
        )
    )
    dns_server: str = field(
        default_factory=lambda: os.getenv("DNS_SERVER", "technitium-dns.dns.svc.cluster.home")
    )

    # Slack
    slack_webhook_url: str = field(
        default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", "")
    )

    # LLM providers
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "")
    )
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic")
    )

    # Scheduling
    check_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
    )
    digest_hour: int = field(
        default_factory=lambda: int(os.getenv("DIGEST_HOUR", "8"))
    )

    # Traefik ingress URL (for probing external endpoints from inside the cluster)
    traefik_url: str = field(
        default_factory=lambda: os.getenv(
            "TRAEFIK_URL",
            "https://traefik.external-traefik.svc.cluster.home",
        )
    )

    # Endpoints to probe (via Traefik with Host header, since CoreDNS can't resolve local domains)
    probe_endpoints: list[dict[str, str]] = field(default_factory=lambda: [
        {"name": "n8n", "host": "workflows.stargate-labs.net", "severity": "critical"},
        {"name": "grafana", "host": "grafana.stargate-labs.net", "severity": "warning"},
    ])

    # DNS checks
    dns_checks: list[dict[str, str]] = field(default_factory=lambda: [
        {
            "name": "external-resolution",
            "query": "google.com",
            "severity": "critical",
        },
        {
            "name": "internal-dns",
            "query": "workflows.stargate-labs.net",
            "expected": "10.112.9.201",
            "severity": "critical",
        },
    ])

    # Internal service health checks
    internal_health_checks: list[dict[str, str]] = field(default_factory=lambda: [
        {
            "name": "victoria-metrics",
            "url": "http://victoria-metrics-single-server.monitoring.svc.cluster.home:8428/health",
            "severity": "warning",
        },
        {
            "name": "loki",
            "url": "http://loki.monitoring.svc.cluster.home:3100/ready",
            "severity": "warning",
        },
    ])

    # Cluster info
    cluster_nodes: list[str] = field(
        default_factory=lambda: ["mercury-server", "venus", "mars", "earth"]
    )
    expected_node_count: int = 4


def load_config() -> Config:
    return Config()
