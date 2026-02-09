"""Kubernetes cluster status via the k8s API."""

from kubernetes import client, config as k8s_config


def _get_k8s_client() -> client.CoreV1Api:
    """Load in-cluster or local kubeconfig."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return client.CoreV1Api()


def _get_apps_client() -> client.AppsV1Api:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return client.AppsV1Api()


async def get_cluster_status() -> dict:
    """Get a summary of cluster health: nodes, pods, deployments.

    Returns a dict with compact cluster overview.
    """
    core = _get_k8s_client()
    apps = _get_apps_client()

    # Nodes
    nodes = core.list_node()
    node_info = []
    for node in nodes.items:
        conditions = {c.type: c.status for c in node.status.conditions}
        node_info.append({
            "name": node.metadata.name,
            "ready": conditions.get("Ready") == "True",
            "memory_pressure": conditions.get("MemoryPressure") == "True",
            "disk_pressure": conditions.get("DiskPressure") == "True",
        })

    # Pods (all namespaces)
    pods = core.list_pod_for_all_namespaces()
    pod_summary = {"running": 0, "pending": 0, "failed": 0, "unknown": 0, "succeeded": 0}
    problem_pods = []
    for pod in pods.items:
        phase = (pod.status.phase or "Unknown").lower()
        if phase in pod_summary:
            pod_summary[phase] += 1
        else:
            pod_summary["unknown"] += 1

        if phase in ("pending", "failed", "unknown"):
            problem_pods.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": phase,
            })

        # Check for crashlooping containers
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.restart_count and cs.restart_count > 5:
                    if cs.state and cs.state.waiting and cs.state.waiting.reason == "CrashLoopBackOff":
                        problem_pods.append({
                            "name": pod.metadata.name,
                            "namespace": pod.metadata.namespace,
                            "phase": "CrashLoopBackOff",
                            "restarts": cs.restart_count,
                        })

    # Deployments
    deployments = apps.list_deployment_for_all_namespaces()
    deploy_issues = []
    for d in deployments.items:
        desired = d.spec.replicas or 0
        available = d.status.available_replicas or 0
        if available < desired:
            deploy_issues.append({
                "name": d.metadata.name,
                "namespace": d.metadata.namespace,
                "desired": desired,
                "available": available,
            })

    return {
        "nodes": node_info,
        "nodes_ready": sum(1 for n in node_info if n["ready"]),
        "nodes_total": len(node_info),
        "pods": pod_summary,
        "problem_pods": problem_pods[:20],
        "deployment_issues": deploy_issues[:20],
    }


def format_cluster_status(status: dict) -> str:
    """Format cluster status into compact text for LLM consumption."""
    lines = [
        f"NODES: {status['nodes_ready']}/{status['nodes_total']} ready",
    ]
    for n in status["nodes"]:
        flags = []
        if not n["ready"]:
            flags.append("NOT_READY")
        if n["memory_pressure"]:
            flags.append("MEM_PRESSURE")
        if n["disk_pressure"]:
            flags.append("DISK_PRESSURE")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {n['name']}{flag_str}")

    pods = status["pods"]
    lines.append(
        f"PODS: {pods['running']} running, {pods['pending']} pending, "
        f"{pods['failed']} failed, {pods['succeeded']} succeeded"
    )

    if status["problem_pods"]:
        lines.append("PROBLEM_PODS:")
        for p in status["problem_pods"][:10]:
            lines.append(f"  {p['namespace']}/{p['name']}: {p['phase']}")

    if status["deployment_issues"]:
        lines.append("DEPLOYMENT_ISSUES:")
        for d in status["deployment_issues"]:
            lines.append(f"  {d['namespace']}/{d['name']}: {d['available']}/{d['desired']} replicas")

    return "\n".join(lines)
