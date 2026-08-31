// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.query_reports["SAP Sync Monitor"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "transaction",
			label: __("Transaction"),
			fieldtype: "Select",
			options: "\nSales Invoice\nCredit Note\nVan Stock Request\nIncoming Payment\nExpense Journal",
		},
		{
			fieldname: "sap_status",
			label: __("SAP Status"),
			fieldtype: "Select",
			options: "\nSynced\nFailed\nPending",
		},
	],

	onload(report) {
		const act = (method, label) => {
			frappe.call({
				method: `stock_addon.stock_addon.doctype.sap_integration_settings.sap_integration_settings.${method}`,
				freeze: true,
				freeze_message: __(label + "..."),
				callback(r) {
					frappe.msgprint({
						title: __(label),
						message: frappe.utils.escape_html(r.message || __("Done")),
						indicator: "green",
					});
					report.refresh();
				},
			});
		};
		report.page.add_inner_button(__("Send Pending to SAP"), () =>
			act("push_pending_now", "Sending pending documents to SAP"));
		report.page.add_inner_button(__("Pull From SAP Now"), () =>
			act("pull_transfers_now", "Pulling transfers from SAP"));
		report.page.add_inner_button(__("SAP Settings"), () =>
			frappe.set_route("Form", "SAP Integration Settings"));
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "sap_status") {
			const color =
				data.sap_status === "Synced" ? "green" :
				data.sap_status === "Failed" ? "red" : "orange";
			value = `<span class="indicator-pill ${color}">${frappe.utils.escape_html(data.sap_status)}</span>`;
		}
		return value;
	},
};
