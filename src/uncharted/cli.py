"""Command-line entry point."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from uncharted import pipeline
from uncharted.config import Config, load_config, load_secrets
from uncharted.garmin.models import GarminApiError
from uncharted.report import period

app = typer.Typer(help="Map which streets you have run.", no_args_is_help=True)

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to config.toml.")]


def _cfg(path: Path) -> Config:
    return load_config(path)


def _log(msg: str) -> None:
    typer.echo(msg)


def _fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


@app.command("auth-check")
def auth_check(config: ConfigOpt = Path("config.toml")) -> None:
    """Exchange the Personal API key for a token and print its expiry."""
    try:
        tok = pipeline.auth_check(_cfg(config), load_secrets())
    except (GarminApiError, ValueError) as exc:
        raise _fail(str(exc)) from None
    expires = datetime.fromtimestamp(tok.expires_at, UTC).astimezone()
    typer.echo(f"OK — token expires {expires:%Y-%m-%d %H:%M %Z}")


@app.command()
def sync(
    config: ConfigOpt = Path("config.toml"),
    since: Annotated[str | None, typer.Option(help="Only activities on/after YYYY-MM-DD.")] = None,
    full: Annotated[bool, typer.Option(help="Re-download everything.")] = False,
) -> None:
    """Download running activities from Garmin Connect."""
    since_date = date.fromisoformat(since) if since else None
    try:
        r = pipeline.run_sync(_cfg(config), load_secrets(), since_date, full, _log)
    except (GarminApiError, ValueError) as exc:
        raise _fail(str(exc)) from None
    typer.echo(
        f"{r.seen} activities seen, {r.matched_type} runs, "
        f"{r.downloaded} downloaded, {r.skipped_existing} already had"
    )


@app.command()
def streets(
    config: ConfigOpt = Path("config.toml"),
    refresh: Annotated[bool, typer.Option(help="Re-download from OSM.")] = False,
) -> None:
    """Download and prepare the street network for the configured area."""
    edges = pipeline.run_streets(_cfg(config), refresh, _log)
    named = edges["name"].dropna().nunique()
    in_district = edges["district"].notna().mean()
    typer.echo(
        f"{len(edges):,} edges, {edges['length_m'].sum() / 1000:,.0f} km, "
        f"{named:,} named streets, {in_district:.0%} of edges inside a district"
    )


@app.command()
def match(
    config: ConfigOpt = Path("config.toml"),
    rebuild: Annotated[bool, typer.Option(help="Recompute coverage from all activities.")] = False,
) -> None:
    """Mark streets covered by the downloaded tracks."""
    try:
        r = pipeline.run_match(_cfg(config), rebuild, _log)
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from None
    typer.echo(
        f"processed {r.activities} activities ({r.with_gps} with GPS); "
        f"{r.covered_edges:,} / {r.total_edges:,} edges covered"
    )


@app.command()
def stats(config: ConfigOpt = Path("config.toml")) -> None:
    """Print coverage statistics and write stats.json."""
    cfg = _cfg(config)
    try:
        s = pipeline.run_stats(cfg)
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from None

    def row(name: str, d: dict) -> str:
        return (
            f"{name:<24} {d['length_coverage']:>7.1%} "
            f"{d['covered_length_m'] / 1000:>8.1f} / {d['total_length_m'] / 1000:>7.1f} km "
            f"{d['streets_completed']:>6} / {d['streets_total']:<6}"
        )

    typer.echo(f"{'':<24} {'length':>7} {'km covered':>20} {'streets':>15}")
    for name, d in sorted(s["districts"].items()):
        typer.echo(row(name, d))
    typer.echo("-" * 72)
    typer.echo(row("Total", s["overall"]))
    typer.echo(f"\nwrote {cfg.output_dir / 'stats.json'}")


@app.command()
def render(config: ConfigOpt = Path("config.toml")) -> None:
    """Render the interactive coverage map."""
    try:
        out = pipeline.run_render(_cfg(config))
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from None
    typer.echo(f"wrote {out}")


@app.command()
def report(
    config: ConfigOpt = Path("config.toml"),
    since: Annotated[
        str | None, typer.Option(help="From YYYY-MM-DD (default: last full Mon–Sun week).")
    ] = None,
    until: Annotated[
        str | None, typer.Option(help="Through YYYY-MM-DD, inclusive (default: today).")
    ] = None,
) -> None:
    """Write a report of what a week's runs added: new streets, completed streets, map."""
    start, end = period(
        date.today(),
        date.fromisoformat(since) if since else None,
        date.fromisoformat(until) if until else None,
    )
    try:
        out, text = pipeline.run_report(_cfg(config), start, end)
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from None
    typer.echo(text)
    typer.echo(f"\nwrote {out}")


@app.command("all")
def run_all(
    config: ConfigOpt = Path("config.toml"),
    since: Annotated[
        str | None, typer.Option(help="Only sync activities on/after YYYY-MM-DD.")
    ] = None,
    skip_sync: Annotated[bool, typer.Option(help="Use already-downloaded runs only.")] = False,
) -> None:
    """sync → streets → match → stats → render."""
    if not skip_sync:
        sync(config=config, since=since, full=False)
    streets(config=config, refresh=False)
    match(config=config, rebuild=False)
    stats(config=config)
    render(config=config)


if __name__ == "__main__":
    app()
