# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Field Expense.

Created from the mobile app by a rep: a date, the route (sales person), the
cost center (from the app) and one or more expense lines (narration + amount).
The back office then maps an Expense Account onto each line and clicks
"Create Journal Entry", which posts the expense:

	Dr  each line's Expense Account        (amount, cost center)
	Cr  the route's cash account           (total)

The route's cash account is taken automatically from the Sales Person's
custom_cash_account (the same field the cash/banking reports use).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate


class FieldExpense(Document):
	def validate(self):
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company")
		if not self.expense_date:
			self.expense_date = nowdate()

		self.set_paid_from_account()
		self.set_line_expense_accounts()
		self.default_line_cost_centers()
		self.calculate_total()
		self.set_status()

	def set_paid_from_account(self):
		"""Auto-fill the cash account from the route's Sales Person."""
		if self.paid_from_account or not self.sales_person:
			return
		cash_account = frappe.db.get_value("Sales Person", self.sales_person, "custom_cash_account")
		if cash_account:
			self.paid_from_account = cash_account

	def set_line_expense_accounts(self):
		"""Auto-fill each line's expense account from its Expense Mapping.

		Covers lines created from the mobile app (no client script runs there).
		An account the back office set by hand is left alone.
		"""
		for row in self.expense_items:
			if row.expense_type and not row.expense_account:
				row.expense_account = frappe.db.get_value(
					"Expense Mapping", row.expense_type, "expense_account"
				)

	def default_line_cost_centers(self):
		"""Every expense line inherits the document cost center (from the app)."""
		if not self.cost_center:
			return
		for row in self.expense_items:
			if not row.cost_center:
				row.cost_center = self.cost_center

	def calculate_total(self):
		self.total_amount = sum(flt(r.amount) for r in self.expense_items)

	def set_status(self):
		if self.journal_entry:
			self.status = "Posted"
		elif self.status == "Cancelled":
			self.status = "Cancelled"
		else:
			self.status = "Draft"


@frappe.whitelist()
def make_journal_entry(source_name):
	"""Create + submit a Journal Entry that posts this Field Expense."""
	doc = frappe.get_doc("Field Expense", source_name)

	if doc.journal_entry:
		frappe.throw(
			_("This expense is already posted via Journal Entry {0}.").format(doc.journal_entry)
		)

	lines = [r for r in doc.expense_items if flt(r.amount) > 0]
	if not lines:
		frappe.throw(_("Add at least one expense line with an amount."))

	# every line must have an expense account mapped by the back office
	missing = [r.idx for r in lines if not r.expense_account]
	if missing:
		frappe.throw(
			_("Map an Expense Account on line(s): {0}.").format(", ".join(str(i) for i in missing))
		)

	if not doc.paid_from_account:
		frappe.throw(
			_(
				"No Paid From (cash) account. Set the route's cash account on the "
				"Sales Person ({0}) or fill 'Paid From Account'."
			).format(frappe.bold(doc.sales_person or "-"))
		)

	company = doc.company or frappe.defaults.get_user_default("Company")
	total = sum(flt(r.amount) for r in lines)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = doc.expense_date
	je.user_remark = doc.remarks or _("Field Expense {0}").format(doc.name)

	# Debit each expense line
	for r in lines:
		je.append(
			"accounts",
			{
				"account": r.expense_account,
				"debit_in_account_currency": flt(r.amount),
				"cost_center": r.cost_center or doc.cost_center,
				"user_remark": r.description,
			},
		)

	# Single credit to the route's cash account
	je.append(
		"accounts",
		{
			"account": doc.paid_from_account,
			"credit_in_account_currency": flt(total),
			"cost_center": doc.cost_center,
		},
	)

	je.flags.ignore_permissions = True
	je.insert()
	je.submit()

	doc.db_set("journal_entry", je.name)
	doc.db_set("status", "Posted")

	# SAP B1: mirror the expense as a Journal Voucher (guarded by
	# SAP Integration Settings; a SAP failure never blocks posting).
	from stock_addon.stock_addon.sap_integration.transactions import on_field_expense_posted
	on_field_expense_posted(doc)

	return je.name
