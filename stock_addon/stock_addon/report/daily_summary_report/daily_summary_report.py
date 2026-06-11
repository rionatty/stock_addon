# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Daily Summary Report.

One row per day (company-wide, optionally narrowed to one route / cost
center) with:
  - Cash Sales    : Sales Invoices without payment terms (paid on the spot)
  - Credit Sales  : Sales Invoices with a payment terms template (on credit)
  - Collection    : customer Payment Entries allocated to Sales Invoices
  - Expense       : Journal Entry debits to Expense accounts
  - Banking       : Journal Entry debits to Bank accounts

Reuses the exact data sources of Route Status Report (Daily) so the two
reports always reconcile — this one just rolls everything up per day.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from stock_addon.stock_addon.report.route_status_report_daily.route_status_report_daily import (
	get_banking,
	get_collection,
	get_expense,
	get_sales,
)


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

	# bucket keyed by posting_date
	buckets = {}

	def bucket(posting_date):
		key = getdate(posting_date)
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
		b = bucket(r.posting_date)
		if r.sale_type == "credit":
			b["credit_sales"] += flt(r.amount)
		else:
			b["cash_sales"] += flt(r.amount)

	for r in get_collection(filters):
		bucket(r.posting_date)["collection"] += flt(r.amount)

	for r in get_expense(filters):
		bucket(r.posting_date)["expense"] += flt(r.amount)

	for r in get_banking(filters):
		bucket(r.posting_date)["banking"] += flt(r.amount)

	data = []
	totals = {
		"cash_sales": 0.0,
		"credit_sales": 0.0,
		"collection": 0.0,
		"expense": 0.0,
		"banking": 0.0,
	}

	for posting_date in sorted(buckets.keys()):
		v = buckets[posting_date]
		data.append(
			{
				"posting_date": posting_date,
				"cash_sales": v["cash_sales"],
				"credit_sales": v["credit_sales"],
				"total_sales": v["cash_sales"] + v["credit_sales"],
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
				"label": _("Total"),
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

	chart = get_chart(data)
	summary = get_summary(totals, currency) if data else []

	return get_columns(), data, None, chart, summary


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{"label": "", "fieldname": "label", "fieldtype": "Data", "width": 80},
		{
			"label": _("Cash Sales"),
			"fieldname": "cash_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Credit Sales"),
			"fieldname": "credit_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Total Sales"),
			"fieldname": "total_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Collection"),
			"fieldname": "collection",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Expense"),
			"fieldname": "expense",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Banking"),
			"fieldname": "banking",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def get_summary(totals, currency):
	return [
		{
			"value": totals["cash_sales"],
			"label": _("Cash Sales"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "green",
		},
		{
			"value": totals["credit_sales"],
			"label": _("Credit Sales"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "orange",
		},
		{
			"value": totals["collection"],
			"label": _("Collection"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "blue",
		},
		{
			"value": totals["expense"],
			"label": _("Expense"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "red",
		},
		{
			"value": totals["banking"],
			"label": _("Banking"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "purple",
		},
	]


def get_chart(data):
	days = [d for d in data if not d.get("is_total")]
	if not days:
		return None
	return {
		"data": {
			"labels": [str(d["posting_date"]) for d in days],
			"datasets": [
				{"name": _("Cash Sales"), "values": [d["cash_sales"] for d in days]},
				{"name": _("Credit Sales"), "values": [d["credit_sales"] for d in days]},
				{"name": _("Collection"), "values": [d["collection"] for d in days]},
				{"name": _("Expense"), "values": [d["expense"] for d in days]},
				{"name": _("Banking"), "values": [d["banking"] for d in days]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 0, "spaceRatio": 0.4},
	}


@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
	"""Print-ready HTML for the report's Print PDF button (shared style)."""
	from stock_addon.stock_addon.report.report_print_utils import render_report_pdf

	return render_report_pdf("Daily Summary Report", filters, columns or get_columns(), data)
