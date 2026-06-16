// Customer list view — loaded as a database Client Script via after_migrate.
// Source lives here for readability; client_scripts.py pushes it into the DB.

frappe.listview_settings['Customer'] = {

	onload(listview) {
		listview.page.add_field({
			label: __('Sales Person'),
			fieldname: 'sales_person',
			fieldtype: 'Link',
			options: 'Sales Person',
			change() {
				const sp = this.get_value();
				listview.filter_area.remove('sales_person');
				if (sp) {
					listview.filter_area.add([['Sales Team', 'sales_person', '=', sp]]);
				}
				listview.refresh();
			},
		});
	},

	refresh(listview) {
		const names = (listview.data || []).map(d => d.name).filter(Boolean);
		if (!names.length) return;

		frappe.call({
			method: 'stock_addon.stock_addon.doc_events.customer_balance.get_customer_balances',
			args: { customers: JSON.stringify(names) },
			freeze: false,
			callback(r) {
				const balances = r.message || {};
				names.forEach(name => {
					const bal = parseFloat(balances[name] || 0);
					// try data-name selector; fall back to escaped-quotes selector
					const $row = $(`.list-row[data-name="${CSS.escape(name)}"]`);
					if (!$row.length) return;

					$row.find('.customer-balance').remove();
					if (bal <= 0) return;

					const fmt = frappe.format(bal, { fieldtype: 'Currency' });
					$row.find('.level-right').prepend(
						`<span class="customer-balance indicator-pill orange"
						       style="margin-right:6px;font-size:11px"
						       title="${__('Outstanding Balance')}">${fmt}</span>`
					);
				});
			},
		});
	},
};
