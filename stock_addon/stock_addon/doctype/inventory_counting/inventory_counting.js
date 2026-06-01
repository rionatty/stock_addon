// Copyright (c) 2026, mohtashim and contributors
// For license information, please see license.txt

frappe.ui.form.on("Inventory Counting", {
	onload(frm) {
		// only show batches that belong to the row's item
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { item: row.item_code || "" } };
		});
	},

	refresh(frm) {
		setup_variance_highlight(frm);

		// Print only the variance rows, valued at selling price
		if (!frm.is_new()) {
			frm.add_custom_button(__("Print Variance"), () => {
				const url =
					"/printview?doctype=" +
					encodeURIComponent(frm.doc.doctype) +
					"&name=" +
					encodeURIComponent(frm.doc.name) +
					"&format=" +
					encodeURIComponent("Inventory Counting Variance") +
					"&trigger_print=1&_lang=" +
					(frappe.boot.lang || "en");
				window.open(frappe.urllib.get_full_url(url));
			}, __("Print"));
		}

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
	batch_no(frm, cdt, cdn) {
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
			batch_no: row.batch_no,
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
	frappe.model.set_value(cdt, cdn, "variance", variance).then(() => {
		tint_variance_rows(cur_frm);
	});
}

// Render the Variance cell with a red badge whenever a counted row differs
// from the on-hand qty. Set as a docfield formatter so it survives grid re-renders.
function setup_variance_highlight(frm) {
	const apply = () => {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (!grid || !grid.fields_map || !grid.fields_map.variance) return;

		grid.fields_map.variance.formatter = function (value, df, options, doc) {
			const v = flt(value);
			const text = format_number(v, null, df.precision || 6);
			if (doc && doc.counted && v !== 0) {
				return `<span style="color:#fff;background-color:#e24c4c;padding:1px 6px;border-radius:3px;font-weight:600;">${text}</span>`;
			}
			return text;
		};
		grid.refresh();
		tint_variance_rows(frm);
	};

	// Apply now, then again once the (read-only, post-submit) grid finishes
	// rendering so the colour is not lost after submission.
	apply();
	setTimeout(apply, 300);
}

// Light red background on the whole row for any counted item with a variance.
function tint_variance_rows(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || !grid.grid_rows) return;
	grid.grid_rows.forEach((gr) => {
		if (!gr.row) return;
		const d = gr.doc || {};
		const has_variance = d.counted && flt(d.variance) !== 0;
		gr.row.css("background-color", has_variance ? "#fdecea" : "");
	});
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
					tint_variance_rows(frm);
					frappe.show_alert(__("Added {0} item(s)", [added]));
					d.hide();
				},
			});
		},
	});
	d.show();
}
