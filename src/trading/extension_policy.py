"""Declaration checks only. No dynamic plugin loading or claimed sandbox."""
from .contracts import Contract, PluginManifest


ANALYTICAL_PERMISSIONS = frozenset({
    "READ_MARKET_DATA", "READ_PUBLIC_RESEARCH", "RUN_ANALYTICS",
    "WRITE_RESEARCH", "PROPOSE_TRADE",
})


def plugin_activation(manifest: PluginManifest, *, explicitly_approved: bool = False) -> dict:
    reasons = []
    if manifest.api_version != "1":
        reasons.append("API_VERSION_UNSUPPORTED")
    if not explicitly_approved:
        reasons.append("ACTIVATION_NOT_APPROVED")
    if set(manifest.requested_permissions) - ANALYTICAL_PERMISSIONS:
        reasons.append("PRIVILEGED_PERMISSION_DENIED")
    if manifest.required_secret_names:
        reasons.append("SECRET_ACCESS_DENIED")
    # No caller flag can claim the OS sandbox exists. This release has none.
    reasons.append("THIRD_PARTY_SANDBOX_UNAVAILABLE")
    return {"active": False, "reasons": reasons, "execution_privileges": False}


class MCPServerPolicy(Contract):
    server_id: str
    api_version: str
    allowed_tools: tuple[str, ...]
    permissions: tuple[str, ...]
    max_concurrency: int
    timeout_seconds: int
    active: bool = False
    revoked: bool = False


def mcp_tool_allowed(policy: MCPServerPolicy, *, tool: str, permission: str) -> bool:
    """Pure allowlist check, not a client or process sandbox.

    Tool names are operator declarations, never parsed from model/MCP text.
    The release has no MCP executor even when a declaration passes this check.
    """
    return bool(policy.active and not policy.revoked and policy.api_version == "1"
                and 1 <= policy.max_concurrency <= 4 and 1 <= policy.timeout_seconds <= 30
                and tool in policy.allowed_tools and permission in policy.permissions
                and permission in ANALYTICAL_PERMISSIONS
                and not (set(policy.permissions) - ANALYTICAL_PERMISSIONS))
