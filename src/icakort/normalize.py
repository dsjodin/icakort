"""Normalisering av Kivras råa kvittodata till platta rader.

Två saker är medvetet strikta här:

* **Alla belopp lagras som heltal ören.** Kivra levererar formaterade
  strängar ("1 234,50 kr", ibland med hårt mellanslag och unicode-minus).
  Flyttal skulle ge drift i års- och kategorisummor.
* **Alla rader är teckenriktiga.** Rabatter och returer är negativa, så att
  summan av raderna kan stämmas av mot kvittots egen totalsumma.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Hårt mellanslag, smalt mellanslag och unicode-minus förekommer i Kivras
# formaterade belopp.
_SPACES = "   "
_MINUS = "−–—"

_MONEY_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

# "2 st x 24,90 kr", "0,412 kg x 89,00 kr/kg", "3 x 12,00"
_QUANTITY_RE = re.compile(
    r"^\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-zà-öA-ZÀ-Ö]+)?\s*[x×*]\s*(?P<price>-?[\d\s.,]+)",
    re.IGNORECASE,
)

# Mängd- och förpackningssuffix som inte hör till varans identitet.
_SIZE_TOKEN_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:kg|g|hg|ml|cl|dl|l|st|pack|förp|p)\b|"
    r"\b\d+(?:[.,]\d+)?\s*%|"
    r"\b\d+\s*-\s*p(?:ack)?\b",
    re.IGNORECASE,
)


def parse_money(value: str | int | float | None) -> int | None:
    """Tolka ett formaterat belopp som heltal ören.

    >>> parse_money("1 234,50 kr")
    123450
    >>> parse_money("−45,00 kr")
    -4500
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value * 100
    if isinstance(value, float):
        return round(value * 100)

    text = str(value)
    for space in _SPACES:
        text = text.replace(space, " ")
    for minus in _MINUS:
        text = text.replace(minus, "-")
    # Ta bort tusentalsavgränsare som mellanslag mellan siffror.
    text = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", text)

    match = _MONEY_RE.search(text)
    if not match:
        return None
    number = match.group(0).replace(",", ".")
    return round(float(number) * 100)


def parse_quantity_cost(value: str | None) -> tuple[float | None, str | None, int | None]:
    """Tolka "2 st x 24,90 kr" till (2.0, "st", 2490)."""
    if not value:
        return None, None, None
    text = str(value)
    for space in _SPACES:
        text = text.replace(space, " ")
    match = _QUANTITY_RE.match(text)
    if not match:
        return None, None, None
    qty = float(match.group("qty").replace(",", "."))
    unit = (match.group("unit") or "").lower() or None
    price = parse_money(match.group("price"))
    return qty, unit, price


def name_key(name: str | None) -> str:
    """Nyckel som kategoriregler och overrides matchar på.

    Tar bort mängd- och förpackningsangivelser så att "MJÖLK ARLA 1,5% 1L"
    och "MJÖLK ARLA 1,5%" blir samma vara.
    """
    if not name:
        return ""
    text = str(name).lower()
    for space in _SPACES:
        text = text.replace(space, " ")
    text = _SIZE_TOKEN_RE.sub(" ", text)
    text = re.sub(r"[^\wåäöéü&/+\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -/")
    return text


def _signed(amount: int | None, is_refund: object) -> int:
    """Gör beloppet negativt om raden är en retur/rabatt."""
    if amount is None:
        return 0
    if is_refund and amount > 0:
        return -amount
    return amount


@dataclass
class Item:
    line_no: int
    item_type: str
    name: str
    name_key: str
    section: str
    quantity: float | None = None
    unit: str | None = None
    unit_price_ore: int | None = None
    amount_ore: int = 0
    discount_ore: int = 0
    deposit_ore: int = 0
    identifiers: list[str] = field(default_factory=list)

    @property
    def line_total_ore(self) -> int:
        return self.amount_ore + self.discount_ore + self.deposit_ore


@dataclass
class Receipt:
    key: str
    purchase_date: str | None
    store_name: str | None
    store_id: str | None
    total_ore: int | None
    items: list[Item]
    # Kontot kvittot hämtades ur, enligt Kivras listning. Finns bara vid synk
    # -- rådatan på disk säger ingenting om vilken inkorg den kom från.
    owner_name: str | None = None

    @property
    def item_sum_ore(self) -> int:
        return sum(item.line_total_ore for item in self.items)

    @property
    def looks_unparsed(self) -> bool:
        """Totalsumma men inga varurader -- tolkningen har missat något.

        Kivras schema är odokumenterat och kan ändras. Ett kvitto utan rader
        ska synas direkt i synkloggen i stället för att tyst bli noll kronor
        i statistiken.
        """
        return not self.items and bool(self.total_ore)


# Kivras typnamn, och de kortformer fältet "type" kan tänkas använda.
# Nyckeln är gemener utan skiljetecken, så "ProductListItem", "product_list_item"
# och "product" alla landar rätt.
_TYPE_MAP = {
    "productlistitem": "product",
    "product": "product",
    "productreturnlistitem": "return",
    "productreturn": "return",
    "return": "return",
    "generaldepositlistitem": "deposit",
    "deposit": "deposit",
    "generaldiscountlistitem": "discount",
    "discount": "discount",
    "generalmodifierlistitem": "modifier",
    "modifier": "modifier",
}


def _normalize_type(value: object) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def _kind_from_shape(node: dict) -> str | None:
    """Gissa radtypen ur dess form.

    Behövs för kvitton som redan hämtats innan frågan bad om __typename --
    de ligger kvar som rådata och ska gå att tolka om utan ny hämtning.
    """
    if node.get("quantityCost") is not None or node.get("name"):
        return "return" if node.get("connectedReceipt") else "product"
    if node.get("money") is None:
        return None
    # Pant har en beskrivning, rena rabatt- och avgiftsrader har det inte.
    # Skillnaden mellan rabatt och avgift syns inte i formen, men båda är
    # justeringar med samma tecken och summerar likadant.
    return "deposit" if node.get("description") else "discount"


def _row_kind(node: dict, in_returns: bool) -> str | None:
    kind = _TYPE_MAP.get(_normalize_type(node.get("__typename")))
    if kind is None:
        kind = _TYPE_MAP.get(_normalize_type(node.get("type")))
    if kind is None:
        kind = _kind_from_shape(node)
    if kind == "product" and in_returns:
        return "return"
    return kind


def _sum_money_rows(rows: object) -> int:
    total = 0
    if not isinstance(rows, list):
        return total
    for row in rows:
        if not isinstance(row, dict):
            continue
        money = (row.get("money") or {}).get("formatted")
        total += _signed(parse_money(money), row.get("isRefund"))
    return total


def _item_from_node(node: dict, line_no: int, section: str, kind: str) -> Item | None:
    money = (node.get("money") or {}).get("formatted")
    amount = parse_money(money)
    name = node.get("name") or node.get("description") or node.get("text") or ""
    name = str(name).strip()

    if kind == "product" or kind == "return":
        qty, unit, unit_price = parse_quantity_cost((node.get("quantityCost") or {}).get("formatted"))
        item = Item(
            line_no=line_no,
            item_type=kind,
            name=name,
            name_key=name_key(name),
            section=section,
            quantity=qty,
            unit=unit,
            unit_price_ore=unit_price,
            amount_ore=_signed(amount, kind == "return"),
            discount_ore=_sum_money_rows(node.get("costModifiers")),
            deposit_ore=_sum_money_rows(node.get("deposits")),
            identifiers=[str(i) for i in (node.get("identifiers") or [])],
        )
        # En retur ska alltid dra ner summan, även om Kivra rapporterar
        # beloppet utan tecken.
        if kind == "return" and item.amount_ore > 0:
            item.amount_ore = -item.amount_ore
        return item

    if amount is None:
        return None
    return Item(
        line_no=line_no,
        item_type=kind,
        name=name,
        name_key=name_key(name),
        section=section,
        amount_ore=_signed(amount, node.get("isRefund")),
    )


def _iter_sections(items_block: object, fallback_section: str):
    """Gå igenom varuraderna i ett block.

    Kivra levererar blocket som ett enda objekt ({text, items}), men vi tar
    även emot en lista av sådana -- formen är odokumenterad och får inte
    kunna tysta bort hela kvittot igen.
    """
    groups = items_block if isinstance(items_block, list) else [items_block]
    for group in groups:
        if not isinstance(group, dict):
            continue
        section = str(group.get("text") or fallback_section)
        for node in group.get("items") or []:
            if isinstance(node, dict):
                yield section, node


def _store_id(store_information: object) -> str | None:
    """Plocka ut butiksnummer/orgnr ur storeInformation-raderna om det finns."""
    if not isinstance(store_information, dict):
        return None
    for row in store_information.get("storeInformation") or []:
        if not isinstance(row, dict):
            continue
        prop = str(row.get("property") or "").lower()
        if any(word in prop for word in ("butiksnummer", "butiks-id", "store", "orgnr", "org.nr")):
            value = row.get("value")
            if value:
                return str(value).strip()
    return None


def _total_from_payment(payment_information: object) -> int | None:
    """Kvittots egen totalsumma, som vi stämmer av radsumman mot."""
    if not isinstance(payment_information, dict):
        return None
    totals_block = payment_information.get("totals")
    rows = (totals_block or {}).get("totals") if isinstance(totals_block, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        prop = str(row.get("property") or "").lower()
        if "total" in prop or "att betala" in prop or "summa" in prop:
            amount = parse_money(row.get("value"))
            if amount is not None:
                return amount
    return None


def normalize_receipt(raw: dict, list_entry: dict | None = None) -> Receipt:
    """Gör ett rått ``receiptV2``-svar till en Receipt med platta rader."""
    content = raw.get("content") or {}
    header = content.get("header") or {}
    items_block = content.get("items") or {}

    items: list[Item] = []
    line_no = 0
    sources = (
        (items_block.get("allItems"), "Varor"),
        (items_block.get("noBonusItems"), "Utan bonus"),
        (items_block.get("returnedItems"), "Returer"),
    )
    for block, fallback in sources:
        for section, node in _iter_sections(block, fallback):
            kind = _row_kind(node, in_returns=fallback == "Returer")
            if kind is None:
                continue
            item = _item_from_node(node, line_no, section, kind)
            if item is None:
                continue
            items.append(item)
            line_no += 1

    total = _total_from_payment(content.get("paymentInformation"))
    if total is None:
        total = parse_money(header.get("totalPurchaseAmount"))
    if total is None and list_entry:
        total = parse_money((list_entry.get("totalAmount") or {}).get("formatted"))

    owner = ((list_entry or {}).get("accessInfo") or {}).get("owner") or {}
    owner_name = owner.get("name")

    purchase_date = header.get("isoDate") or (list_entry or {}).get("purchaseDate")
    store_name = ((list_entry or {}).get("store") or {}).get("name") or (
        raw.get("sender") or {}
    ).get("name")

    return Receipt(
        key=raw.get("key") or (list_entry or {}).get("key") or "",
        purchase_date=str(purchase_date)[:10] if purchase_date else None,
        store_name=str(store_name).strip() if store_name else None,
        store_id=_store_id(content.get("storeInformation")),
        total_ore=total,
        items=items,
        owner_name=str(owner_name).strip() if owner_name else None,
    )
