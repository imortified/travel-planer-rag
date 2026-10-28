"""Read explicit schema.org menu offers; never infer a price from ranges or prose."""
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


class JsonLd(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.parts.append("".join(self.current))
            self.current = None


def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def menu_prices(html, source_url, checked_on=None):
    checked_on = checked_on or date.today()
    parser = JsonLd()
    parser.feed(html)
    rows = []
    for part in parser.parts:
        try:
            document = json.loads(part)
        except ValueError:
            continue
        for item in objects(document):
            kinds = item.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if not isinstance(kinds, list) or "MenuItem" not in kinds or not isinstance(
                item.get("name"), str
            ):
                continue
            offers = item.get("offers", [])
            offers = [offers] if isinstance(offers, dict) else offers
            if not isinstance(offers, list):
                continue
            for offer in offers:
                if not isinstance(offer, dict) or offer.get("priceCurrency") != "EUR":
                    continue
                raw = offer.get("price")
                if type(raw) not in (str, int, float) or not re.fullmatch(
                    r"\d+(?:[.,]\d{1,2})?", str(raw)
                ):
                    continue
                try:
                    price = Decimal(str(raw).replace(",", ".")) * 100
                    valid = date.fromisoformat(offer["priceValidUntil"]) if offer.get(
                        "priceValidUntil"
                    ) else None
                except (ValueError, TypeError, InvalidOperation):
                    continue
                if price > 1_000_000_000 or (valid and valid < checked_on):
                    continue
                # A conditional member/quantity price must not become an ordinary meal price.
                if any(key in offer for key in (
                    "eligibleCustomerType", "eligibleQuantity", "priceSpecification",
                    "eligibleRegion", "addOn",
                )):
                    continue
                rows.append({
                    "item_name": item["name"], "cost_per_person": int(price), "currency": "EUR",
                    "price_source": source_url, "checked_on": str(checked_on),
                    "source_valid_until": str(valid) if valid else None,
                    "evidence_kind": "schema.org/MenuItem.offers",
                })
    return rows
