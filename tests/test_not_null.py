"""Not-null checks on every column a downstream report depends on."""

from __future__ import annotations

import pytest

from tests.reporting import print_violations

REQUIRED_COLUMNS = {
    "store_reference": ["store_code", "store_name", "city", "state", "region", "store_format", "opened_date",
                        "is_active"],
    # shelf_life_days is legitimately null for non-perishable items.
    "sku_master": ["sku", "item_description", "brand", "department", "category", "unit_of_measure",
                   "is_perishable", "launch_date"],
    # removal_date is legitimately null while the item is on the shelf.
    "planogram": ["planogram_id", "store_code", "sku", "shelf_location", "set_date", "status", "regular_price"],
    # promo_id is legitimately null on non-promotion days.
    "daily_sku_sales": ["planogram_id", "business_date", "units_sold", "net_sales", "basket_count", "promo_flag",
                        "shrink_units", "waste_units"],
}

CASES = [(table, column) for table, columns in REQUIRED_COLUMNS.items() for column in columns]


@pytest.mark.parametrize(("table", "column"), CASES, ids=[f"{t}.{c}" for t, c in CASES])
def test_required_column_not_null(tables, table, column):
    df = tables[table]
    nulls = df[df[column].isna()]

    print_violations(f"{table}.{column} not null", nulls)
    null_count = len(nulls)
    assert null_count == 0, f"{table}.{column}: {null_count} null value(s)"
