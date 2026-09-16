# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Make migrate unable to fail on data it is about to convert.

before_migrate hook (wired in hooks.py). It runs at the very start of
EVERY migrate — ahead of the pre-model-sync patches and well ahead of
fixture sync, which is where the conversion happens.

Frappe makes Currency, Float, Percent, Int and Check columns NOT NULL
(schema.py, NOT_NULL_TYPES). When this app's fixtures define a field as
Currency or Float, migrate issues

    ALTER TABLE `tabX` MODIFY `field` decimal(21,9) NOT NULL DEFAULT 0.0

and MariaDB in strict mode aborts the whole migrate with
"(1265) Data truncated for column ... at row N" if a single row cannot be
converted. Two kinds of row do that:

  * a NULL — going into a NOT NULL column is reported as truncation, the
    same error as bad text, whatever the column's current type;
  * text that is not a number — an empty string is enough, and so is a
    thousands separator.

This used to be a one-shot patch, and that was the flaw. A patch runs
once per site, so anything written into the column after that first run
— by the app, by an import, by hand — was never cleaned, and the next
migrate that touched the column failed. It also deliberately skipped
NULLs, believing them harmless, and skipped any column already numeric,
which is exactly the column a NULL breaks.

Values that are real numbers are left exactly as they are. Thousands
separators and stray whitespace are stripped rather than zeroed, so
"1,200.50" survives as 1200.50. Only what is genuinely not a number
becomes 0, which is what a Currency or Float column would have meant.
"""

import frappe

# (doctype, fieldname) pairs this app ships as Currency/Float/Int
NUMERIC_FIELDS = [
    ("Material Request", "custom_total_stock_value"),
    ("Material Request", "custom_total_qty"),
    ("Material Request Item", "custom_sales_price"),
    ("Sales Order Item", "custom_sales_price"),
    ("Stock Entry Detail", "custom_sales_price"),
    ("Stock Entry Detail", "custom_total_amount_sales_price"),
    ("Stock Entry", "custom_total_qty"),
    ("Stock Entry", "custom_total_sales_amount"),
    ("Sales Invoice", "custom_location_distance"),
    ("Sales Order", "custom_location_distance"),
    ("Payment Entry", "custom_location_distance"),
]

# A number MariaDB will convert without complaint. No surrounding
# whitespace: that is trimmed first, because leaving it to the conversion
# is itself a truncation.
STRICT_NUMBER = "^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)$"


def coerce_numeric_custom_fields():
    cleaned = []
    for doctype, fieldname in NUMERIC_FIELDS:
        column_type = _column_type(doctype, fieldname)
        if column_type is None:
            continue                            # table or column not there yet

        table = f"`tab{doctype}`"
        field = f"`{fieldname}`"
        is_text = "char" in column_type or "text" in column_type

        # NULLs break a NOT NULL conversion whatever the column type is now.
        nulls = _count(f"SELECT COUNT(*) FROM {table} WHERE {field} IS NULL")
        if nulls:
            # 0 converts to '0' in a text column, so one statement serves both
            frappe.db.sql(f"UPDATE {table} SET {field} = 0 WHERE {field} IS NULL")

        tidied = junk = 0
        if is_text:
            # Keep "1,200.50" and " 42 " as the numbers they are.
            tidied = _count(
                f"SELECT COUNT(*) FROM {table} WHERE {field} LIKE %s "
                f"OR {field} REGEXP %s",
                ("%,%", "^[[:space:]]|[[:space:]]$"),
            )
            if tidied:
                frappe.db.sql(
                    f"UPDATE {table} SET {field} = TRIM(REPLACE({field}, ',', '')) "
                    f"WHERE {field} LIKE %s OR {field} REGEXP %s",
                    ("%,%", "^[[:space:]]|[[:space:]]$"),
                )
            # Whatever is left and still not a number really is not one.
            junk = _count(f"SELECT COUNT(*) FROM {table} WHERE {field} NOT REGEXP %s",
                          (STRICT_NUMBER,))
            if junk:
                frappe.db.sql(f"UPDATE {table} SET {field} = '0' WHERE {field} NOT REGEXP %s",
                              (STRICT_NUMBER,))

        if nulls or tidied or junk:
            cleaned.append(f"{doctype}.{fieldname}: {nulls} empty -> 0, "
                           f"{tidied} tidied, {junk} non-numeric -> 0")

    if cleaned:
        frappe.db.commit()
        # Said once, when something actually changed — a migrate that finds
        # everything clean is silent.
        frappe.log_error("\n".join(cleaned), "Stock Addon: numeric fields cleaned before migrate")


def _column_type(doctype, fieldname):
    """The column's SQL type, lower-cased, or None when it does not exist."""
    if not frappe.db.table_exists(doctype):
        return None
    row = frappe.db.sql(f"SHOW COLUMNS FROM `tab{doctype}` LIKE %s", (fieldname,), as_dict=True)
    return (row[0].get("Type") or "").lower() if row else None


def _count(query, values=None):
    # Omit values entirely when there are none. frappe.db.sql treats only its
    # own EmptyQueryValues sentinel as "no parameters"; an explicit None is
    # wrapped into (None,), and MySQLdb then refuses to substitute it into a
    # query with no placeholders.
    if values is None:
        return frappe.db.sql(query)[0][0]
    return frappe.db.sql(query, values)[0][0]
