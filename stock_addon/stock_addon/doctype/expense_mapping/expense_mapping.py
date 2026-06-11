# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Expense Mapping.

Back-office master for Field Expense: a human-friendly expense name
(e.g. "Fuel", "Parking") mapped to the Expense (P&L) account it posts to.
Reps pick the expense name on a Field Expense line and the account
auto-populates, so the back office no longer maps accounts by hand.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ExpenseMapping(Document):
	def validate(self):
		account = frappe.db.get_value(
			"Account", self.expense_account, ["root_type", "is_group"], as_dict=True
		)
		if not account:
			return
		if account.is_group:
			frappe.throw(_("Expense Account cannot be a group account."))
		if account.root_type != "Expense":
			frappe.throw(_("Expense Account must be an Expense (P&L) account."))
