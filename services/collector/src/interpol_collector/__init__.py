"""Collector service: fetches red notices from the Interpol API."""

from interpol_collector.config import CollectorSettings


def main() -> None:
    """Entry point of the collector service."""
    settings = CollectorSettings()
    print(f"Collector starting (api={settings.interpol_api_base_url}, log={settings.log_level})")
