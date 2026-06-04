# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Van Banking Report.

Money banked by the vans, taken from Journal Entries that debit a Bank account
(cash collected on the route -> deposited to the bank). Shows every deposit as a
detail row, plus a Daily Total per route (cost center) and a grand total.

Filter by date range and (optionally) a specific route / cost center.
"""

from itertools import groupby

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		filters.company = frappe.defaults.get_user_default("Company")
	if not filters.from_date:
		filters.from_date = nowdate()
	if not filters.to_date:
		filters.to_date = nowdate()

	filters.from_date = getdate(filters.from_date)
	filters.to_date = getdate(filters.to_date)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency") or ""

	entries = get_banking_entries(filters)

	data = []
	grand_total = 0.0

	# entries are ordered by cost_center, posting_date — group for daily totals
	for (cost_center, posting_date), group in groupby(
		entries, key=lambda r: (r.cost_center or _("(No Route)"), getdate(r.posting_date))
	):
		day_total = 0.0
		for r in group:
			amount = flt(r.amount)
			day_total += amount
			grand_total += amount
			data.append(
				{
					"posting_date": getdate(r.posting_date),
					"cost_center": cost_center,
					"bank_account": r.bank_account,
					"reference_no": r.reference_no,
					"amount": amount,
					"voucher_no": r.voucher_no,
					"remark": r.remark,
					"currency": currency,
				}
			)

		data.append(
			{
				"cost_center": cost_center,
				"reference_no": _("Daily Total — {0}").format(posting_date),
				"amount": day_total,
				"currency": currency,
				"is_total": 1,
			}
		)

	if data:
		data.append(
			{
				"reference_no": _("Grand Total"),
				"amount": grand_total,
				"currency": currency,
				"is_total": 1,
			}
		)

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Route (Cost Center)"),
			"fieldname": "cost_center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 200,
		},
		{
			"label": _("Bank Account"),
			"fieldname": "bank_account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 220,
		},
		{"label": _("Reference No"), "fieldname": "reference_no", "fieldtype": "Data", "width": 200},
		{
			"label": _("Amount Banked"),
			"fieldname": "amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
		{
			"label": _("Journal Entry"),
			"fieldname": "voucher_no",
			"fieldtype": "Link",
			"options": "Journal Entry",
			"width": 170,
		},
		{"label": _("Remark"), "fieldname": "remark", "fieldtype": "Data", "width": 260},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def get_banking_entries(filters):
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	cc = ""
	if filters.cost_center:
		values["cost_center"] = filters.cost_center
		cc = " AND jea.cost_center = %(cost_center)s"

	return frappe.db.sql(
		f"""
		SELECT
			je.posting_date AS posting_date,
			jea.cost_center AS cost_center,
			jea.account     AS bank_account,
			jea.debit_in_account_currency AS amount,
			je.name         AS voucher_no,
			je.cheque_no    AS reference_no,
			je.user_remark  AS remark
		FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		JOIN `tabAccount` acc ON acc.name = jea.account
		WHERE je.docstatus = 1
		  AND je.company = %(company)s
		  AND acc.account_type = 'Bank'
		  AND jea.debit_in_account_currency > 0
		  AND je.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {cc}
		ORDER BY jea.cost_center ASC, je.posting_date ASC, je.name ASC
		""",
		values,
		as_dict=True,
	)
