# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Make text columns safe to convert to Currency/Float.

Some sites already had these fields before this app shipped them — created
by hand or by an early build of the Sales Pro app — as Data (varchar)
columns. Our fixtures define them as Currency/Float, so migrate issues

    ALTER TABLE `tabX` MODIFY `field` decimal(21,9) NOT NULL DEFAULT 0.0

and MariaDB aborts the whole migrate with
"(1265) Data truncated for column ... at row N" the moment one row holds
something that is not a number — an empty string is enough.

This runs pre_model_sync (before fixtures are applied), so the column is
already clean by the time the ALTER happens. Values that are genuinely
numeric are left untouched; anything else becomes 0, which is what a
Currency/Float column would have meant anyway.
"""

import frappe

# (doctype, fieldname) pairs this app ships as Currency/Float
NUMERIC_FIELDS = [
    ("Material Request", "custom_total_stock_value"),
    ("Material Request", "custom_total_qty"),
    ("Material Request Item", "custom_sales_price"),
    ("Sales Order Item", "custom_sales_price"),
    ("Stock Entry Detail", "custom_sales_price"),
    ("Stock Entry Detail", "custom_total_amount_sales_price"),
    ("Stock Entry", "custom_total_qty"),
    ("Stock Entry", "custom_total_sales_amount"),
]

# What MariaDB will accept as a number. [.] rather than an escaped dot so
# no backslash has to survive the driver and the regex engine intact.
NUMERIC_PATTERN = "^[[:space:]]*-?[0-9]+([.][0-9]+)?[[:space:]]*$"


def execute():
    for doctype, fieldname in NUMERIC_FIELDS:
        # table_exists takes the DOCTYPE — it prepends "tab" itself
        if not frappe.db.table_exists(doctype):
            continue
        table = f"tab{doctype}"
        if not _column_is_text(table, fieldname):
            continue  # already numeric (or absent) — nothing to clean

        # NULL is fine for the ALTER; only non-numeric text breaks it
        frappe.db.sql(
            f"""
            UPDATE `{table}`
            SET `{fieldname}` = '0'
            WHERE `{fieldname}` IS NOT NULL
              AND `{fieldname}` NOT REGEXP %s
            """,
            (NUMERIC_PATTERN,),
        )
        frappe.db.commit()


def _column_is_text(table, fieldname):
    row = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s", (fieldname,), as_dict=True
    )
    if not row:
        return False
    return "char" in row[0].get("Type", "").lower() or "text" in row[0].get("Type", "").lower()
