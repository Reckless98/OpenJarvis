"""``jarvis cockpit`` — deterministic local Jarvis cockpit routing."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from openjarvis.tools.filip_cockpit import detect_tools, execute_route, route_text


@click.command()
@click.argument("request", nargs=-1)
@click.option(
    "--repo",
    "repo_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Repository or workspace path.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected route without executing it."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Print machine-readable route/result JSON."
)
def cockpit(
    request: tuple[str, ...], repo_path: Path | None, dry_run: bool, as_json: bool
) -> None:
    """Route a text request to Codex, Claude, Lumo, Perplexity, .aria, or safe shell."""
    console = Console()
    text = " ".join(request).strip()
    if not text:
        raise click.UsageError(
            "Provide a cockpit request, for example: "
            "jarvis cockpit 'show system status'"
        )

    decision = route_text(text)
    result = execute_route(text, str(repo_path) if repo_path else None, dry_run=dry_run)
    payload = {
        "backend": decision.backend,
        "reason": decision.reason,
        "action": decision.action,
        "success": result.success,
        "result": result.content,
        "available": detect_tools(),
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return

    title = f"Jarvis cockpit -> {decision.backend}"
    subtitle = "success" if result.success else "failed"
    console.print(Panel(result.content, title=title, subtitle=subtitle))


__all__ = ["cockpit"]
