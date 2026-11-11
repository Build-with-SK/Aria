# Plugin system

No existing trading plugin registry was found. This release adds declaration
contracts and pure permission checks, not a dynamic loader. Every third-party
activation returns inactive because no verified OS sandbox exists. No caller can
turn that guarantee on with a boolean claiming to have a sandbox.

Manifests declare name/version/API, entrypoint, capabilities, permissions, required
secret names (not values), dependencies, resource limits, network scope, health
check and environments. Analytical scope permits public research/data, analytics,
research writes and proposals only. Account/private portfolio, approval, order
mutation, plugin administration and secrets are denied for third-party activation.

Before actual extension support: separate low-privilege process, constrained files,
CPU/memory/time/network, signed/versioned allowlist, explicit authenticated owner
activation, bounded schema-validated messages, revocation and audit. Analytical
workers must never import into an execution process or receive broker credentials.
