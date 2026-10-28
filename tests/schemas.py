"""Pandera schemas: the column, data-type, and value-domain contract per table.

Columns are declared nullable here on purpose. Required-column checks live in
test_not_null.py so each failure class is reported by exactly one test, and a
null never shows up twice as both a schema and a not-null failure.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

STORE_CODE = Check.str_matches(r"^ST\d{3}$", name="store_code_format")
SKU = Check.str_matches(r"^\d{6}$", name="sku_format")
PLANOGRAM_ID = Check.str_matches(r"^PG\d{8}$", name="planogram_id_format")
PROMO_ID = Check.str_matches(r"^PR\d{6}$", name="promo_id_format")
BOOLEAN = Check.isin(["True", "False"])
NON_NEGATIVE = Check.ge(0)

REGIONS = ["Central", "East", "North", "South", "West"]
STORE_FORMATS = ["SUPERMARKET", "NEIGHBORHOOD", "EXPRESS"]
DEPARTMENTS = ["PRODUCE", "DAIRY", "MEAT_SEAFOOD", "BAKERY", "GROCERY", "FROZEN", "HOUSEHOLD"]
UNITS_OF_MEASURE = ["EA", "LB"]
SHELF_LOCATIONS = ["AISLE", "ENDCAP", "COOLER", "FREEZER", "PRODUCE_TABLE", "BAKERY_CASE"]
PLANOGRAM_STATUSES = ["ACTIVE", "REMOVED"]


def col(dtype, *checks: Check) -> Column:
    # Every source column arrives as text; coerce=True makes the schema prove
    # each value parses as the declared type and report the ones that do not.
    return Column(dtype, checks=list(checks), coerce=dtype is not str, nullable=True)


DATE = "datetime64[ns]"
INT = "Int64"  # nullable integer, so a null is left to the not-null tests

SCHEMAS: dict[str, DataFrameSchema] = {
    "store_reference": DataFrameSchema(
        {
            "store_code": col(str, STORE_CODE),
            "store_name": col(str),
            "city": col(str),
            "state": col(str, Check.str_matches(r"^[A-Z]{2}$", name="state_format")),
            "region": col(str, Check.isin(REGIONS)),
            "store_format": col(str, Check.isin(STORE_FORMATS)),
            "opened_date": col(DATE),
            "is_active": col(str, BOOLEAN),
        },
        strict=True,
        ordered=True,
    ),
    "sku_master": DataFrameSchema(
        {
            "sku": col(str, SKU),
            "item_description": col(str),
            "brand": col(str),
            "department": col(str, Check.isin(DEPARTMENTS)),
            "category": col(str, Check.str_matches(r"^[A-Z_]+$", name="category_format")),
            "unit_of_measure": col(str, Check.isin(UNITS_OF_MEASURE)),
            "is_perishable": col(str, BOOLEAN),
            "shelf_life_days": col(INT, Check.in_range(1, 365)),
            "launch_date": col(DATE),
        },
        checks=[
            # Shelf life drives perishable-waste reporting, so it must be set
            # exactly for perishable items.
            Check(lambda df: df["shelf_life_days"].notna() == (df["is_perishable"] == "True"),
                  name="shelf_life_set_only_when_perishable"),
        ],
        strict=True,
        ordered=True,
    ),
    "planogram": DataFrameSchema(
        {
            "planogram_id": col(str, PLANOGRAM_ID),
            "store_code": col(str, STORE_CODE),
            "sku": col(str, SKU),
            "shelf_location": col(str, Check.isin(SHELF_LOCATIONS)),
            "set_date": col(DATE),
            "removal_date": col(DATE),
            "status": col(str, Check.isin(PLANOGRAM_STATUSES)),
            "regular_price": col(float, Check.in_range(0.01, 99.99)),
        },
        checks=[
            Check(lambda df: df["removal_date"].isna() | (df["removal_date"] >= df["set_date"]),
                  name="removal_date_on_or_after_set_date"),
            Check(lambda df: df["removal_date"].isna() == (df["status"] == "ACTIVE"),
                  name="removal_date_set_only_when_removed"),
        ],
        strict=True,
        ordered=True,
    ),
    "daily_sku_sales": DataFrameSchema(
        {
            "planogram_id": col(str, PLANOGRAM_ID),
            "business_date": col(DATE),
            "units_sold": col(INT, NON_NEGATIVE),
            "net_sales": col(float, NON_NEGATIVE),
            "basket_count": col(INT, NON_NEGATIVE),
            "promo_flag": col(str, BOOLEAN),
            "promo_id": col(str, PROMO_ID),
            "shrink_units": col(INT, NON_NEGATIVE),
            "waste_units": col(INT, NON_NEGATIVE),
        },
        checks=[
            # A basket holds one or more units of the SKU.
            Check(lambda df: df["basket_count"] <= df["units_sold"],
                  name="basket_count_not_above_units_sold", ignore_na=True),
            # Category managers filter on promo_flag and drill into promo_id;
            # the two must agree.
            Check(lambda df: df["promo_id"].notna() == (df["promo_flag"] == "True"),
                  name="promo_id_set_only_when_promo_flag"),
        ],
        strict=True,
        ordered=True,
    ),
}

SchemaErrors = pa.errors.SchemaErrors
