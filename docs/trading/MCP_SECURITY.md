# MCP security

`MCPServerPolicy` and `mcp_tool_allowed` are pure declaration checks. No MCP
executor, server process or network listener is installed. Active/version/tool/
permission/timeout/concurrency/revocation checks default deny. Privileged
permissions never pass the analytical allowlist.

A passing declaration check is not a sandbox or authorisation to invoke an
arbitrary tool. Future configuration must bind approved server identity and
transport to exact request/response schemas, constrained process/network scope,
audit and revocation. This remains unimplemented and disabled.

External text is input to research, never instructions selecting tools or enabling
execution. No execution or secret access capability is supplied. Prompt-injection
text passed to the research API cannot trigger any operational action. Tests cover
unknown/privileged tools and revoked policies; no claim of external MCP hardening.
