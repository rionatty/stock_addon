// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.ui.form.on("Inventory Counting", {
	refresh(frm) {
		// Copy to Inventory Posting -> Stock Reconciliation (after submit, not yet posted)
		if (frm.doc.docstatus === 1 && !frm.doc.stock_reconciliation) {
			frm.add_custom_button(__("Copy to Inventory Posting"), () => {
				frappe.call({
					method: "stock_addon.stock_addon.doctype.inventory_counting.inventory_counting.make_inventory_posting",
					args: { source_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Stock Reconciliation..."),
					callback(r) {
						if (r.message) {
							frappe.show_alert({
								message: __("Stock Reconciliation {0} created", [r.message]),
								indicator: "green",
							});
							frm.reload_doc();
							frappe.set_route("Form", "Stock Reconciliation", r.message);
						}
					},
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.stock_reconciliation) {
			frm.add_custom_button(__("Inventory Posting"), () => {
				frappe.set_route("Form", "Stock Reconciliation", frm.doc.stock_reconciliation);
			}, __("View"));
		}

		// Add Items (only while editable)
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Add Items"), () => open_add_items_dialog(frm));

			// Adjust Counted Quantities menu
			frm.add_custom_button(__("Set as Not Counted"), () => set_as_not_counted(frm),
				__("Adjust Counted Quantities"));
			frm.add_custom_button(__("Copy In-Whse Qty on Count Date"), () => copy_in_whse_qty(frm),
				__("Adjust Counted Quantities"));
		}
	},

	count_date(frm) {
		// Re-pull on-hand qty for all rows when the count date changes
		(frm.doc.items || []).forEach((row) => fetch_in_warehouse_qty(frm, row));
	},
});

frappe.ui.form.on("Inventory Counting Item", {
	item_code(frm, cdt, cdn) {
		fetch_in_warehouse_qty(frm, locals[cdt][cdn]);
	},
	warehouse(frm, cdt, cdn) {
		fetch_in_warehouse_qty(frm, locals[cdt][cdn]);
	},
	counted_qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (flt(row.counted_qty) && !row.counted) {
			frappe.model.set_value(cdt, cdn, "counted", 1);
		}
		compute_variance(cdt, cdn);
	},
	counted(frm, cdt, cdn) {
		compute_variance(cdt, cdn);
	},
});

function fetch_in_warehouse_qty(frm, row) {
	if (!row.item_code || !row.warehouse) return;
	frappe.call({
		method: "stock_addon.stock_addon.doctype.inventory_counting.inventory_counting.get_stock_as_on",
		args: {
			item_code: row.item_code,
			warehouse: row.warehouse,
			count_date: frm.doc.count_date,
		},
		callback(r) {
			if (r.message) {
				frappe.model.set_value(row.doctype, row.name, "in_warehouse_qty", r.message.qty);
				frappe.model.set_value(row.doctype, row.name, "valuation_rate", r.message.valuation_rate);
				compute_variance(row.doctype, row.name);
			}
		},
	});
}

function compute_variance(cdt, cdn) {
	const row = locals[cdt][cdn];
	const variance = row.counted ? flt(row.counted_qty) - flt(row.in_warehouse_qty) : 0;
	frappe.model.set_value(cdt, cdn, "variance", variance);
}

function set_as_not_counted(frm) {
	(frm.doc.items || []).forEach((row) => {
		frappe.model.set_value(row.doctype, row.name, "counted", 0);
		frappe.model.set_value(row.doctype, row.name, "counted_qty", 0);
		frappe.model.set_value(row.doctype, row.name, "variance", 0);
	});
	frappe.show_alert(__("All rows set as Not Counted"));
}

function copy_in_whse_qty(frm) {
	(frm.doc.items || []).forEach((row) => {
		frappe.model.set_value(row.doctype, row.name, "counted", 1);
		frappe.model.set_value(row.doctype, row.name, "counted_qty", flt(row.in_warehouse_qty));
		frappe.model.set_value(row.doctype, row.name, "variance", 0);
	});
	frappe.show_alert(__("Copied In-Whse Qty into Counted Qty"));
}

function open_add_items_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Add Items to Count"),
		fields: [
			{
				fieldtype: "Link",
				fieldname: "warehouse",
				label: __("Warehouse"),
				options: "Warehouse",
				reqd: 1,
			},
			{
				fieldtype: "Link",
				fieldname: "item_group",
				label: __("Item Group"),
				options: "Item Group",
			},
			{
				fieldtype: "Check",
				fieldname: "include_zero_qty",
				label: __("Include Items with Zero Qty"),
				default: 0,
			},
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			frappe.call({
				method: "stock_addon.stock_addon.doctype.inventory_counting.inventory_counting.get_items_for_count",
				args: {
					warehouse: values.warehouse,
					item_group: values.item_group,
					count_date: frm.doc.count_date,
					include_zero_qty: values.include_zero_qty ? 1 : 0,
				},
				freeze: true,
				callback(r) {
					const rows = r.message || [];
					if (!rows.length) {
						frappe.msgprint(__("No items found for the selected filters."));
						return;
					}
					const existing = new Set(
						(frm.doc.items || []).map((it) => `${it.item_code}::${it.warehouse}`)
					);
					let added = 0;
					rows.forEach((data) => {
						if (existing.has(`${data.item_code}::${data.warehouse}`)) return;
						const child = frm.add_child("items", data);
						existing.add(`${data.item_code}::${data.warehouse}`);
						added++;
					});
					frm.refresh_field("items");
					frappe.show_alert(__("Added {0} item(s)", [added]));
					d.hide();
				},
			});
		},
	});
	d.show();
}
