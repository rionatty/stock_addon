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
from frappe.utils import flt

from erpnext.accounts.report.general_ledger.general_ledger import (
	execute as standard_gl_execute,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})

	# This report is a per-entry running-balance view: grouping would wrap
	# every voucher in its own Opening/Total/Closing block and reset the
	# balance, so the standard group_by is always cleared.
	filters["group_by"] = ""

	result = standard_gl_execute(filters)
	columns = list(result[0]) if result and result[0] else []
	data = result[1] if result and len(result) > 1 else []

	data = _rebuild_running_balance(data)

	_add_party_names(data)
	columns = _arrange_columns(columns)

	# Preserve any extra return values (chart, summary, etc.) from the std report.
	rest = list(result[2:]) if result and len(result) > 2 else []
	return tuple([columns, data] + rest)


def _summary_label(row):
	"""The standard GL marks Opening/Total/Closing rows by an account label
	wrapped in literal quotes (e.g. \"'Opening'\"). Return the bare label, or
	None for a normal ledger row."""
	account = (row.get("account") or "").strip().strip("'\"")
	if account in ("Opening", "Total", "Closing (Opening + Total)") and not row.get(
		"voucher_no"
	):
		return account
	return None


def _rebuild_running_balance(data):
	"""Flatten the standard GL output to: one Opening row, every ledger entry
	with a TRUE cumulative running balance, then one Closing row.

	The standard report (even ungrouped) can emit repeated Opening/Total/
	Closing blocks and blank separator rows; per-block balances reset, which
	defeats the purpose of this report.
	"""
	opening_row = None
	entries = []

	for row in data or []:
		if not isinstance(row, dict):
			continue
		label = _summary_label(row)
		if label == "Opening":
			if opening_row is None:
				opening_row = row
			continue
		if label in ("Total", "Closing (Opening + Total)"):
			continue
		if not row.get("voucher_no"):
			# blank separator rows
			continue
		entries.append(row)

	out = []
	opening_balance = 0.0
	if opening_row is not None:
		opening_row["account"] = _("Opening")
		opening_balance = flt(opening_row.get("balance")) or (
			flt(opening_row.get("debit")) - flt(opening_row.get("credit"))
		)
		opening_row["balance"] = opening_balance
		out.append(opening_row)

	running = opening_balance
	total_debit = 0.0
	total_credit = 0.0
	for row in entries:
		debit = flt(row.get("debit"))
		credit = flt(row.get("credit"))
		running += debit - credit
		total_debit += debit
		total_credit += credit
		row["balance"] = running
		out.append(row)

	if entries or opening_row is not None:
		# No "Total" row on purpose: summing a running-balance column is
		# meaningless — only the Closing row carries the real position.
		currency = (entries[0] if entries else opening_row).get("account_currency")
		out.append(
			{
				"account": _("Closing (Opening + Total)"),
				"debit": opening_balance + total_debit,
				"credit": total_credit,
				"balance": running,
				"account_currency": currency,
			}
		)

	return out


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
