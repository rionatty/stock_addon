# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Default list-view ordering.

A list's default order comes from the DocType's own sort_field/sort_order,
so it is set with Property Setters rather than list JS — that way it holds
without a bench build and survives an app update of the doctype.

Material Request defaults to newest first by `creation`, which is a
Datetime and therefore orders by date AND time in one field. Ordering by
`transaction_date` alone would only be date-accurate and would shuffle
same-day requests arbitrarily — the very thing this fixes.

To order by the business date instead, change SORT_FIELD to
"transaction_date" and re-run migrate.
"""

import frappe

LIST_SORT = {
    # doctype: (sort_field, sort_order)
    "Material Request": ("creation", "DESC"),
}


def set_default_list_sort():
    """Apply the default ordering above, idempotently."""
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    for doctype, (sort_field, sort_order) in LIST_SORT.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        meta = frappe.get_meta(doctype)
        if not meta.get_field(sort_field) and sort_field not in ("creation", "modified", "name"):
            # never point the list at a field this site does not have —
            # the list view would error instead of just ordering oddly
            frappe.log_error(
                f"{doctype} has no field '{sort_field}'; default sort left unchanged.",
                "Stock Addon: list sort",
            )
            continue

        for prop, value in (("sort_field", sort_field), ("sort_order", sort_order)):
            current = frappe.db.get_value(
                "Property Setter",
                {"doc_type": doctype, "property": prop, "field_name": ""},
                "value",
            )
            if current == value:
                continue
            make_property_setter(
                doctype, None, prop, value, "Data",
                for_doctype=True, validate_fields_for_doctype=False,
            )

    frappe.clear_cache()
