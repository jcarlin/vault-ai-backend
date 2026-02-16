import asyncio
from datetime import timezone

import typer
from rich.console import Console
from rich.table import Table

console = Console()
cli_app = typer.Typer(name="vault-admin", help="Vault AI administrative CLI")


def _run_async(coro):
    """Run async code from sync CLI context."""
    return asyncio.run(coro)


async def _ensure_db():
    from app.core.database import init_db
    await init_db()


@cli_app.command("create-key")
def create_key(
    label: str = typer.Option(..., "--label", help="Human-readable label for this key"),
    scope: str = typer.Option("user", "--scope", help="Key scope: 'user' or 'admin'"),
    notes: str = typer.Option(None, "--notes", help="Optional notes"),
):
    """Create a new API key."""
    async def _create():
        await _ensure_db()
        from app.services.auth import AuthService
        service = AuthService()
        raw_key, key_row = await service.create_key(label=label, scope=scope, notes=notes)
        return raw_key, key_row

    raw_key, key_row = _run_async(_create())

    console.print(f"\n[bold green]API key created successfully![/bold green]\n")
    console.print(f"  Label:  {key_row.label}")
    console.print(f"  Scope:  {key_row.scope}")
    console.print(f"  Prefix: {key_row.key_prefix}")
    console.print(f"\n  [bold yellow]Key: {raw_key}[/bold yellow]")
    console.print(f"\n  [dim]Save this key now — it cannot be retrieved later.[/dim]\n")


@cli_app.command("list-keys")
def list_keys():
    """List all active API keys."""
    async def _list():
        await _ensure_db()
        from app.services.auth import AuthService
        service = AuthService()
        return await service.list_keys()

    keys = _run_async(_list())

    if not keys:
        console.print("[dim]No active API keys found.[/dim]")
        return

    table = Table(title="Active API Keys")
    table.add_column("Prefix", style="cyan")
    table.add_column("Label")
    table.add_column("Scope", style="green")
    table.add_column("Created")
    table.add_column("Last Used")

    for key in keys:
        created = key.created_at.strftime("%Y-%m-%d %H:%M") if key.created_at else "—"
        last_used = key.last_used_at.strftime("%Y-%m-%d %H:%M") if key.last_used_at else "never"
        table.add_row(key.key_prefix, key.label, key.scope, created, last_used)

    console.print(table)


@cli_app.command("revoke-key")
def revoke_key(
    key: str = typer.Argument(help="Full API key or key prefix to revoke"),
):
    """Revoke an API key."""
    async def _revoke():
        await _ensure_db()
        from app.services.auth import AuthService
        service = AuthService()
        return await service.revoke_key(key)

    success = _run_async(_revoke())

    if success:
        console.print(f"[bold red]Key revoked successfully.[/bold red]")
    else:
        console.print(f"[yellow]No active key found matching '{key}'.[/yellow]")
        raise typer.Exit(code=1)


def main():
    cli_app()


if __name__ == "__main__":
    main()
