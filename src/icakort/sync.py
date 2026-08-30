"""Hämtar kvitton från Kivra och lagrar dem lokalt.

Rådatan skrivs till ``data/raw/{key}.json`` *innan* den tolkas. Det gör att
normalisering och kategorisering kan göras om hur många gånger som helst
utan att röra Kivras API igen -- vilket är bra både för dem och för oss.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from . import config, store
from .kivra.client import KivraClient
from .normalize import normalize_receipt


@dataclass
class SyncResult:
    listed: int = 0
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    unparsed: int = 0

    def __str__(self) -> str:
        summary = (
            f"{self.listed} kvitton i listan, {self.fetched} hämtade, "
            f"{self.skipped} redan kända, {self.failed} misslyckades"
        )
        if self.unparsed:
            summary += (
                f"\nVARNING: {self.unparsed} kvitton gav inga varurader trots en "
                "totalsumma. Rådatan finns sparad -- kör `icakort verify` för att "
                "se vilka."
            )
        return summary


def _raw_path(key: str):
    return config.raw_dir() / f"{key}.json"


def sync(
    conn: sqlite3.Connection,
    client: KivraClient,
    store_filter: str | None = "ica",
    max_receipts: int | None = None,
    refresh: bool = False,
    progress=None,
    owner_key: str | None = None,
) -> SyncResult:
    """Synka kvitton till databasen.

    store_filter: delsträng som butiksnamnet måste innehålla (skiftlägesokänsligt).
    None hämtar alla kvitton i Kivra-inkorgen.
    refresh: hämta om kvitton som redan finns lokalt.
    owner_key: kontot synken körs med, sparas per kvitto. Måste sättas vid
    hämtning -- rådatan säger ingenting om vilken inkorg kvittot kom ur, så
    en attribution som inte skrivs ner nu går inte att få tillbaka.
    """
    result = SyncResult()
    known = store.known_receipt_keys(conn)
    needle = store_filter.lower() if store_filter else None

    for entry in client.iter_receipts():
        name = ((entry.get("store") or {}).get("name") or "").lower()
        if needle and needle not in name:
            continue
        result.listed += 1

        key = entry.get("key")
        if not key:
            continue
        if key in known and not refresh:
            result.skipped += 1
            continue

        try:
            raw = client.receipt_details(key)
        except Exception as exc:  # noqa: BLE001 - ett trasigt kvitto ska inte stoppa resten
            result.failed += 1
            if progress:
                progress(f"  ! {key}: {exc}")
            continue

        path = _raw_path(key)
        path.write_text(
            json.dumps({"list_entry": entry, "receipt": raw}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        receipt = normalize_receipt(raw, entry)
        store.save_receipt(
            conn,
            receipt,
            raw_path=str(path),
            owner_key=owner_key,
            owner_name=receipt.owner_name or owner_key,
        )
        result.fetched += 1
        if receipt.looks_unparsed:
            result.unparsed += 1
        if progress:
            marker = "!" if receipt.looks_unparsed else "+"
            note = " INGA RADER TOLKADE" if receipt.looks_unparsed else ""
            progress(
                f"  {marker} {receipt.purchase_date} {receipt.store_name} "
                f"{(receipt.total_ore or 0) / 100:.2f} kr "
                f"({len(receipt.items)} rader){note}"
            )

        if max_receipts and result.fetched >= max_receipts:
            break

    return result


def reparse(conn: sqlite3.Connection, progress=None) -> tuple[int, int]:
    """Tolka om alla sparade råfiler utan att kontakta Kivra.

    Det är den här vägen tillbaka som gör en tolkningsbugg ofarlig: rådatan
    ligger kvar, så en rättad normalisering kan appliceras på hela historiken
    utan ny BankID-signering.

    Returnerar (antal kvitton, antal utan varurader).
    """
    count = 0
    unparsed = 0
    for path in sorted(config.raw_dir().glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        receipt = normalize_receipt(payload["receipt"], payload.get("list_entry"))
        store.save_receipt(conn, receipt, raw_path=str(path))
        count += 1
        if receipt.looks_unparsed:
            unparsed += 1
        if progress and count % 50 == 0:
            progress(f"  … {count} kvitton omtolkade")
    return count, unparsed


def verify(conn: sqlite3.Connection, tolerance_ore: int = 100) -> list[sqlite3.Row]:
    """Kvitton där summan av raderna inte stämmer med kvittots totalsumma."""
    return list(
        conn.execute(
            """
            SELECT key, purchase_date, store_name, total_ore, item_sum_ore,
                   (item_sum_ore - total_ore) AS diff_ore
            FROM receipts
            WHERE total_ore IS NOT NULL
              AND ABS(item_sum_ore - total_ore) > ?
            ORDER BY ABS(item_sum_ore - total_ore) DESC
            """,
            (tolerance_ore,),
        )
    )
