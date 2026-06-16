// Customer list view — adds Sales Person quick-filter and outstanding balance badges.
// No custom fields are created; Sales Person filters via the standard Sales Team
// child table, and balance is fetched on-demand from Sales Invoice.

frappe.listview_settings['Customer'] = {

	onload(listview) {
		// Add a Sales Person picker to the filter bar.
		// Uses the existing Sales Team child table — no custom field needed.
		listview.page.add_field({
			label: __('Sales Person'),
			fieldname: 'sales_person',
			fieldtype: 'Link',
			options: 'Sales Person',
			change() {
				const sp = this.get_value();
				// remove any prior sales-person filter before re-adding
				listview.filter_area.remove('sales_person');
				if (sp) {
					listview.filter_area.add([['Sales Team', 'sales_person', '=', sp]]);
				}
				listview.refresh();
			}
		});
	},

	refresh(listview) {
		_inject_balances(listview);
	}
};

function _inject_balances(listview) {
	const names = (listview.data || []).map(d => d.name).filter(Boolean);
	if (!names.length) return;

	frappe.call({
		method: 'stock_addon.stock_addon.doc_events.customer_balance.get_customer_balances',
		args: { customers: JSON.stringify(names) },
		callback(r) {
			const balances = r.message || {};
			names.forEach(name => {
				const bal = parseFloat(balances[name] || 0);
				const $row = $(`.list-row[data-name="${CSS.escape(name)}"]`);
				if (!$row.length) return;

				// remove stale badge from a previous render
				$row.find('.customer-balance').remove();

				if (bal <= 0) return;

				const fmt = frappe.format(bal, { fieldtype: 'Currency' });
				const pill = `<span class="customer-balance indicator-pill orange"
				                    title="${__('Outstanding Balance')}"
				                    style="margin-right:6px;font-size:11px">${fmt}</span>`;
				// inject before the modified-on timestamp on the right side
				$row.find('.level-right').prepend(pill);
			});
		}
	});
}
