import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_receipt() -> dict:
    return json.loads((FIXTURES / "receipt_ica.json").read_text(encoding="utf-8"))


@pytest.fixture
def raw_receipt_object_shape() -> dict:
    """Kivras faktiska form: allItems är ett objekt och "type" saknas."""
    return json.loads(
        (FIXTURES / "receipt_ica_object_shape.json").read_text(encoding="utf-8")
    )
