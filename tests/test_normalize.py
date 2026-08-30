"""Beloppsparsning är den enda platsen där ett tyst fel blir fel årssumma."""

import pytest

from icakort.normalize import name_key, normalize_receipt, parse_money, parse_quantity_cost


@pytest.mark.parametrize(
    "text,expected",
    [
        ("123,45 kr", 12345),
        ("1 234,50 kr", 123450),
        ("1 234,50 kr", 123450),      # hårt mellanslag
        ("−45,00 kr", -4500),          # unicode-minus
        ("-45,00", -4500),
        ("0,00 kr", 0),
        ("59.90", 5990),
        ("6 kr", 600),
        (None, None),
        ("ingen siffra", None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2 st x 17,95 kr", (2.0, "st", 1795)),
        ("0,712 kg x 24,90 kr/kg", (0.712, "kg", 2490)),
        ("3 x 12,00", (3.0, None, 1200)),
        ("", (None, None, None)),
        (None, (None, None, None)),
    ],
)
def test_parse_quantity_cost(text, expected):
    assert parse_quantity_cost(text) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MJÖLK MELLAN 1,5% 1L", "mjölk mellan"),
        ("BANAN EKO", "banan eko"),
        ("COCA COLA 33CL 6-P", "coca cola"),
        ("KAFFE MELLANROST 450G", "kaffe mellanrost"),
        ("", ""),
    ],
)
def test_name_key(raw, expected):
    assert name_key(raw) == expected


def test_normalize_receipt(raw_receipt):
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])

    assert receipt.key == "rcpt-test-0001"
    assert receipt.purchase_date == "2026-03-14"
    assert receipt.store_name == "ICA Kvantum Testköping"
    assert receipt.store_id == "12345"
    assert receipt.total_ore == 17943
    assert len(receipt.items) == 6


def test_line_sum_matches_receipt_total(raw_receipt):
    """Skyddsnätet: raderna måste summera till kvittots egen total."""
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    assert receipt.item_sum_ore == receipt.total_ore


def test_discounts_and_deposits_are_signed(raw_receipt):
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    by_name = {item.name: item for item in receipt.items}

    kaffe = by_name["KAFFE MELLANROST 450G"]
    assert kaffe.amount_ore == 5990
    assert kaffe.discount_ore == -1000       # isRefund -> negativt
    assert kaffe.line_total_ore == 4990

    cola = by_name["COCA COLA 33CL 6-P"]
    assert cola.deposit_ore == 600
    assert cola.line_total_ore == 5590

    kupong = by_name["Kuponger"]
    assert kupong.item_type == "discount"
    assert kupong.amount_ore == -500


def test_quantity_and_unit_price(raw_receipt):
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    banan = next(item for item in receipt.items if item.name == "BANAN EKO")
    assert banan.quantity == 0.712
    assert banan.unit == "kg"
    assert banan.unit_price_ore == 2490
