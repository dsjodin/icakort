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
4. Docker och Docker Compose, samt BankID på telefonen. (Utan container:
   Python 3.11 eller senare.)

## Snabbstart

```bash
cp .env.example .env      # sätt ICAKORT_PASSWORD
docker compose up -d
```

Öppna `http://<servern>:8000` och logga in med användarnamnet och lösenordet
från `.env`.

Allt sköts sedan i webbläsaren:

1. **Logga in & synka** — visar en BankID-QR att skanna, och hämtar kvittona
   direkt efteråt
2. **Sätt kategori** — i tabellen över okategoriserat längst ner väljer du
   kategori i en dropdown; siffrorna uppdateras direkt
3. **Synka nu** — dyker upp så länge inloggningen lever
4. **Tolka om** — bygger om hela historiken från sparad rådata, utan ny
   BankID-signering. Behövs när normaliseringen ändrats

Kivra ger ingen refresh-token, så du signerar med BankID varje gång den gått
ut. Därför är inloggning och synk samma knapp: en signering, färsk data.

### Miljövariabler

| Variabel | Default | Betyder |
|---|---|---|
| `ICAKORT_PASSWORD` | – | Lösenord till dashboarden. **Utan detta vägrar appen lyssna på annat än localhost** |
| `ICAKORT_USER` | `icakort` | Användarnamn |
| `ICAKORT_DATA_DIR` | `./data` | Token, databas, råa kvitton, regelfil |
| `ICAKORT_HOST` / `ICAKORT_PORT` | `127.0.0.1` / `8000` | Lyssnaradress |
| `ICAKORT_REQUEST_DELAY` | `0.3` | Paus mellan Kivra-anrop, sekunder |

Volymen `icakort-data` håller allt som ska överleva en omstart. Dashboarden
saknar TLS — på ett hemnät bakom brandvägg är HTTP Basic rimligt, men ska den
någonsin nå internet hör den hemma bakom en proxy med certifikat.

## Utan container

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate
icakort serve            # http://127.0.0.1:8000, inget lösenord behövs lokalt
```

## Kommandon

Webbappen täcker det dagliga. CLI:t finns kvar för felsökning och
engångskörningar — i containern via `docker compose exec icakort icakort …`.

| Kommando | Gör |
|---|---|
| `icakort serve` | Startar webbappen |
| `icakort auth` | Loggar in med BankID, QR som ASCII i terminalen |
| `icakort sync` | Hämtar kvitton. `--max N`, `--refresh`, `--store TEXT` (default `ica`), `--all-stores` |
| `icakort reparse` | Tolkar om sparade råkvitton utan att kontakta Kivra |
| `icakort categorize` | Kör om kategoriseringen. `--unknown` listar okategoriserat |
| `icakort set-category "VARA" Kategori` | Manuell override som alltid slår reglerna |
| `icakort verify` | Stämmer av radsummor mot kvittototaler |
| `icakort stats` | Sammanfattning i terminalen. `--from --to --store --category` |

## Kategorisering

Det vanliga sättet är dropdownen i webbappen — den sätter en override för just
den varan.

Vill du hellre skriva regler ligger `categories.yaml` i datakatalogen (i
containern: volymen `icakort-data`), utlagd från paketets förlaga första
gången appen startar. En regel är en delsträng eller ett reguljärt uttryck, och
matchas mot ett normaliserat varunamn (gemener, utan mängdangivelser, så
`MJÖLK MELLAN 1,5% 1L` blir `mjölk mellan`).

En delsträng träffar vid en ordgräns i någon ände, så svenska sammansättningar
fångas åt båda hållen: `mjölk` matchar `havremjölk` och `kyckling` matchar
`kycklingfilé`, medan `ros` inte gör `kaffe mellanrost` till en blomma.
**Första träffen vinner**, så ordningen i filen är prioritetsordningen — därför
ligger Städ före Frukt & grönt, annars blir `DISKMEDEL CITRON` en citrusfrukt.

Efter en ändring i filen: klicka **Kategorisera om**, eller kör
`icakort categorize`. Kategori är ett härlett fält som skrivs om vid varje
körning, så nya regler slår igenom retroaktivt på hela historiken.

## Så hänger det ihop

```
BankID → Kivra GraphQL → data/raw/{key}.json → SQLite → kategorisering → stats → dashboard
                          ^ oföränderlig rådata          ^ körs om fritt
```

Rådatan sparas innan den tolkas. Därför kan normalisering och kategorisering
göras om hur många gånger som helst — **Tolka om** i webbappen eller
`icakort reparse` — utan att röra Kivras API igen. Det är den egenskapen som
gör en tolkningsbugg ofarlig: inget behöver hämtas en andra gång.

Kivras schema är odokumenterat, så tolkningen litar inte på ett enda fält.
Radtypen läses i tur och ordning ur `__typename`, fältet `type` och till sist
radens form — och ett kvitto som ger noll varurader trots en totalsumma
rapporteras högt i synkloggen i stället för att tyst bli noll kronor.

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
- Datakatalogen (`data/` lokalt, volymen `icakort-data` i containern)
  innehåller token, databas och råa kvitton. Den är gitignorad — committa den
  aldrig. `.env` likaså.
- Personnummer behövs inte – inloggningen sker med QR-kod.
