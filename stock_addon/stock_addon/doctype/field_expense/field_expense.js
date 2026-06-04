// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.ui.form.on("Field Expense", {
	onload(frm) {
		// only let the back office pick expense (P&L) accounts for the lines
		frm.set_query("expense_account", "expense_items", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					root_type: "Expense",
				},
			};
		});
		// paid-from must be a cash/bank account
		frm.set_query("paid_from_account", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					account_type: ["in", ["Cash", "Bank"]],
				},
			};
		});
	},

	refresh(frm) {
		if (!frm.is_new() && !frm.doc.journal_entry) {
			frm.add_custom_button(__("Create Journal Entry"), () => {
				// guard: all lines need an expense account
				const missing = (frm.doc.expense_items || []).filter((r) => !r.expense_account);
				if (missing.length) {
					frappe.msgprint(
						__("Map an Expense Account on every expense line first.")
					);
					return;
				}
				frappe.call({
					method: "stock_addon.stock_addon.doctype.field_expense.field_expense.make_journal_entry",
					args: { source_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Posting expense to a Journal Entry..."),
					callback(r) {
						if (r.message) {
							frappe.show_alert({
								message: __("Journal Entry {0} created", [r.message]),
								indicator: "green",
							});
							frm.reload_doc();
							frappe.set_route("Form", "Journal Entry", r.message);
						}
					},
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.journal_entry) {
			frm.add_custom_button(
				__("Journal Entry"),
				() => frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry),
				__("View")
			);
		}
	},

	sales_person(frm) {
		// pull the route's cash account so the back office can see it before posting
		if (frm.doc.sales_person) {
			frappe.db.get_value(
				"Sales Person",
				frm.doc.sales_person,
				"custom_cash_account",
				(r) => {
					if (r && r.custom_cash_account) {
						frm.set_value("paid_from_account", r.custom_cash_account);
					}
				}
			);
		}
	},

	cost_center(frm) {
		// apply the document cost center to any line that has none
		(frm.doc.expense_items || []).forEach((row) => {
			if (!row.cost_center) {
				frappe.model.set_value(row.doctype, row.name, "cost_center", frm.doc.cost_center);
			}
		});
	},
});
