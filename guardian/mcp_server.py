"""FastMCP server for MCPGuardian Phase 5A.

Security boundary:
This MCP server exposes MCPGuardian controls to Claude Desktop. It does not
enforce policy over other MCP servers unless those servers are removed from
Claude Desktop config or routed through a future Guardian Gateway.
"""
from __future__ import annotations

from typing import Any

if __package__ in {None, ""}:  # Support `python guardian/mcp_server.py` from Claude Desktop config.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from guardian import mcp_tools  # type: ignore
else:
    from . import mcp_tools


def _load_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised only without SDK at runtime
        raise RuntimeError(
            "MCP SDK is not installed. Install the `mcp` package or run the transport-agnostic "
            "guardian.mcp_tools functions directly for tests."
        ) from exc
    return FastMCP


def create_server() -> Any:
    FastMCP = _load_fastmcp()
    server = FastMCP("MCPGuardian")

    @server.tool()
    def mcpguardian_preflight(
        task_type: str,
        requested_action: str = "",
        input_paths: list[str] | None = None,
        existing_artifacts: list[str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate MCPGuardian hard preflight gate and write structured trace."""
        return mcp_tools.mcpguardian_preflight(
            task_type=task_type,
            requested_action=requested_action,
            input_paths=input_paths,
            existing_artifacts=existing_artifacts,
            run_id=run_id,
        )

    @server.tool()
    def mcpguardian_validate_xlsx(
        input_path: str,
        analyzed_sheets: list[str] | None = None,
        assume_full_analysis: bool = False,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate XLSX sheet, row-count, coverage, and validator artifacts."""
        return mcp_tools.mcpguardian_validate_xlsx(
            input_path=input_path,
            analyzed_sheets=analyzed_sheets,
            assume_full_analysis=assume_full_analysis,
            run_id=run_id,
        )

    @server.tool()
    def mcpguardian_validate_claim_manifest(
        manifest_path: str,
        run_id: str | None = None,
        run_dir: str | None = None,
        output_document: str | None = None,
        strict_output_coverage: bool = False,
    ) -> dict[str, Any]:
        """Validate explicit quantitative-claim manifest and evidence artifacts."""
        return mcp_tools.mcpguardian_validate_claim_manifest(
            manifest_path=manifest_path,
            run_id=run_id,
            run_dir=run_dir,
            output_document=output_document,
            strict_output_coverage=strict_output_coverage,
        )

    @server.tool()
    def mcpguardian_analyze_runs(min_occurrences: int = 2) -> dict[str, Any]:
        """Analyze structured runs and update pending_rules.json only."""
        return mcp_tools.mcpguardian_analyze_runs(min_occurrences=min_occurrences)

    @server.tool()
    def mcpguardian_list_pending_rules(status: str | None = None) -> dict[str, Any]:
        """List pending rule candidates."""
        return mcp_tools.mcpguardian_list_pending_rules(status=status)

    @server.tool()
    def mcpguardian_approve_rule(rule_id: str, note: str | None = None) -> dict[str, Any]:
        """Approve a pending rule. Disabled unless mutation is explicitly enabled."""
        return mcp_tools.mcpguardian_approve_rule(rule_id=rule_id, note=note)

    @server.tool()
    def mcpguardian_reject_rule(rule_id: str, reason: str) -> dict[str, Any]:
        """Reject a pending rule. Disabled unless mutation is explicitly enabled."""
        return mcp_tools.mcpguardian_reject_rule(rule_id=rule_id, reason=reason)

    @server.tool()
    def mcpguardian_get_run_summary(run_id: str) -> dict[str, Any]:
        """Return artifact and trace summary for a run directory."""
        return mcp_tools.mcpguardian_get_run_summary(run_id=run_id)

    return server


def main() -> None:
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
