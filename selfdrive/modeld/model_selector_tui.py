#!/usr/bin/env python3
"""
Model Selector TUI - Terminal User Interface for model selection
Run over SSH for easy model management on C3 device
"""
import sys
import subprocess
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.text import Text
except ImportError:
    print("❌ Rich library not found. Install with: pip install rich")
    sys.exit(1)

from model_swapper import ModelSwapper


class ModelSelectorTUI:
    """Interactive TUI for model selection"""

    def __init__(self):
        self.console = Console()
        self.swapper = ModelSwapper()

    def run(self):
        """Main TUI loop"""
        while True:
            self.console.clear()
            self.show_header()
            self.show_models()
            self.show_menu()

            choice = Prompt.ask(
                "\n[bold cyan]Choose an option[/bold cyan]",
                choices=["1", "2", "3", "4", "5", "q"],
                default="q"
            )

            if choice == "1":
                self.list_models_detailed()
            elif choice == "2":
                self.swap_model_interactive()
            elif choice == "3":
                self.verify_model_interactive()
            elif choice == "4":
                self.show_active_model()
            elif choice == "5":
                self.show_help()
            elif choice.lower() == "q":
                self.console.print("\n[bold green]👋 Goodbye![/bold green]")
                break

    def show_header(self):
        """Display header"""
        header = Text()
        header.append("🚗 ", style="bold blue")
        header.append("openpilot Model Selector", style="bold white")
        header.append(" 🚀", style="bold blue")

        self.console.print(Panel(
            header,
            border_style="blue",
            expand=False
        ))
        self.console.print()

    def show_models(self):
        """Show currently active model"""
        active_model = self.swapper.get_active_model()
        models = self.swapper.list_models()

        # Find active model info
        active_info = None
        for model in models:
            if model['id'] == active_model:
                active_info = model
                break

        if active_info:
            status = f"[bold green]✓[/bold green] {active_info['name']}"
            version = f"[dim]({active_info['version']})[/dim]"
        else:
            status = f"[yellow]⚠[/yellow] {active_model}"
            version = "[dim](unknown)[/dim]"

        self.console.print(f"Active Model: {status} {version}\n")

    def show_menu(self):
        """Display menu options"""
        menu_items = [
            ("1", "List all available models"),
            ("2", "Swap to different model"),
            ("3", "Verify model integrity"),
            ("4", "Show active model details"),
            ("5", "Help & instructions"),
            ("q", "Quit"),
        ]

        for key, desc in menu_items:
            self.console.print(f"  [bold cyan]{key}[/bold cyan]) {desc}")

    def list_models_detailed(self):
        """Show detailed model list"""
        self.console.clear()
        self.show_header()

        models = self.swapper.list_models()
        active_model = self.swapper.get_active_model()

        if not models:
            self.console.print("[yellow]No models found in storage.[/yellow]")
            self.console.print("\nAdd models to: /data/models/ (or ~/driving_data/models/ locally)")
        else:
            table = Table(title=f"Available Models ({len(models)})", show_header=True)
            table.add_column("ID", style="cyan", width=25)
            table.add_column("Name", style="magenta")
            table.add_column("Version", style="green", width=12)
            table.add_column("Description", style="white")
            table.add_column("Active", justify="center", width=8)

            for model in models:
                is_active = "✓" if model['id'] == active_model else ""
                table.add_row(
                    model['id'],
                    model['name'],
                    model['version'],
                    model['description'],
                    f"[bold green]{is_active}[/bold green]" if is_active else ""
                )

            self.console.print(table)

        self.console.print("\n[dim]Press Enter to continue...[/dim]")
        input()

    def swap_model_interactive(self):
        """Interactive model swapping"""
        self.console.clear()
        self.show_header()

        models = self.swapper.list_models()
        if not models:
            self.console.print("[yellow]No models available to swap.[/yellow]")
            input("\nPress Enter to continue...")
            return

        active_model = self.swapper.get_active_model()

        # Show available models
        self.console.print("[bold]Available Models:[/bold]\n")
        for i, model in enumerate(models, 1):
            is_active = " [green](active)[/green]" if model['id'] == active_model else ""
            self.console.print(f"{i}. {model['name']}{is_active}")
            self.console.print(f"   [dim]{model['description']}[/dim]")
            self.console.print()

        # Get user choice
        choice = Prompt.ask(
            "\n[bold cyan]Select model number (or 'c' to cancel)[/bold cyan]",
            default="c"
        )

        if choice.lower() == 'c':
            return

        try:
            model_idx = int(choice) - 1
            if model_idx < 0 or model_idx >= len(models):
                self.console.print("[red]Invalid choice![/red]")
                input("\nPress Enter to continue...")
                return

            selected_model = models[model_idx]

            # Confirm swap
            if selected_model['id'] == active_model:
                self.console.print(f"\n[yellow]Model '{selected_model['name']}' is already active![/yellow]")
                input("\nPress Enter to continue...")
                return

            self.console.print(f"\n[bold]Swap to:[/bold] {selected_model['name']}")
            self.console.print(f"[dim]From:[/dim] {active_model}")

            if not Confirm.ask("\n[yellow]⚠ Openpilot will need to restart. Continue?[/yellow]"):
                return

            # Perform swap
            with self.console.status("[bold green]Swapping models...[/bold green]"):
                self.swapper.swap_model(selected_model['id'])

            self.console.print("\n[bold green]✅ Model swapped successfully![/bold green]")
            self.console.print("[yellow]⚠ Restart openpilot for changes to take effect[/yellow]")

            if Confirm.ask("\nRestart openpilot now?"):
                self.console.print("[bold]Restarting openpilot...[/bold]")
                subprocess.run(["sudo", "systemctl", "restart", "openpilot"])

        except ValueError:
            self.console.print("[red]Invalid input![/red]")
            input("\nPress Enter to continue...")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")
            input("\nPress Enter to continue...")

    def verify_model_interactive(self):
        """Interactive model verification"""
        self.console.clear()
        self.show_header()

        models = self.swapper.list_models()
        if not models:
            self.console.print("[yellow]No models to verify.[/yellow]")
            input("\nPress Enter to continue...")
            return

        # Show available models
        self.console.print("[bold]Available Models:[/bold]\n")
        for i, model in enumerate(models, 1):
            self.console.print(f"{i}. {model['name']}")

        # Get user choice
        choice = Prompt.ask(
            "\n[bold cyan]Select model number to verify (or 'c' to cancel)[/bold cyan]",
            default="c"
        )

        if choice.lower() == 'c':
            return

        try:
            model_idx = int(choice) - 1
            if model_idx < 0 or model_idx >= len(models):
                self.console.print("[red]Invalid choice![/red]")
                input("\nPress Enter to continue...")
                return

            selected_model = models[model_idx]

            # Verify model
            with self.console.status(f"[bold green]Verifying {selected_model['name']}...[/bold green]"):
                result = self.swapper.verify_model(selected_model['id'])

            if result['valid']:
                self.console.print(f"\n[bold green]✅ Model '{selected_model['name']}' is valid![/bold green]\n")

                # Show file sizes
                table = Table(title="Model Files", show_header=True)
                table.add_column("File", style="cyan")
                table.add_column("Size (MB)", style="green", justify="right")

                for filename, size in result['file_sizes'].items():
                    table.add_row(filename, f"{size / 1024 / 1024:.1f}")

                self.console.print(table)
            else:
                self.console.print(f"\n[bold red]❌ Model verification failed![/bold red]")
                self.console.print(f"[red]{result['error']}[/red]")

        except ValueError:
            self.console.print("[red]Invalid input![/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def show_active_model(self):
        """Show detailed information about active model"""
        self.console.clear()
        self.show_header()

        active_model = self.swapper.get_active_model()
        models = self.swapper.list_models()

        # Find active model info
        active_info = None
        for model in models:
            if model['id'] == active_model:
                active_info = model
                break

        if active_info:
            # Show model details
            details = [
                ("Name", active_info['name']),
                ("ID", active_info['id']),
                ("Version", active_info['version']),
                ("Description", active_info['description']),
                ("Source", active_info.get('source', 'unknown')),
            ]

            table = Table(title="Active Model Details", show_header=False)
            table.add_column("Property", style="cyan", width=15)
            table.add_column("Value", style="white")

            for prop, value in details:
                table.add_row(prop, value)

            self.console.print(table)

            # Verify model
            result = self.swapper.verify_model(active_info['id'])
            if result['valid']:
                self.console.print("\n[bold green]✓[/bold green] Model integrity verified")
            else:
                self.console.print(f"\n[bold red]✗[/bold red] {result['error']}")
        else:
            self.console.print(f"[yellow]Active model ID: {active_model}[/yellow]")
            self.console.print("[yellow]No metadata available[/yellow]")

        input("\nPress Enter to continue...")

    def show_help(self):
        """Show help and instructions"""
        self.console.clear()
        self.show_header()

        help_text = """
[bold]Model Selector Help[/bold]

[cyan]Adding New Models:[/cyan]
1. SSH to C3 device: [dim]ssh c3[/dim]
2. Create model directory: [dim]mkdir /data/models/{model_name}[/dim]
3. Upload model files (.pkl) via scp
4. Create model_info.json with model metadata
5. Run this tool to select the model

[cyan]Model Structure:[/cyan]
Each model needs 4 files:
  • driving_vision_tinygrad.pkl (vision network)
  • driving_policy_tinygrad.pkl (policy network)
  • driving_vision_metadata.pkl (vision metadata)
  • driving_policy_metadata.pkl (policy metadata)

[cyan]Creative Naming:[/cyan]
Follow openpilot's fun naming tradition! 🎉
  • "Medium Fanta 🥤", "Cool People's Model 😎"
  • "nevada model 🌵", "Space Lab 🛰️"

[cyan]Safety:[/cyan]
  • Stock model always backed up
  • Automatic backup before each swap
  • Easy rollback to any previous model

[cyan]Where to Find Models:[/cyan]
  • openpilot master: github.com/commaai/openpilot
  • FrogPilot: github.com/FrogAi/FrogPilot
  • SunnyPilot: github.com/sunnyhaibin/sunnypilot

[yellow]⚠ Remember to restart openpilot after swapping![/yellow]
        """

        self.console.print(Panel(help_text, border_style="green"))
        input("\nPress Enter to continue...")


def main():
    try:
        tui = ModelSelectorTUI()
        tui.run()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
