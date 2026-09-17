# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Taxes on sales and purchase documents are included in the rate.

Prices here are quoted tax-inclusive: the VAT and the excise duty on a line
are already inside the rate the customer pays, and the same goes for what
suppliers charge. A tax row left exclusive adds itself on top instead, and
the document's total comes out higher than the price list — usually noticed
only after the invoice has gone out. So every tax row is marked "Is this Tax
included in Basic Rate?":

  * as it is added, by the field's default (apply_inclusive_tax_default);
  * as it arrives from a tax template (get_taxes_and_charges below);
  * once more before the document is validated, whatever wrote it — the
    desk, the Sales Pro app or the API (include_taxes_in_rate).

Two kinds of row are left alone, because ERPNext refuses them as inclusive
(accounts_controller.validate_inclusive_tax) and saving would fail:

  * a charge of type "Actual" — a flat amount, not a rate to back out;
  * a "Valuation" charge on a purchase, which belongs to the item's cost
    rather than to its rate.
"""

import frappe
from erpnext.controllers.accounts_controller import (
	get_taxes_and_charges as erpnext_get_taxes_and_charges,
)
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint

TAX_TABLES = ("Sales Taxes and Charges", "Purchase Taxes and Charges")
FIELDNAME = "included_in_print_rate"


def can_be_inclusive(row):
	"""Whether ERPNext accepts this row as included in the rate."""
	return row.get("charge_type") != "Actual" and row.get("category") != "Valuation"


def include_taxes_in_rate(doc, method=None):
	"""before_validate on every sales and purchase document (hooks.py).

	Runs before ERPNext totals the document, so the rate the tax is backed
	out of is the one that is saved.
	"""
	for row in doc.get("taxes") or []:
		if can_be_inclusive(row) and not cint(row.get(FIELDNAME)):
			row.set(FIELDNAME, 1)


@frappe.whitelist()
def get_taxes_and_charges(master_doctype, master_name):
	"""ERPNext's own, with the template's rows already marked inclusive.

	Wired through override_whitelisted_methods, so choosing a tax template
	shows the inclusive totals straight away rather than only after saving.
	Templates written before this change still hold exclusive rows.
	"""
	rows = erpnext_get_taxes_and_charges(master_doctype, master_name) or []
	for row in rows:
		if can_be_inclusive(row):
			row[FIELDNAME] = 1
	return rows


def apply_inclusive_tax_default():
	"""after_migrate: a tax row starts inclusive, wherever it is added.

	The default is what makes a row added by hand show its inclusive total
	immediately, before the document is saved.
	"""
	for doctype in TAX_TABLES:
		if not frappe.db.exists("DocType", doctype):
			continue

		filters = {"doc_type": doctype, "field_name": FIELDNAME, "property": "default"}
		if frappe.db.get_value("Property Setter", filters, "value") == "1":
			continue

		make_property_setter(doctype, FIELDNAME, "default", "1", "Text")
