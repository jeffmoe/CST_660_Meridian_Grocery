"""Primary-key uniqueness: every key value must identify exactly one row."""

from __future__ import annotations

import pytest

from tests.reporting import print_violations

PRIMARY_KEYS = {
    "store_reference": ["store_code"],
    "sku_master": ["sku"],
    "planogram": ["planogram_id"],
    "daily_sku_sales": ["planogram_id", "business_date"],
}


@pytest.mark.parametrize("table", PRIMARY_KEYS)
def test_primary_key_is_unique(tables, table):
    keys = PRIMARY_KEYS[table]
    df = tables[table]
    duplicates = df[df.duplicated(keys, keep=False)].sort_values(keys)

    print_violations(f"{table} primary key ({', '.join(keys)}) unique", duplicates)
    duplicated_keys = len(duplicates[keys].drop_duplicates())
    assert duplicated_keys == 0, (
        f"{table}: {duplicated_keys} key value(s) appear on more than one row ({len(duplicates)} rows involved)"
    )
