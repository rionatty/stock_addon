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
		frm.add_custom_button(__("Discover SAP Entities"), () => call("discover_entities_now", "Reading SAP $metadata"));

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Sync Items"), () => call("sync_items_now", "Syncing items from SAP"), __("Masters"));
			frm.add_custom_button(__("Sync Customers"), () => call("sync_customers_now", "Syncing customers from SAP"), __("Masters"));
			frm.add_custom_button(__("Sync Taxes"), () => call("sync_taxes_now", "Syncing tax codes from SAP"), __("Masters"));
			frm.add_custom_button(__("Sync Currencies"), () => call("sync_currencies_now", "Syncing currencies from SAP"), __("Masters"));
			frm.add_custom_button(__("Sync Pricing"), () => call("sync_pricing_now", "Syncing pricing from SAP"), __("Masters"));
			frm.add_custom_button(__("Assign Customer Codes"), () => {
				frappe.confirm(
					__("Customers without a SAP CardCode will be given a uniform code ({0}00001…) and <b>renamed to it</b>. Their transaction history follows the rename. Continue?", [
						frappe.utils.escape_html(frm.doc.customer_code_prefix || "C"),
					]),
					() => call("assign_customer_codes_now", "Assigning customer codes")
				);
			}, __("Masters"));
			frm.add_custom_button(__("Pull Van Transfers"), () => call("pull_transfers_now", "Pulling van transfers from SAP"), __("Transactions"));
			frm.add_custom_button(__("Pull From DocEntry"), () => {
				frappe.prompt(
					{
						fieldname: "docentry",
						label: __("SAP Transfer DocEntry"),
						fieldtype: "Int",
						reqd: 1,
						description: __("Pull starts at this transfer. Transfers already mirrored are skipped, so this cannot duplicate stock — but a very low number will scan a lot of history."),
					},
					(values) => {
						frappe.call({
							method: "stock_addon.stock_addon.doctype.sap_integration_settings.sap_integration_settings.pull_from_docentry_now",
							args: { docentry: values.docentry },
							freeze: true,
							freeze_message: __("Pulling from SAP..."),
							callback(r) {
								frappe.msgprint({
									title: __("Pull From DocEntry"),
									message: frappe.utils.escape_html(r.message || __("Done")),
									indicator: "green",
								});
								frm.reload_doc();
							},
						});
					},
					__("Pull From a Specific Transfer"),
					__("Pull")
				);
			}, __("Transactions"));
			frm.add_custom_button(__("Send Pending to SAP"), () => call("push_pending_now", "Sending pending documents to SAP"), __("Transactions"));
			frm.add_custom_button(__("Retry Failed Pushes"), () => call("retry_failed_now", "Retrying failed pushes"), __("Transactions"));
			frm.add_custom_button(__("Sync Monitor"), () => frappe.set_route("query-report", "SAP Sync Monitor"));
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
