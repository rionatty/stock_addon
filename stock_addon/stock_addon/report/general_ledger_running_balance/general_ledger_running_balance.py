# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""General Ledger with a running balance + a visible Customer/Party name.

This is a thin wrapper over ERPNext's STANDARD General Ledger report, so it
keeps everything the standard report already does correctly — opening balance,
running balance, against accounts, cost center, totals/closing — and only:

  * adds a "Customer / Party Name" column placed BEFORE Debit & Credit, and
  * resolves that name from the row's party, or (for cash/bank rows that carry
    no party) from the customer shown in the "against" column, and
  * makes sure the Cost Center column is present and visible.
"""

import frappe
from frappe import _

from erpnext.accounts.report.general_ledger.general_ledger import (
	execute as standard_gl_execute,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})

	# Per-entry running balance (not consolidated) unless the user chose otherwise.
	if not filters.get("group_by"):
		filters["group_by"] = ""

	result = standard_gl_execute(filters)
	columns = list(result[0]) if result and result[0] else []
	data = result[1] if result and len(result) > 1 else []

	_add_party_names(data)
	columns = _arrange_columns(columns)

	# Preserve any extra return values (chart, summary, etc.) from the std report.
	rest = list(result[2:]) if result and len(result) > 2 else []
	return tuple([columns, data] + rest)


def _add_party_names(data):
	"""Set row['party_name'] from the party, else from the 'against' customer."""
	party_cache = {}
	customer_cache = {}

	name_field = {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Employee": "employee_name",
	}

	for row in data or []:
		if not isinstance(row, dict):
			continue

		name = ""
		party_type = row.get("party_type")
		party = row.get("party")

		if party_type and party:
			key = (party_type, party)
			if key not in party_cache:
				field = name_field.get(party_type)
				party_cache[key] = (
					frappe.db.get_value(party_type, party, field) if field else None
				) or party
			name = party_cache[key]
		else:
			# cash / bank rows: the customer is usually in the "against" column
			against = (row.get("against") or "").strip()
			if against:
				first = against.split(",")[0].strip()
				if first:
					if first not in customer_cache:
						customer_cache[first] = frappe.db.get_value(
							"Customer", first, "customer_name"
						)
					name = customer_cache.get(first) or ""

		row["party_name"] = name


def _arrange_columns(columns):
	"""Insert the Party Name column before Debit and make sure Cost Center shows."""
	party_name_col = {
		"label": _("Customer / Party Name"),
		"fieldname": "party_name",
		"fieldtype": "Data",
		"width": 200,
	}

	# Don't duplicate if a rerun already added it.
	has_party_name = any(_fieldname(c) == "party_name" for c in columns)
	has_cost_center = any(_fieldname(c) == "cost_center" for c in columns)

	# Insert party name immediately before the debit column.
	if not has_party_name:
		debit_idx = next(
			(i for i, c in enumerate(columns) if _fieldname(c) == "debit"), None
		)
		if debit_idx is None:
			columns.append(party_name_col)
		else:
			columns.insert(debit_idx, party_name_col)

	# Ensure a Cost Center column exists (standard GL provides the data key).
	if not has_cost_center:
		columns.append(
			{
				"label": _("Cost Center"),
				"fieldname": "cost_center",
				"fieldtype": "Link",
				"options": "Cost Center",
				"width": 160,
			}
		)

	return columns


def _fieldname(col):
	if isinstance(col, dict):
		return col.get("fieldname")
	# standard report sometimes uses "label:fieldtype/options:width" strings
	if isinstance(col, str) and ":" in col:
		return frappe.scrub(col.split(":", 1)[0])
	return None


@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
	"""Print-ready HTML for the report's Print PDF button (shared style).

	Columns are passed from the client (they are built by the standard GL report
	at run time), so the printout matches exactly what is on screen."""
	from stock_addon.stock_addon.report.report_print_utils import render_report_pdf

	return render_report_pdf("General Ledger Running Balance", filters, columns or [], data)
