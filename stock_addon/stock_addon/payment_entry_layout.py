# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Payment Entry layout: Transaction ID before Taxes, and Taxes folded away.

after_migrate hook (wired in hooks.py). ERPNext places the Transaction ID
section — the cheque or reference number every receipt needs — below
Taxes and Charges, Deductions and Tax Withholding, which a cash receipt
almost never uses. So the section a cashier fills on every payment sat
under three they skip.

Both changes are Property Setters, not edits to ERPNext: the order is a
field_order setter on the doctype, and the fold is a collapsible setter on
the Taxes and Charges section.

The order is recomputed from ERPNext's own field list on every migrate
rather than stored once. A hard-coded list would freeze today's form, and
a field ERPNext adds in a later update would then land wherever Frappe
guessed. Recomputing moves one block and leaves everything else exactly
where the current ERPNext puts it. Custom fields are deliberately left out
of the list, so they keep being placed by their own insert_after.

If ERPNext ever renames either section, this does nothing and the form
simply stays as ERPNext ships it.
"""

import json

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

DOCTYPE = "Payment Entry"
TAXES_SECTION = "taxes_and_charges_section"
TRANSACTION_SECTION = "transaction_references"
BREAKS = ("Section Break", "Tab Break")


def apply_payment_entry_layout():
    if not frappe.db.exists("DocType", DOCTYPE):
        return

    # The DocType record holds ERPNext's own fields in ERPNext's own order;
    # property setters and custom fields are not part of it.
    fields = frappe.get_doc("DocType", DOCTYPE).fields
    fieldtypes = {f.fieldname: f.fieldtype for f in fields}
    standard_order = [f.fieldname for f in fields]

    if TAXES_SECTION not in fieldtypes or TRANSACTION_SECTION not in fieldtypes:
        return

    wanted = move_section_before(standard_order, fieldtypes, TRANSACTION_SECTION, TAXES_SECTION)
    _set_if_changed(None, "field_order", json.dumps(wanted), "Data", for_doctype=True)
    _set_if_changed(TAXES_SECTION, "collapsible", "1", "Check")


def move_section_before(order, fieldtypes, section, before):
    """Move `section` and the fields under it to just before `before`.

    A section's fields are everything up to the next Section or Tab Break,
    so its columns and all its inputs travel with it.
    """
    start = order.index(section)
    end = start + 1
    while end < len(order) and fieldtypes.get(order[end]) not in BREAKS:
        end += 1
    block = order[start:end]

    rest = order[:start] + order[end:]
    target = rest.index(before)
    return rest[:target] + block + rest[target:]


def _set_if_changed(fieldname, prop, value, prop_type, for_doctype=False):
    """Write a property setter only when it would change something.

    Writing one clears the doctype's cached meta, so rewriting an identical
    value on every migrate would be churn for no effect.
    """
    filters = {"doc_type": DOCTYPE, "property": prop}
    if for_doctype:
        filters["doctype_or_field"] = "DocType"
    else:
        filters.update({"doctype_or_field": "DocField", "field_name": fieldname})

    if frappe.db.get_value("Property Setter", filters, "value") == value:
        return
    make_property_setter(DOCTYPE, fieldname, prop, value, prop_type, for_doctype=for_doctype)
