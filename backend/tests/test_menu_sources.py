import json
from datetime import date

import pytest

from app.catalog.source_prices import menu_prices


def page(price="18.50", **offer):
    data = {"@type": "Restaurant", "hasMenu": {"hasMenuItem": [{
        "@type": "MenuItem", "name": "Lunch", "offers": {
            "@type": "Offer", "priceCurrency": "EUR", "price": price, **offer,
        },
    }]}}
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


def test_source_menu_price_is_literal_and_separate_from_travel_validity():
    rows = menu_prices(page(), "https://example.org/menu", date(2026, 10, 28))
    assert rows[0]["cost_per_person"] == 1850
    assert rows[0]["source_valid_until"] is None
    assert rows[0]["checked_on"] == "2026-10-28"


@pytest.mark.parametrize("price,offer", [
    (True, {}), ("18–25", {}), ("NaN", {}), ("18.555", {}),
    (18, {"priceCurrency": "USD"}), (18, {"priceValidUntil": "2026-10-01"}),
    (18, {"eligibleCustomerType": "Member"}), (18, {"priceSpecification": {}}),
])
def test_ambiguous_expired_foreign_and_conditional_menu_prices_are_not_promoted(price, offer):
    assert not menu_prices(page(price, **offer), "https://example.org/menu", date(2026, 10, 28))


def test_plain_prose_never_becomes_a_menu_price():
    assert not menu_prices('<p>Cheap lunch, about 10–20 EUR</p>', 'https://example.org/menu')
