"""Kategoriförslagen från Claude.

Inget test rör nätverket: klienten mockas, så sviten kan köras utan nyckel
och utan kostnad.
"""

import json
from dataclasses import dataclass

import pytest

from icakort import classify

CATEGORIES = ["Mjölk & fil", "Ost", "Kaffe & te", "Städ & rengöring"]
GROUPS = {
    "Mejeri & ägg": ["Mjölk & fil", "Ost"],
    "Dryck": ["Kaffe & te"],
    "Hushåll": ["Städ & rengöring"],
}


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage


class FakeClient:
    """Svarar med det som matats in, och sparar anropen för granskning."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        names = kwargs["messages"][0]["content"].split("\n")
        assignments = [
            {"name": name, "category": self.answers.get(name, classify.UNKNOWN)}
            for name in names
            if name in self.answers
        ]
        return FakeResponse(
            content=[FakeBlock(json.dumps({"assignments": assignments}))],
            usage=FakeUsage(),
        )


def test_assignments_are_returned_and_unknowns_kept_apart():
    client = FakeClient({
        "mjölk arla": "Mjölk & fil",
        "getost": "Ost",
        "prylburk xyz": classify.UNKNOWN,
    })

    result = classify.classify_names(
        ["mjölk arla", "getost", "prylburk xyz"], CATEGORIES, GROUPS, client=client
    )

    assert result.assignments == {"mjölk arla": "Mjölk & fil", "getost": "Ost"}
    assert result.unknown == ["prylburk xyz"]


def test_an_unknown_answer_never_becomes_a_guess():
    """"Vet ej" ska lämna varan okategoriserad, inte gissas in någonstans."""
    client = FakeClient({"konstig vara": classify.UNKNOWN})
    result = classify.classify_names(["konstig vara"], CATEGORIES, GROUPS, client=client)

    assert result.assignments == {}
    assert result.unknown == ["konstig vara"]


def test_a_category_outside_the_taxonomy_is_rejected():
    """Enum i schemat ska hindra det, men koden litar inte på det ensamt."""
    client = FakeClient({"mjölk arla": "Påhittad Kategori"})
    result = classify.classify_names(["mjölk arla"], CATEGORIES, GROUPS, client=client)

    assert result.assignments == {}
    assert result.unknown == ["mjölk arla"]


def test_a_name_the_model_forgot_is_not_lost_silently():
    client = FakeClient({"getost": "Ost"})       # svarar inte om "mjölk arla"
    result = classify.classify_names(["mjölk arla", "getost"], CATEGORIES, GROUPS, client=client)

    assert result.assignments == {"getost": "Ost"}
    assert result.unknown == ["mjölk arla"]


def test_names_are_split_into_batches():
    names = [f"vara {i}" for i in range(25)]
    client = FakeClient({name: "Ost" for name in names})

    result = classify.classify_names(
        names, CATEGORIES, GROUPS, client=client, batch_size=10
    )

    assert result.batches == 3
    assert len(client.calls) == 3
    assert len(result.assignments) == 25


def test_max_batches_limits_a_trial_run():
    names = [f"vara {i}" for i in range(25)]
    client = FakeClient({name: "Ost" for name in names})

    result = classify.classify_names(
        names, CATEGORIES, GROUPS, client=client, batch_size=10, max_batches=1
    )

    assert result.batches == 1
    assert len(result.assignments) == 10


def test_the_taxonomy_is_sent_as_an_enum_and_cached():
    """Modellen ska inte kunna returnera ett kategorinamn som inte finns."""
    client = FakeClient({"getost": "Ost"})
    classify.classify_names(["getost"], CATEGORIES, GROUPS, client=client)

    call = client.calls[0]
    schema = call["output_config"]["format"]["schema"]
    enum = schema["properties"]["assignments"]["items"]["properties"]["category"]["enum"]
    assert set(enum) == set(CATEGORIES) | {classify.UNKNOWN}

    # Taxonomiprompten är identisk mellan satser och ska därför cachas.
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["effort"] == "low"


def test_cost_estimate_discounts_cached_tokens():
    result = classify.ClassifyResult(
        input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000
    )
    # En miljon färska plus en miljon cachade: 5 dollar plus 10 % av 5.
    assert result.estimated_cost_usd == pytest.approx(5.50)


def test_no_names_means_no_api_calls():
    client = FakeClient({})
    result = classify.classify_names([], CATEGORIES, GROUPS, client=client)

    assert client.calls == []
    assert result.batches == 0


def test_a_missing_key_is_reported_not_crashed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(classify.ClassifyError, match="ANTHROPIC_API_KEY"):
        classify.classify_names(["getost"], CATEGORIES, GROUPS)


def test_a_broken_response_is_reported_with_the_batch_number():
    class BrokenClient(FakeClient):
        def create(self, **kwargs):
            return FakeResponse(content=[FakeBlock("inte json")], usage=FakeUsage())

    with pytest.raises(classify.ClassifyError, match="sats 1"):
        classify.classify_names(["getost"], CATEGORIES, GROUPS, client=BrokenClient({}))
