# icakort

Hämtar dina digitala ICA-kvitton från Kivra, kategoriserar varorna och visar
statistik över inköpen i en lokal dashboard.

Allt körs på din egen dator mot din egen data. Ingenting skickas vidare.

## Varför Kivra och inte ICA?

Kartläggningen av datakällor gav ett tydligt svar:

| Källa | Innehåll |
|---|---|
| `handla.api.ica.se/api/user/minbonustransaction` | Bara köp-**totaler**: butik, datum, rabatt, belopp |
| ICA-appens "Köphistorik" | Länkar vidare till Kivra |
| **Kivra** (`bff.kivra.com/graphql`) | **Varurader**: namn, antal, styckpris, rabatt, pant |

ICA lagrar alltså inte varuraderna åt dig – Kivra gör det. Därför hämtar det
här verktyget kvittona därifrån.

## Förutsättningar

1. **Kvitton måste vara aktiverat i Kivra-appen** (Kivra → Kvitton → aktivera).
2. Du måste identifiera dig som ICA-stammis i kassan för att kvittot ska
   hamna i Kivra.
3. Bara kvitton från aktiveringsdatum och framåt finns. Äldre inköp går inte
   att hämta i efterhand.
4. Python 3.11 eller senare, och BankID på telefonen.

## Installation

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
```

## Kom igång

```bash
icakort auth                  # BankID-QR i terminalen, skanna med appen
icakort sync --max 5          # provkör mot fem kvitton först
icakort verify                # stämmer varuraderna mot kvittototalerna?
icakort sync                  # hämta resten
icakort categorize --unknown  # se vad som saknar kategori, störst belopp först
icakort serve                 # dashboard på http://127.0.0.1:8000
```

## Kommandon

| Kommando | Gör |
|---|---|
| `icakort auth` | Loggar in med BankID och cachar token i `data/token.json` |
| `icakort sync` | Hämtar kvitton. `--max N`, `--refresh`, `--store TEXT` (default `ica`), `--all-stores` |
| `icakort reparse` | Tolkar om sparade råkvitton utan att kontakta Kivra |
| `icakort categorize` | Kör om kategoriseringen. `--unknown` listar okategoriserat |
| `icakort set-category "VARA" Kategori` | Manuell override som alltid slår reglerna |
| `icakort unset-category "VARA"` | Tar bort en override |
| `icakort verify` | Stämmer av radsummor mot kvittototaler |
| `icakort stats` | Sammanfattning i terminalen. `--from --to --store --category` |
| `icakort serve` | Startar dashboarden |

## Kategorisering

Reglerna ligger i [`categories.yaml`](categories.yaml). En regel är en
delsträng eller ett reguljärt uttryck, och matchas mot ett normaliserat
varunamn (gemener, utan mängd- och förpackningsangivelser, så
`MJÖLK MELLAN 1,5% 1L` blir `mjölk mellan`). **Första träffen vinner**, så
ordningen i filen är prioritetsordningen.

Arbetsflödet som faktiskt ger bra täckning:

```bash
icakort categorize --unknown        # störst belopp först
# lägg till regler i categories.yaml uppifrån
icakort categorize                  # täckningsgraden stiger
```

För enstaka varor som inte förtjänar en regel:

```bash
icakort set-category "PRYLBURK XYZ" "Städ & hushåll"
```

Kategori är ett härlett fält som skrivs om vid varje körning, så nya regler
slår igenom retroaktivt på hela historiken.

## Så hänger det ihop

```
BankID → Kivra GraphQL → data/raw/{key}.json → SQLite → kategorisering → stats → dashboard
                          ^ oföränderlig rådata          ^ körs om fritt
```

Rådatan sparas innan den tolkas. Därför kan normalisering och kategorisering
göras om hur många gånger som helst (`icakort reparse`) utan att röra Kivras
API igen.

Belopp lagras som **heltal ören** – flyttal ger drift i års- och
kategorisummor. Rabatter och returer är negativa, vilket gör att `icakort
verify` kan stämma av radsumman mot kvittots egen totalsumma. Ett kvitto som
inte går ihop är ett parsningsfel att felsöka, inte något att summera vidare på.

## Tester

```bash
pytest
```

Testerna kör mot en fixtur och behöver varken nätverk eller BankID.

## Att veta

- Kivras GraphQL-BFF är ett **inofficiellt** API utan garantier. Det kan
  ändras när som helst. All kunskap om det ligger i `src/icakort/kivra/`, så
  en ändring blir en lokal fix. Verktyget gör samma anrop som Kivras
  webbklient, mot din egen data, och pausar mellan anropen.
- `data/` är gitignorad och innehåller token, databas och råa kvitton.
  Committa den aldrig.
- Personnummer behövs inte – inloggningen sker med QR-kod.
