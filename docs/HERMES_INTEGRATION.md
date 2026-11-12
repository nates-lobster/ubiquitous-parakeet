# Hermes / MCP integration status

The repo now has an optional stdio MCP server (`parakeet-mcp`) with tools for presence status, eligibility checks, and explicit local event logging. It can be installed with `./scripts/dev-setup.sh --mcp` and run directly:

```bash
.venv/bin/parakeet-mcp --workspace /absolute/path/to/alzheimers_buddy
```

A common MCP host configuration shape looks like this (paths are examples, not a verified Hermes-specific file):

```json
{
  "mcpServers": {
    "ubiquitous-parakeet": {
      "command": "/absolute/path/to/ubiquitous-parakeet/.venv/bin/parakeet-mcp",
      "args": ["--workspace", "/absolute/path/to/alzheimers_buddy"],
      "env": { "PARAKEET_HARDWARE": "mock" }
    }
  }
}
```

On a Pi, use a confirmed GPIO BCM pin via `PARAKEET_PRESENCE_GPIO` and install the optional `hardware` extra. Keep the MCP process on stdio; do not expose it as an unauthenticated network service.

**Important limitation:** the MCP `check_in_eligibility` tool reports a decision; it cannot intercept a model call made by Hermes. `invoke_if_eligible(...)` is the intended local orchestration seam, but this repository does not yet contain a Hermes-specific adapter or provider client. To enforce zero calls, the target Hermes runtime must support a pre-inference hook/middleware (or have all model requests routed through a wrapper that calls this gate). Registering the MCP tools alone is insufficient.

The exact “Hermes Agent” project/version and its MCP config schema were not specified. Before writing a drop-in Hermes configuration or pre-inference adapter, confirm which Hermes distribution/version you intend to run (for example, the NousResearch Hermes Agent project) and where you want its config managed. That avoids tying the project to an incompatible config/plugin API. Similarly, the physical HAL currently supports the digital OUT pin only; confirm the exact LD2410 board/interface and chosen Pi wiring before enabling GPIO.
