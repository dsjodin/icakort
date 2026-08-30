"""Kommandoradsgränssnitt för icakort."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from . import categorize as categorize_mod
from . import config, stats, store, sync as sync_mod
from .kivra.auth import AuthError, get_token
from .kivra.client import KivraClient, KivraError
from .normalize import name_key as make_name_key

app = typer.Typer(
    add_completion=False,
    help="Kategorisera och för statistik över ICA-inköp från digitala kvitton i Kivra.",
)


def kr(ore: int | float | None) -> str:
    """Formatera ören som kronor med svensk decimalkomma."""
    return f"{(ore or 0) / 100:,.2f} kr".replace(",", " ").replace(".", ",")


def _echo(message: str) -> None:
    typer.echo(message)


@app.command()
def auth() -> None:
    """Logga in mot Kivra med BankID och cachea token."""
    try:
        token = get_token(interactive=True)
    except AuthError as exc:
        typer.secho(f"Inloggning misslyckades: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    _echo(f"\nInloggad. Token sparad i {config.token_path()}")


@app.command()
def sync(
    max_receipts: int = typer.Option(0, "--max", help="Sluta efter N hämtade kvitton (0 = alla)."),
    store_filter: str = typer.Option(
        "ica", "--store", help="Butiksnamnet måste innehålla detta (skiftlägesokänsligt)."
    ),
    all_stores: bool = typer.Option(False, "--all-stores", help="Hämta kvitton från alla butiker."),
    refresh: bool = typer.Option(False, "--refresh", help="Hämta om kvitton som redan finns."),
) -> None:
    """Hämta kvitton från Kivra till den lokala databasen."""
    try:
        token = get_token(interactive=True)
    except AuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    conn = store.connect()
    needle = None if all_stores else (store_filter or None)
    try:
        with KivraClient(token) as client:
            result = sync_mod.sync(
                conn,
                client,
                store_filter=needle,
                max_receipts=max_receipts or None,
                refresh=refresh,
                progress=_echo,
            )
    except KivraError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    _echo(f"\n{result}")
    if result.fetched:
        counts = categorize_mod.recategorize(conn)
        _echo(f"Kategoriserade {counts['total']} rader ({counts.get('fallback', 0)} okända).")


@app.command()
def reparse() -> None:
    """Tolka om alla sparade råkvitton utan att kontakta Kivra."""
    conn = store.connect()
    count, unparsed = sync_mod.reparse(conn, progress=_echo)
    counts = categorize_mod.recategorize(conn)
    _echo(f"{count} kvitton omtolkade, {counts['total']} rader kategoriserade.")
    if unparsed:
        typer.secho(
            f"{unparsed} kvitton gav inga varurader trots en totalsumma. "
            "Kör `icakort verify` för att se vilka.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def categorize(
    unknown: bool = typer.Option(
        False, "--unknown", help="Visa okategoriserade varor sorterade på belopp."
    ),
    limit: int = typer.Option(40, "--limit", help="Antal rader i listan över okända varor."),
) -> None:
    """Kör om kategoriseringen med reglerna i categories.yaml."""
    conn = store.connect()
    try:
        counts = categorize_mod.recategorize(conn)
    except categorize_mod.RuleError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    cov = categorize_mod.coverage(conn)
    _echo(
        f"{counts['total']} rader: {counts.get('rule', 0)} via regel, "
        f"{counts.get('override', 0)} via override, {counts.get('type', 0)} via radtyp, "
        f"{counts.get('fallback', 0)} okända."
    )
    if cov["covered_share"] is None:
        typer.secho("Inga varurader att kategorisera.", fg=typer.colors.YELLOW)
    else:
        _echo(
            f"Täckningsgrad: {cov['covered_share']:.1%} av varukronorna "
            f"({kr(cov['unknown_ore'])} okategoriserat)."
        )

    if unknown:
        rows = categorize_mod.unknown_items(conn, limit=limit)
        if not rows:
            _echo("\nInga okategoriserade varor kvar.")
            return
        _echo("\nOkategoriserat, störst belopp först:")
        for row in rows:
            _echo(
                f"  {kr(row['total_ore']):>14}  {row['times']:>3} ggr  "
                f"{row['example_name']}   [{row['name_key']}]"
            )


@app.command("set-category")
def set_category(name: str, category: str) -> None:
    """Tvinga en vara till en kategori (slår alltid reglerna)."""
    conn = store.connect()
    key = make_name_key(name)
    store.set_override(conn, key, category)
    categorize_mod.recategorize(conn)
    _echo(f"'{key}' -> {category}")


@app.command("unset-category")
def unset_category(name: str) -> None:
    """Ta bort en manuell kategori och låt reglerna bestämma igen."""
    conn = store.connect()
    key = make_name_key(name)
    store.clear_override(conn, key)
    categorize_mod.recategorize(conn)
    _echo(f"Override borttagen för '{key}'")


@app.command()
def verify(
    tolerance: int = typer.Option(100, "--tolerance", help="Tillåten avvikelse i ören.")
) -> None:
    """Stäm av summan av varuraderna mot kvittots egen totalsumma."""
    conn = store.connect()
    rows = sync_mod.verify(conn, tolerance_ore=tolerance)
    total = conn.execute("SELECT COUNT(*) AS n FROM receipts").fetchone()["n"]
    if not rows:
        _echo(f"Alla {total} kvitton stämmer inom {tolerance} ören.")
        return
    typer.secho(f"{len(rows)} av {total} kvitton avviker:", fg=typer.colors.YELLOW)
    for row in rows:
        _echo(
            f"  {row['purchase_date']}  {row['store_name']}  "
            f"kvitto {kr(row['total_ore'])}  rader {kr(row['item_sum_ore'])}  "
            f"diff {kr(row['diff_ore'])}  [{row['key']}]"
        )


@app.command("stats")
def stats_command(
    date_from: Optional[str] = typer.Option(None, "--from", help="Från datum, YYYY-MM-DD."),
    date_to: Optional[str] = typer.Option(None, "--to", help="Till datum, YYYY-MM-DD."),
    store_name: Optional[str] = typer.Option(None, "--store", help="Exakt butiksnamn."),
    category: Optional[str] = typer.Option(None, "--category", help="Enskild kategori."),
) -> None:
    """Skriv ut en sammanfattning i terminalen."""
    conn = store.connect()
    filters = stats.Filters(date_from, date_to, store_name, category)

    head = stats.summary(conn, filters)
    if not head["receipts"]:
        _echo("Inga kvitton matchar. Kör `icakort sync` först.")
        return

    _echo(f"Period:        {head['first_date']} – {head['last_date']}")
    _echo(f"Kvitton:       {head['receipts']}")
    _echo(f"Varurader:     {head['items']}")
    _echo(f"Totalt:        {kr(head['total_ore'])}")
    _echo(f"Snitt/kvitto:  {kr(head['avg_receipt_ore'])}")

    _echo("\nPer månad:")
    for row in stats.monthly(conn, filters):
        _echo(f"  {row['month']}  {kr(row['total_ore']):>14}  ({row['receipts']} kvitton)")

    _echo("\nPer kategori:")
    for row in stats.by_category(conn, filters):
        share = row["total_ore"] / head["total_ore"] if head["total_ore"] else 0
        _echo(f"  {row['category']:<28} {kr(row['total_ore']):>14}  {share:>6.1%}")

    _echo("\nStörsta varor:")
    for row in stats.top_items(conn, filters, limit=10):
        _echo(f"  {kr(row['total_ore']):>14}  {row['times']:>3} ggr  {row['name']}")


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="Default: ICAKORT_HOST."),
    port: Optional[int] = typer.Option(None, "--port", help="Default: ICAKORT_PORT."),
) -> None:
    """Starta webbappen."""
    import uvicorn

    from .web.security import configured_password, configured_user, is_loopback

    host = host or config.web_host()
    port = port or config.web_port()

    # Dashboarden visar hela köphistoriken. Att exponera den utan lösenord
    # ska inte gå av misstag.
    if not is_loopback(host) and configured_password() is None:
        typer.secho(
            f"Vägrar lyssna på {host} utan lösenord.\n"
            "Sätt ICAKORT_PASSWORD, eller använd --host 127.0.0.1 för att bara "
            "vara nåbar lokalt.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    config.ensure_categories_file()
    protection = (
        f"lösenordsskyddad (användare: {configured_user()})"
        if configured_password()
        else "utan lösenord"
    )
    _echo(f"Dashboard: http://{host}:{port}  [{protection}]")
    uvicorn.run("icakort.web.app:app", host=host, port=port, log_level="warning")


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
