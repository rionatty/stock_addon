# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Route Status Report (Daily).

One row per route (cost center) per day with:
  - Cash Sales    : Sales Invoices WITHOUT payment terms (paid on the spot)
  - Credit Sales  : Sales Invoices WITH a payment terms template (on credit)
  - Collection    : customer Payment Entries, attributed to the route via the
                    cost center of the Sales Invoice(s) they pay
  - Expense       : Journal Entry debits to Expense accounts, by cost center
  - Banking       : Journal Entry debits to Bank accounts, by cost center

Both sales and expenses are tracked at the COST CENTER (route) level. Filter by
date range and (optionally) a specific route / cost center.
"""

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

	# bucket keyed by (cost_center, posting_date)
	buckets = {}

	def bucket(cost_center, posting_date):
		key = (cost_center or _("(No Route)"), getdate(posting_date))
		if key not in buckets:
			buckets[key] = {
				"cash_sales": 0.0,
				"credit_sales": 0.0,
				"collection": 0.0,
				"expense": 0.0,
				"banking": 0.0,
			}
		return buckets[key]

	for r in get_sales(filters):
		b = bucket(r.cost_center, r.posting_date)
		if r.sale_type == "credit":
			b["credit_sales"] += flt(r.amount)
		else:
			b["cash_sales"] += flt(r.amount)

	for r in get_collection(filters):
		bucket(r.cost_center, r.posting_date)["collection"] += flt(r.amount)

	for r in get_expense(filters):
		bucket(r.cost_center, r.posting_date)["expense"] += flt(r.amount)

	for r in get_banking(filters):
		bucket(r.cost_center, r.posting_date)["banking"] += flt(r.amount)

	data = []
	totals = {
		"cash_sales": 0.0,
		"credit_sales": 0.0,
		"collection": 0.0,
		"expense": 0.0,
		"banking": 0.0,
	}

	for (cost_center, posting_date) in sorted(buckets.keys(), key=lambda k: (k[1], k[0])):
		v = buckets[(cost_center, posting_date)]
		total_sales = v["cash_sales"] + v["credit_sales"]
		data.append(
			{
				"posting_date": posting_date,
				"cost_center": cost_center,
				"cash_sales": v["cash_sales"],
				"credit_sales": v["credit_sales"],
				"total_sales": total_sales,
				"collection": v["collection"],
				"expense": v["expense"],
				"banking": v["banking"],
				"currency": currency,
			}
		)
		for k in totals:
			totals[k] += v[k]

	if data:
		data.append(
			{
				"cost_center": _("Total"),
				"cash_sales": totals["cash_sales"],
				"credit_sales": totals["credit_sales"],
				"total_sales": totals["cash_sales"] + totals["credit_sales"],
				"collection": totals["collection"],
				"expense": totals["expense"],
				"banking": totals["banking"],
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
			"width": 220,
		},
		{
			"label": _("Cash Sales"),
			"fieldname": "cash_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Credit Sales"),
			"fieldname": "credit_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Total Sales"),
			"fieldname": "total_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Collection"),
			"fieldname": "collection",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Expense"),
			"fieldname": "expense",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Banking"),
			"fieldname": "banking",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def _cc_clause(filters, values, field):
	"""Optional cost-center filter on the given column."""
	if filters.cost_center:
		values["cost_center"] = filters.cost_center
		return f" AND {field} = %(cost_center)s"
	return ""


def get_sales(filters):
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	cc = _cc_clause(filters, values, "si.cost_center")
	return frappe.db.sql(
		f"""
		SELECT
			si.posting_date AS posting_date,
			si.cost_center  AS cost_center,
			si.grand_total  AS amount,
			CASE WHEN COALESCE(si.payment_terms_template, '') = '' THEN 'cash'
			     ELSE 'credit' END AS sale_type
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
		  AND si.company = %(company)s
		  AND si.is_return = 0
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {cc}
		""",
		values,
		as_dict=True,
	)


def get_collection(filters):
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	cc = _cc_clause(filters, values, "si.cost_center")
	return frappe.db.sql(
		f"""
		SELECT
			pe.posting_date AS posting_date,
			si.cost_center  AS cost_center,
			per.allocated_amount AS amount
		FROM `tabPayment Entry` pe
		JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		JOIN `tabSales Invoice` si
			ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE pe.docstatus = 1
		  AND pe.payment_type = 'Receive'
		  AND pe.party_type = 'Customer'
		  AND pe.company = %(company)s
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {cc}
		""",
		values,
		as_dict=True,
	)


def get_expense(filters):
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	cc = _cc_clause(filters, values, "jea.cost_center")
	return frappe.db.sql(
		f"""
		SELECT
			je.posting_date AS posting_date,
			jea.cost_center AS cost_center,
			(jea.debit_in_account_currency - jea.credit_in_account_currency) AS amount
		FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		JOIN `tabAccount` acc ON acc.name = jea.account
		WHERE je.docstatus = 1
		  AND je.company = %(company)s
		  AND acc.root_type = 'Expense'
		  AND je.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {cc}
		""",
		values,
		as_dict=True,
	)


def get_banking(filters):
	values = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	cc = _cc_clause(filters, values, "jea.cost_center")
	return frappe.db.sql(
		f"""
		SELECT
			je.posting_date AS posting_date,
			jea.cost_center AS cost_center,
			jea.debit_in_account_currency AS amount
		FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		JOIN `tabAccount` acc ON acc.name = jea.account
		WHERE je.docstatus = 1
		  AND je.company = %(company)s
		  AND acc.account_type = 'Bank'
		  AND jea.debit_in_account_currency > 0
		  AND je.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {cc}
		""",
		values,
		as_dict=True,
	)


@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
	"""Print-ready HTML for the report's Print PDF button (shared style)."""
	from stock_addon.stock_addon.report.report_print_utils import render_report_pdf

	return render_report_pdf("Route Status Report (Daily)", filters, columns or get_columns(), data)
