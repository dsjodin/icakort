import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_receipt() -> dict:
    return json.loads((FIXTURES / "receipt_ica.json").read_text(encoding="utf-8"))
