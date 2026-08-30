"""Kategoriförslag från Claude för varunamn som reglerna missar.

Modellen **föreslår**, databasen bestämmer. Varje okänt varunamn skickas en
gång; svaret skrivs som en override och märks med källan "llm" så det går att
granska samlat. Appen behöver aldrig API:et för att rendera en sida, och utan
nyckel fungerar allt som förut på enbart regler.

Två val som gör utfallet pålitligt:

* **Taxonomin skickas som enum i strukturerad utdata.** Modellen kan inte
  returnera ett kategorinamn som inte finns, så ingen efterhandsvalidering
  behöver gissa vad den menade.
* **"OKÄND" är ett tillåtet svar.** En vara modellen inte känner igen lämnas
  okategoriserad och hamnar i listan som betas av för hand, i stället för att
  bli en tyst gissning i statistiken.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Namnen skickas i satser. Stora satser sparar promptkostnad men gör en
# misslyckad förfrågan dyrare att göra om.
BATCH_SIZE = 80

MODEL = "claude-opus-5"
UNKNOWN = "OKÄND"

# Priser per miljon tokens för MODEL, för kostnadsuppskattningen i loggen.
INPUT_COST_PER_MTOK = 5.00
OUTPUT_COST_PER_MTOK = 25.00

SYSTEM = """Du kategoriserar varor från svenska ICA-kvitton.

Varunamnen är förkortade och versaliserade som på ett kassakvitto, ofta med
varumärke och mängd inbakade: "MJÖLK ARLA 1,5%", "KYCKLINGFILÉ 700G".

Regler:
- Välj den mest specifika kategorin som stämmer.
- Förvaringsform är inte varutyp: fryst lax är fisk, glass är glass.
- Är du osäker på vad varan är, svara OKÄND. Det är ett korrekt svar och
  bättre än en gissning -- okända varor granskas för hand efteråt.
- Svara för varje namn du får, med exakt det namn du fick."""


class ClassifyError(RuntimeError):
    """Klassificeringen kunde inte köras."""


@dataclass
class ClassifyResult:
    assignments: dict[str, str] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    batches: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        # Cachade tokens kostar ~10 % av ordinarie inpris.
        billed_input = self.input_tokens + self.cached_tokens * 0.1
        return (
            billed_input / 1_000_000 * INPUT_COST_PER_MTOK
            + self.output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
        )

    def summary(self) -> str:
        return (
            f"{len(self.assignments)} varor kategoriserade, "
            f"{len(self.unknown)} lämnade okända, {self.batches} satser, "
            f"~${self.estimated_cost_usd:.2f}"
        )


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _schema(categories: list[str]) -> dict:
    """Taxonomin som enum -- ogiltiga kategorinamn blir omöjliga."""
    return {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "assignments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "category": {
                                    "type": "string",
                                    "enum": [*categories, UNKNOWN],
                                },
                            },
                            "required": ["name", "category"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["assignments"],
                "additionalProperties": False,
            },
        },
        "effort": "low",
    }


def _taxonomy_text(groups: dict[str, list[str]]) -> str:
    lines = []
    for group, categories in groups.items():
        lines.append(f"{group}: " + ", ".join(categories))
    return "\n".join(lines)


def classify_names(
    names: list[str],
    categories: list[str],
    groups: dict[str, list[str]],
    client=None,
    batch_size: int = BATCH_SIZE,
    max_batches: int | None = None,
    progress=None,
) -> ClassifyResult:
    """Fråga Claude vilken kategori varje varunamn hör till."""
    result = ClassifyResult()
    if not names:
        return result

    if client is None:
        if not has_api_key():
            raise ClassifyError(
                "ANTHROPIC_API_KEY saknas. Lägg till den i .env och starta om "
                "containern, eller kategorisera för hand."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - beroendet finns i imagen
            raise ClassifyError("Paketet anthropic är inte installerat.") from exc
        client = anthropic.Anthropic()

    valid = set(categories)
    batches = [names[i : i + batch_size] for i in range(0, len(names), batch_size)]
    if max_batches:
        batches = batches[:max_batches]

    for number, batch in enumerate(batches, start=1):
        if progress:
            progress(f"  sats {number}/{len(batches)} ({len(batch)} varor) …")

        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=[
                {
                    "type": "text",
                    # Taxonomin är identisk mellan satser, så den cachas och
                    # kostar ~10 % från andra satsen och framåt.
                    "text": SYSTEM + "\n\nKategorier per grupp:\n" + _taxonomy_text(groups),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": "\n".join(batch)}],
            output_config=_schema(categories),
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClassifyError(f"Kunde inte tolka svaret i sats {number}: {exc}") from exc

        for row in payload.get("assignments") or []:
            name = str(row.get("name") or "")
            category = str(row.get("category") or "")
            if name not in batch:
                continue                      # modellen hittade på ett namn
            if category == UNKNOWN or category not in valid:
                result.unknown.append(name)
            else:
                result.assignments[name] = category

        # Namn modellen tappade bort ska inte tyst försvinna.
        answered = {str(r.get("name")) for r in payload.get("assignments") or []}
        result.unknown.extend(name for name in batch if name not in answered)

        usage = response.usage
        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(usage, "output_tokens", 0) or 0
        result.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        result.batches += 1

    return result
