// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.ui.form.on("SAP Integration Settings", {
	refresh(frm) {
		const call = (method, label) => {
			if (frm.is_dirty() || frm.is_new()) {
				frappe.msgprint(__("Save the settings first — these actions use the saved values."));
				return;
			}
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
					frm.reload_doc();
				},
			});
		};

		frm.add_custom_button(__("Test Connection"), () => call("test_connection", "Testing SAP connection"));

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Sync Items"), () => call("sync_items_now", "Syncing items from SAP"), __("Masters"));
			frm.add_custom_button(__("Sync Customers"), () => call("sync_customers_now", "Syncing customers from SAP"), __("Masters"));
			frm.add_custom_button(__("Pull Van Transfers"), () => call("pull_transfers_now", "Pulling van transfers from SAP"), __("Transactions"));
			frm.add_custom_button(__("Retry Failed Pushes"), () => call("retry_failed_now", "Retrying failed pushes"), __("Transactions"));
		}

		frm.dashboard.set_headline(
			frm.doc.enabled
				? __("SAP integration is <b>active</b>. Logs: {0}", [
						`<a href="/app/sap-integration-log">${__("SAP Integration Log")}</a>`,
				  ])
				: __("SAP integration is <b>off</b> — tick 'Enable SAP Integration' and save.")
		);
	},
});
