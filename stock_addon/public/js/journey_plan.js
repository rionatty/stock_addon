// Journey Plan form — loaded as a database Client Script via after_migrate.
// Source lives here for readability; client_scripts.py pushes it into the DB.

frappe.ui.form.on('Journey Plan', {
	refresh(frm) {
		if (frm.doc.docstatus !== 0) return;

		frm.add_custom_button(__('Get Customers'), function () {
			if (!frm.doc.sales_person) {
				frappe.msgprint(__('Please select a Sales Person first.'));
				return;
			}

			frappe.call({
				method: 'stock_addon.stock_addon.doctype.journey_plan.journey_plan.get_customers_for_sales_person',
				args: { sales_person: frm.doc.sales_person },
				callback(r) {
					const list = r.message || [];
					if (!list.length) {
						frappe.msgprint(__('No customers are assigned to {0}.', [frm.doc.sales_person]));
						return;
					}

					const existing = new Set((frm.doc.customers || []).map(row => row.customer));
					let added = 0;

					// Use frappe.model.add_child — does NOT trigger a grid re-render
					// per row, so adding hundreds of rows stays fast.
					list.forEach(c => {
						if (existing.has(c.customer)) return;
						const row = frappe.model.add_child(frm.doc, 'Journey Plan Customer', 'customers');
						row.customer      = c.customer;
						row.customer_name = c.customer_name;
						added++;
					});

					// Single refresh after all rows are in memory
					frm.refresh_field('customers');

					frappe.show_alert({
						message: added > 0
							? __('Added {0} customer(s). Set Visit Day and Frequency for each row.', [added])
							: __('All customers from {0} are already in the list.', [frm.doc.sales_person]),
						indicator: added > 0 ? 'green' : 'blue',
					});
				},
			});
		}, __('Actions'));
	},
});
