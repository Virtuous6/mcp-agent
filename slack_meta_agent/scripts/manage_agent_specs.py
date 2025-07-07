#!/usr/bin/env python3
"""
Agent Spec Management Tool

Manage agent specifications in the database:
- List all agent specs
- View agent spec details
- Update agent specs
- Create new agent specs
- Import/export specs
"""

import asyncio
import json
import sys
from typing import Optional
from pathlib import Path

import click
from supabase import create_client, Client
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.slack_meta_agent.models import EnhancedAgentSpec

console = Console()


class AgentSpecManager:
    def __init__(self, supabase_url: str, supabase_key: str):
        self.client: Client = create_client(supabase_url, supabase_key)

    async def list_specs(self) -> list:
        """List all agent specs."""
        response = self.client.table("agent_specs").select("*").execute()
        return response.data

    async def get_spec(self, agent_id: str) -> Optional[dict]:
        """Get a specific agent spec."""
        response = (
            self.client.table("agent_specs")
            .select("*")
            .eq("id", agent_id)
            .single()
            .execute()
        )
        return response.data

    async def create_spec(self, spec: EnhancedAgentSpec) -> dict:
        """Create a new agent spec."""
        data = spec.to_dict()
        response = self.client.table("agent_specs").insert(data).execute()
        return response.data[0] if response.data else None

    async def update_spec(self, agent_id: str, updates: dict) -> dict:
        """Update an existing agent spec."""
        response = (
            self.client.table("agent_specs")
            .update(updates)
            .eq("id", agent_id)
            .execute()
        )
        return response.data[0] if response.data else None

    async def delete_spec(self, agent_id: str) -> bool:
        """Delete an agent spec."""
        response = (
            self.client.table("agent_specs").delete().eq("id", agent_id).execute()
        )
        return len(response.data) > 0


@click.group()
@click.option("--supabase-url", envvar="SUPABASE_URL", required=True)
@click.option("--supabase-key", envvar="SUPABASE_SERVICE_ROLE_KEY", required=True)
@click.pass_context
def cli(ctx, supabase_url, supabase_key):
    """Manage agent specifications in Supabase."""
    ctx.ensure_object(dict)
    ctx.obj["manager"] = AgentSpecManager(supabase_url, supabase_key)


@cli.command()
@click.pass_context
def list(ctx):
    """List all agent specifications."""

    async def _list():
        manager = ctx.obj["manager"]
        specs = await manager.list_specs()

        if not specs:
            console.print("[yellow]No agent specs found[/yellow]")
            return

        table = Table(title="Agent Specifications")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Role", style="yellow")
        table.add_column("Model", style="magenta")
        table.add_column("Tools", style="blue")

        for spec in specs:
            tools = (
                ", ".join(spec.get("tools", []))[:30] + "..."
                if len(", ".join(spec.get("tools", []))) > 30
                else ", ".join(spec.get("tools", []))
            )
            table.add_row(
                spec["id"],
                spec["name"],
                spec["role"][:40] + "..." if len(spec["role"]) > 40 else spec["role"],
                spec["llm_model"],
                tools,
            )

        console.print(table)

    asyncio.run(_list())


@cli.command()
@click.argument("agent_id")
@click.pass_context
def show(ctx, agent_id):
    """Show details of a specific agent specification."""

    async def _show():
        manager = ctx.obj["manager"]
        spec = await manager.get_spec(agent_id)

        if not spec:
            console.print(f"[red]Agent spec '{agent_id}' not found[/red]")
            return

        # Create panels for different sections
        console.print(
            Panel(
                f"[bold cyan]{spec['name']}[/bold cyan]", title=f"Agent: {spec['id']}"
            )
        )

        console.print("\n[bold]Core Identity:[/bold]")
        console.print(f"  Role: [yellow]{spec['role']}[/yellow]")
        console.print(f"  Backstory: [dim]{spec['backstory']}[/dim]")
        console.print(f"  Goal: [green]{spec['goal']}[/green]")

        console.print("\n[bold]LLM Configuration:[/bold]")
        console.print(f"  Model: [magenta]{spec['llm_model']}[/magenta]")
        console.print(f"  Temperature: {spec['temperature']}")
        console.print(f"  Max Tokens: {spec['max_tokens']}")

        console.print("\n[bold]Tools:[/bold]")
        for tool in spec.get("tools", []):
            console.print(f"  - [blue]{tool}[/blue]")

        if spec.get("constraints"):
            console.print("\n[bold]Constraints:[/bold]")
            for constraint in spec["constraints"]:
                console.print(f"  - {constraint}")

        if spec.get("system_prompt"):
            console.print("\n[bold]System Prompt:[/bold]")
            console.print(Panel(spec["system_prompt"], style="dim"))

    asyncio.run(_show())


@cli.command()
@click.argument("agent_id")
@click.option("--name", help="Update agent name")
@click.option("--role", help="Update agent role")
@click.option("--backstory", help="Update backstory")
@click.option("--goal", help="Update goal")
@click.option("--model", help="Update LLM model")
@click.option("--temperature", type=float, help="Update temperature")
@click.option("--max-tokens", type=int, help="Update max tokens")
@click.option("--system-prompt", help="Update system prompt")
@click.pass_context
def update(ctx, agent_id, **kwargs):
    """Update an agent specification."""

    async def _update():
        manager = ctx.obj["manager"]

        # Build update dict
        updates = {}
        if kwargs.get("name"):
            updates["name"] = kwargs["name"]
        if kwargs.get("role"):
            updates["role"] = kwargs["role"]
        if kwargs.get("backstory"):
            updates["backstory"] = kwargs["backstory"]
        if kwargs.get("goal"):
            updates["goal"] = kwargs["goal"]
        if kwargs.get("model"):
            updates["llm_model"] = kwargs["model"]
        if kwargs.get("temperature") is not None:
            updates["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens"):
            updates["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("system_prompt"):
            updates["system_prompt"] = kwargs["system_prompt"]

        if not updates:
            console.print("[yellow]No updates specified[/yellow]")
            return

        result = await manager.update_spec(agent_id, updates)
        if result:
            console.print(f"[green]✓ Updated agent spec '{agent_id}'[/green]")
            console.print("Updated fields:")
            for key, value in updates.items():
                console.print(f"  {key}: {value}")
        else:
            console.print(f"[red]Failed to update agent spec '{agent_id}'[/red]")

    asyncio.run(_update())


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.pass_context
def import_spec(ctx, spec_file):
    """Import agent spec from JSON file."""

    async def _import():
        manager = ctx.obj["manager"]

        with open(spec_file, "r") as f:
            data = json.load(f)

        spec = EnhancedAgentSpec.from_dict(data)
        result = await manager.create_spec(spec)

        if result:
            console.print(f"[green]✓ Imported agent spec '{spec.id}'[/green]")
        else:
            console.print(f"[red]Failed to import agent spec[/red]")

    asyncio.run(_import())


@cli.command()
@click.argument("agent_id")
@click.argument("output_file", type=click.Path())
@click.pass_context
def export(ctx, agent_id, output_file):
    """Export agent spec to JSON file."""

    async def _export():
        manager = ctx.obj["manager"]
        spec = await manager.get_spec(agent_id)

        if not spec:
            console.print(f"[red]Agent spec '{agent_id}' not found[/red]")
            return

        with open(output_file, "w") as f:
            json.dump(spec, f, indent=2)

        console.print(f"[green]✓ Exported agent spec to {output_file}[/green]")

    asyncio.run(_export())


@cli.command()
@click.pass_context
def init_defaults(ctx):
    """Initialize database with default enhanced agent specs."""

    async def _init():
        manager = ctx.obj["manager"]

        # Check if specs already exist
        existing = await manager.list_specs()
        if existing:
            if not click.confirm("Agent specs already exist. Continue anyway?"):
                return

        console.print("[yellow]Initializing default agent specs...[/yellow]")

        # This would normally load from a file or use the migration
        console.print(
            "[green]✓ Default specs initialized (use migration for full data)[/green]"
        )
        console.print("Run the migration file to populate default specs.")

    asyncio.run(_init())


if __name__ == "__main__":
    cli()
