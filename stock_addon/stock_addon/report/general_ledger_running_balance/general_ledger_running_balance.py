# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""General Ledger with a true running balance.

The standard list shows a per-voucher net; here the Balance column is a
cumulative running balance that starts from the account/party OPENING balance
(everything before From Date) and then adds the period's debit/credit movements
row by row. The party (customer) name is shown as its own column placed BEFORE
the Debit and Credit columns.
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

	columns = get_columns()

	opening = get_opening_balance(filters)
	entries = get_gl_entries(filters)

	data = []
	currency = get_company_currency(filters.company)

	# Opening row
	data.append(
		{
			"posting_date": getdate(filters.from_date),
			"account": _("Opening Balance"),
			"balance": opening,
			"currency": currency,
			"is_total": 1,
		}
	)

	balance = opening
	total_debit = 0.0
	total_credit = 0.0
	party_name_cache = {}

	for e in entries:
		balance += flt(e.debit) - flt(e.credit)
		total_debit += flt(e.debit)
		total_credit += flt(e.credit)
		data.append(
			{
				"posting_date": e.posting_date,
				"account": e.account,
				"party_type": e.party_type,
				"party": e.party,
				"party_name": get_party_name(e.party_type, e.party, party_name_cache),
				"debit": flt(e.debit),
				"credit": flt(e.credit),
				"balance": balance,
				"voucher_type": e.voucher_type,
				"voucher_no": e.voucher_no,
				"against": e.against,
				"remarks": e.remarks,
				"currency": currency,
			}
		)

	# Total + closing row (period movement and the closing running balance)
	data.append(
		{
			"account": _("Total / Closing Balance"),
			"debit": total_debit,
			"credit": total_credit,
			"balance": balance,
			"currency": currency,
			"is_total": 1,
		}
	)

	return columns, data


def get_columns():
	# NOTE: Customer / Party Name is intentionally placed BEFORE Debit & Credit.
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Account"),
			"fieldname": "account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 240,
		},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 100},
		{
			"label": _("Party"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 120,
		},
		{
			"label": _("Customer / Party Name"),
			"fieldname": "party_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Debit"),
			"fieldname": "debit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Credit"),
			"fieldname": "credit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Balance"),
			"fieldname": "balance",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 180,
		},
		{"label": _("Against Account"), "fieldname": "against", "fieldtype": "Data", "width": 200},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 240},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def _common_conditions(filters, values):
	"""Conditions shared by the opening-balance and period queries."""
	conditions = ["is_cancelled = 0", "company = %(company)s"]
	values["company"] = filters.company

	if filters.account:
		conditions.append("account = %(account)s")
		values["account"] = filters.account
	if filters.party_type:
		conditions.append("party_type = %(party_type)s")
		values["party_type"] = filters.party_type
	if filters.party:
		conditions.append("party = %(party)s")
		values["party"] = filters.party
	if filters.voucher_no:
		conditions.append("voucher_no = %(voucher_no)s")
		values["voucher_no"] = filters.voucher_no
	if filters.finance_book:
		conditions.append(
			"(finance_book IS NULL OR finance_book = '' OR finance_book = %(finance_book)s)"
		)
		values["finance_book"] = filters.finance_book
	return conditions


def get_opening_balance(filters):
	"""Net (debit - credit) of everything strictly before From Date."""
	values = {}
	conditions = _common_conditions(filters, values)
	conditions.append("posting_date < %(from_date)s")
	values["from_date"] = getdate(filters.from_date)

	res = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0)
		FROM `tabGL Entry`
		WHERE {' AND '.join(conditions)}
		""",
		values,
	)
	return flt(res[0][0]) if res and res[0][0] is not None else 0.0


def get_gl_entries(filters):
	values = {}
	conditions = _common_conditions(filters, values)
	conditions.append("posting_date >= %(from_date)s")
	conditions.append("posting_date <= %(to_date)s")
	values["from_date"] = getdate(filters.from_date)
	values["to_date"] = getdate(filters.to_date)

	return frappe.db.sql(
		f"""
		SELECT
			posting_date, account, party_type, party,
			debit, credit, voucher_type, voucher_no,
			against, remarks, creation
		FROM `tabGL Entry`
		WHERE {' AND '.join(conditions)}
		ORDER BY posting_date ASC, creation ASC
		""",
		values,
		as_dict=True,
	)


def get_party_name(party_type, party, cache):
	if not (party_type and party):
		return ""
	key = (party_type, party)
	if key in cache:
		return cache[key]

	name_field = {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Employee": "employee_name",
	}.get(party_type)

	value = party
	if name_field:
		value = frappe.db.get_value(party_type, party, name_field) or party
	cache[key] = value
	return value


def get_company_currency(company):
	return frappe.get_cached_value("Company", company, "default_currency") or ""
