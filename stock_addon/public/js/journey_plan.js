frappe.ui.form.on('Journey Plan', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Get Customers'), function () {
				if (!frm.doc.sales_person) {
					frappe.msgprint(__('Please select a Sales Person first.'));
					return;
				}
				frappe.call({
					method: 'stock_addon.stock_addon.doctype.journey_plan.journey_plan.get_customers_for_sales_person',
					args: { sales_person: frm.doc.sales_person },
					freeze: true,
					freeze_message: __('Loading customers for {0}…', [frm.doc.sales_person]),
					callback(r) {
						const customers = r.message || [];
						if (!customers.length) {
							frappe.msgprint(__('No customers are assigned to {0}.', [frm.doc.sales_person]));
							return;
						}
						const existing = new Set(
							(frm.doc.customers || []).map(row => row.customer)
						);
						let added = 0;
						customers.forEach(c => {
							if (!existing.has(c.customer)) {
								const row = frm.add_child('customers');
								row.customer      = c.customer;
								row.customer_name = c.customer_name;
								added++;
							}
						});
						frm.refresh_field('customers');
						frappe.show_alert({
							message: added
								? __('Added {0} customer(s) — set Visit Day and Frequency for each row.', [added])
								: __('All {0} customer(s) are already in the list.', [customers.length]),
							indicator: added ? 'green' : 'blue',
						});
					},
				});
			}, __('Actions'));
		}
	},
});
