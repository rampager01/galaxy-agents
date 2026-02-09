"""Tier 2: AI-powered anomaly investigation using tool-use loop."""

import json
import logging
from pathlib import Path

from shared.llm.provider import LLMProvider
from shared.tools.cluster import format_cluster_status, get_cluster_status
from shared.tools.logs import format_log_results, query_logs
from shared.tools.metrics import format_instant_results, query_metrics
from shared.tools.slack import send_slack_alert

log = logging.getLogger("sentinel.investigate")

MAX_TOOL_ROUNDS = 5

# Tool definitions for Claude tool-use
INVESTIGATION_TOOLS = [
    {
        "name": "query_metrics",
        "description": (
            "Query VictoriaMetrics with a PromQL expression. Returns metric values. "
            "Use this to check resource usage, pod status, node conditions, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "promql": {
                    "type": "string",
                    "description": "The PromQL query to execute",
                },
            },
            "required": ["promql"],
        },
    },
    {
        "name": "query_logs",
        "description": (
            "Query Loki with a LogQL expression. Returns recent log lines. "
            "Labels use OTel convention: k8s_namespace_name, k8s_pod_name, k8s_container_name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "logql": {
                    "type": "string",
                    "description": "The LogQL query to execute",
                },
                "since": {
                    "type": "string",
                    "description": "How far back to look (e.g., '5m', '1h', '24h'). Default: 15m",
                    "default": "15m",
                },
            },
            "required": ["logql"],
        },
    },
    {
        "name": "get_cluster_status",
        "description": (
            "Get current cluster status: nodes, pods, deployments. "
            "Returns a summary of what's healthy and what's not."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "send_alert",
        "description": (
            "Send an alert to Slack with your investigation findings. "
            "Call this when you have determined the root cause and severity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["critical", "warning", "info", "resolved"],
                    "description": "Alert severity level",
                },
                "title": {
                    "type": "string",
                    "description": "Short alert title",
                },
                "message": {
                    "type": "string",
                    "description": "Detailed alert message with root cause analysis",
                },
            },
            "required": ["severity", "title", "message"],
        },
    },
]


async def _execute_tool(config, tool_name: str, tool_input: dict) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if tool_name == "query_metrics":
            data = await query_metrics(config, tool_input["promql"])
            return format_instant_results(data)

        elif tool_name == "query_logs":
            entries = await query_logs(
                config,
                tool_input["logql"],
                since=tool_input.get("since", "15m"),
            )
            return format_log_results(entries)

        elif tool_name == "get_cluster_status":
            status = await get_cluster_status()
            return format_cluster_status(status)

        elif tool_name == "send_alert":
            await send_slack_alert(
                config,
                severity=tool_input["severity"],
                title=tool_input["title"],
                message=tool_input["message"],
            )
            return "Alert sent to Slack successfully."

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Tool error: {e}"


def _load_system_prompt() -> str:
    """Load the agent system prompt from system.md."""
    prompt_path = Path(__file__).parent / "system.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return (
        "You are Galaxy Sentinel, a monitoring AI for a Kubernetes cluster. "
        "Investigate the reported alerts using the available tools. "
        "Determine the root cause and send an appropriate alert to Slack."
    )


async def investigate_alerts(config, alerts: list):
    """Run AI investigation for a batch of alerts.

    The AI agent receives the alert context and uses tools to investigate
    the root cause before sending a contextual alert to Slack.
    """
    # Format alerts into a user message
    alert_lines = []
    for a in alerts:
        alert_lines.append(f"[{a.severity.upper()}] {a.check_name}: {a.message}")
    alert_text = "\n".join(alert_lines)

    log.info("Starting Tier 2 investigation for %d alert(s)", len(alerts))

    system_prompt = _load_system_prompt()
    llm = LLMProvider(config)

    messages = [
        {
            "role": "user",
            "content": (
                f"The following alerts were just detected by automated monitoring:\n\n"
                f"{alert_text}\n\n"
                f"Investigate the root cause using the available tools, then send an alert "
                f"to Slack with your findings. Be concise and actionable."
            ),
        }
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        log.info("Investigation round %d/%d", round_num + 1, MAX_TOOL_ROUNDS)

        response = await llm.complete(
            system=system_prompt,
            messages=messages,
            max_tokens=2000,
            tools=INVESTIGATION_TOOLS,
            model="claude-haiku-4-5-20251001",
        )

        if not response["tool_calls"]:
            # AI is done — it responded with text only
            log.info("Investigation complete (text response)")
            # If the AI didn't call send_alert, send the raw response
            if response["content"]:
                await send_slack_alert(
                    config,
                    severity="warning",
                    title="Investigation Result",
                    message=response["content"][:3000],
                )
            break

        # Build the assistant message with both text and tool_use blocks
        assistant_content = []
        if response["content"]:
            assistant_content.append({"type": "text", "text": response["content"]})
        for tc in response["tool_calls"]:
            assistant_content.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            })
        messages.append({"role": "assistant", "content": assistant_content})

        # Execute each tool call and build tool_result messages
        tool_results = []
        for tc in response["tool_calls"]:
            log.info("  Tool call: %s(%s)", tc["name"], json.dumps(tc["input"])[:100])
            result = await _execute_tool(config, tc["name"], tc["input"])
            log.info("  Result: %s", result[:200])
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result[:2000],
            })

        messages.append({"role": "user", "content": tool_results})

        # Check if the AI called send_alert — if so, we can stop
        if any(tc["name"] == "send_alert" for tc in response["tool_calls"]):
            log.info("Investigation complete (alert sent)")
            break
    else:
        log.warning("Investigation hit max rounds without concluding")
        # Send what we know
        await send_slack_alert(
            config,
            severity="warning",
            title="Investigation Timeout",
            message=f"AI investigation for the following alerts hit the {MAX_TOOL_ROUNDS}-round limit:\n{alert_text}",
        )
