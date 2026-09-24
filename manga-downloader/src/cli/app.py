"""
Main Typer CLI application.
"""

import typer
from dataclasses import replace
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from pathlib import Path

from .display import Display
from .menus import MainMenu, ChapterSelector, SettingsMenu
from ..api.comix import ComixAPI
from ..core.downloader import MangaDownloader, cancel_downloads
from ..core.models import OutputFormat
from ..utils.config import ConfigManager
from ..utils.logger import get_logger, setup_logging

console = Console()
logger = get_logger(__name__)
app = typer.Typer(
    name="comix-downloader",
    help="🎨 Beautiful Manga Downloader CLI for comix.to",
    add_completion=False
)


class ComixDownloaderApp:
    """Main application class."""
    
    def __init__(self):
        self.config_manager = ConfigManager()
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup logging based on config."""
        config = self.config_manager.get_download_config()
        setup_logging(enable=config.enable_logs)
    
    def run(self):
        """Run the interactive CLI."""
        Display.show_banner()
        
        while True:
            try:
                action = MainMenu.show()
                
                if action == "exit":
                    console.print("\n[bold cyan]👋 Goodbye! Happy reading![/]\n")
                    break
                
                elif action == "settings":
                    SettingsMenu.show(self.config_manager)
                    self._setup_logging()  # Re-apply logging settings
                
                elif action == "download":
                    self._handle_download()
                    
            except KeyboardInterrupt:
                cancel_downloads()
                console.print("\n[yellow]Interrupted. Returning to menu...[/]")
                continue
    
    def _handle_download(self):
        """Handle manga download flow."""
        url = MainMenu.get_manga_url()
        
        if not url:
            Display.error("No URL provided")
            return
        
        if "comix.to" not in url:
            Display.error("Invalid URL. Please provide a comix.to manga URL")
            return
        
        try:
            # Extract manga code and fetch info
            with console.status("[bold cyan]Fetching manga information..."):
                manga_code = ComixAPI.extract_manga_code(url)
                manga = ComixAPI.get_manga_info(manga_code)
            
            if not manga:
                Display.error("Could not fetch manga information")
                return
            
            Display.show_manga_info(manga)
            
            # Fetch chapters
            with console.status("[bold cyan]Fetching chapters..."):
                chapters = ComixAPI.get_all_chapters(manga_code)
            
            if not chapters:
                Display.error("No chapters found")
                return
            
            display_limit = self.config_manager.get("chapters_display_limit", 20)
            Display.show_chapters_table(chapters, display_limit=display_limit)
            
            # Select chapters
            selected = ChapterSelector.select_chapters(chapters)
            
            if not selected:
                Display.info("No chapters selected")
                return
            
            # Get config and show current settings
            config = self.config_manager.get_download_config()
            Display.show_settings(config)
            
            console.print(f"\n[bold]Starting download of {len(selected)} chapter(s)...[/]\n")
            
            # Download with progress
            downloader = MangaDownloader(config)
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
                expand=True
            ) as progress:
                
                def on_complete(chapter, success, message):
                    status = "[green]✓[/]" if success else "[red]✗[/]"
                    console.print(f"  {status} {message}")
                
                successful, failed = downloader.download_chapters(
                    manga,
                    selected,
                    progress,
                    on_chapter_complete=on_complete
                )
            
            # Show summary
            Display.show_download_summary(successful, failed, manga.title)
            
            # Show download location
            download_path = Path(config.download_path) / manga.get_safe_title()
            Display.success(f"Files saved to: {download_path.absolute()}")
            
        except Exception as e:
            Display.error(f"Download failed: {str(e)}")
            raise


def main():
    """Entry point for the CLI."""
    try:
        app_instance = ComixDownloaderApp()
        app_instance.run()
    except KeyboardInterrupt:
        cancel_downloads()
        console.print("\n[bold cyan]👋 Goodbye![/]\n")
        import sys
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Fatal error: {e}[/]\n")
        logger.exception("Interactive CLI failed")
        return 1


@app.command()
def download(
    url: str = typer.Argument(None, help="Manga URL to download"),
    chapters: str = typer.Option(None, "--chapters", "-c", help="Chapter selection (e.g., '1-10', 'all')"),
    format: str = typer.Option(None, "--format", "-f", help="Output format: images, pdf, cbz"),
    output: str = typer.Option(None, "--output", "-o", help="Output directory"),
    headless: bool = typer.Option(None, "--headless/--no-headless", help="Run browser in headless mode")
):
    """Download manga directly from command line."""
    if url:
        # Direct download mode
        if format and format not in {item.value for item in OutputFormat}:
            Display.error("Invalid format. Choose one of: images, pdf, cbz")
            raise typer.Exit(1)

        config_manager = ConfigManager()
        config = config_manager.get_download_config()
        config = replace(
            config,
            output_format=OutputFormat(format) if format else config.output_format,
            download_path=output if output else config.download_path,
            headless=headless if headless is not None else config.headless,
        )
        setup_logging(enable=config.enable_logs)
        
        try:
            manga_code = ComixAPI.extract_manga_code(url)
            manga = ComixAPI.get_manga_info(manga_code, headless=config.headless)
            all_chapters = ComixAPI.get_all_chapters(manga_code, headless=config.headless)
            
            if chapters and chapters.lower() != "all":
                from .menus import ChapterSelector
                selected = ChapterSelector._parse_selection(chapters, all_chapters)
            else:
                selected = all_chapters
            
            downloader = MangaDownloader(config)
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                successful, failed = downloader.download_chapters(
                    manga, selected, progress
                )
            
            Display.show_download_summary(successful, failed, manga.title)
            
        except KeyboardInterrupt:
            cancel_downloads()
            console.print("\n[yellow]Interrupted by user.[/]")
            raise typer.Exit(0)
        except Exception as e:
            Display.error(str(e))
            raise typer.Exit(1)
    else:
        # Interactive mode
        main()


@app.command()
def settings():
    """Open settings menu."""
    config_manager = ConfigManager()
    SettingsMenu.show(config_manager)


if __name__ == "__main__":
    app()
