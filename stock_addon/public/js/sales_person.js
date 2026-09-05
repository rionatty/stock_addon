// Stock Addon — the "Customers" tab on Sales Person.
//
// Lists the customers on this rep's round with what they owe, their
// customer group and their payment terms, and lets you put a customer on
// the round — existing or brand new.
//
// "On the round" means a row for this rep in the customer's Sales Team,
// which is exactly what the Sales Pro app filters on, so a customer added
// here shows up on the rep's phone.
//
// Installed as a database Client Script by client_scripts.py, so it loads
// without a bench build.

frappe.ui.form.on("Sales Person", {
	refresh(frm) {
		stock_addon_render_customers(frm);
	},
});

const SA_API = "stock_addon.stock_addon.sales_person_customers";

function stock_addon_render_customers(frm) {
	const field = frm.get_field("custom_customer_list");
	if (!field) return;                       // fixture not migrated yet
	const $wrapper = field.$wrapper.empty();

	if (frm.is_new()) {
		$wrapper.append(
			`<p class="text-muted">${__("Save this sales person first — customers are assigned to a record that exists.")}</p>`
		);
		return;
	}

	$wrapper.append(`<div class="sa-customers"><p class="text-muted">${__("Loading...")}</p></div>`);
	const $box = $wrapper.find(".sa-customers");

	frappe.call({
		method: `${SA_API}.get_customers`,
		args: { sales_person: frm.doc.name },
		callback(r) {
			stock_addon_paint(frm, $box, r.message || []);
		},
	});
}

function stock_addon_paint(frm, $box, rows) {
	const money = (v) => format_currency(v, frappe.defaults.get_default("currency"));
	const owed = rows.reduce((sum, row) => sum + (row.balance || 0), 0);
	const esc = frappe.utils.escape_html;

	const header =
		`<div class="d-flex align-items-center justify-content-between mb-3" style="gap:8px;flex-wrap:wrap">
			<div>
				<b>${rows.length}</b> ${__("customer(s)")}
				${rows.length ? ` &nbsp;·&nbsp; ${__("Outstanding")}: <b>${money(owed)}</b>` : ""}
			</div>
			<div>
				<button class="btn btn-sm btn-default sa-add-existing">${__("Assign Existing Customer")}</button>
				<button class="btn btn-sm btn-primary sa-add-new">${__("New Customer")}</button>
			</div>
		</div>`;

	let body;
	if (!rows.length) {
		body = `<p class="text-muted">${__("No customers assigned yet. Use the buttons above to put one on this round.")}</p>`;
	} else {
		const cells = rows
			.map(
				(row) => `<tr>
					<td><a href="/app/customer/${encodeURIComponent(row.name)}">${esc(row.customer_name || row.name)}</a>
						${row.disabled ? ` <span class="text-muted">(${__("disabled")})</span>` : ""}
						<div class="text-muted small">${esc(row.name)}</div></td>
					<td>${esc(row.customer_group || "")}</td>
					<td>${esc(row.payment_terms || "")}</td>
					<td class="text-right">${money(row.balance)}</td>
					<td class="text-right">
						<button class="btn btn-xs btn-default sa-remove" data-customer="${esc(row.name)}">${__("Remove")}</button>
					</td>
				</tr>`
			)
			.join("");
		body = `<div style="overflow-x:auto">
			<table class="table table-bordered" style="margin:0">
				<thead><tr>
					<th>${__("Customer")}</th>
					<th>${__("Customer Group")}</th>
					<th>${__("Payment Terms")}</th>
					<th class="text-right">${__("Outstanding")}</th>
					<th></th>
				</tr></thead>
				<tbody>${cells}</tbody>
			</table></div>`;
	}

	$box.html(header + body);

	$box.find(".sa-add-existing").on("click", () => stock_addon_assign_existing(frm));
	$box.find(".sa-add-new").on("click", () => stock_addon_create_customer(frm));
	$box.find(".sa-remove").on("click", function () {
		stock_addon_remove(frm, $(this).data("customer"));
	});
}

// ── assign an existing customer ────────────────────────────────────
function stock_addon_assign_existing(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Assign Customer to {0}", [frm.doc.name]),
		fields: [
			{
				fieldname: "customer",
				fieldtype: "Link",
				options: "Customer",
				label: __("Customer"),
				reqd: 1,
			},
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"The customer is moved onto this sales person's round. If another sales person holds them, you will be asked before they are taken off."
				)}</p>`,
			},
		],
		primary_action_label: __("Assign"),
		primary_action(values) {
			dialog.hide();
			stock_addon_assign(frm, values.customer, false);
		},
	});
	dialog.show();
}

function stock_addon_assign(frm, customer, replace) {
	frappe.call({
		method: `${SA_API}.assign_customer`,
		args: { sales_person: frm.doc.name, customer, replace: replace ? 1 : 0 },
		freeze: true,
		freeze_message: __("Assigning..."),
		callback(r) {
			const result = r.message || {};
			if (result.status === "held_by") {
				// Somebody else's customer — say whose before moving them.
				frappe.confirm(
					__("{0} is currently assigned to {1}. Move them to {2}?", [
						frappe.utils.escape_html(customer),
						frappe.utils.escape_html((result.held_by || []).join(", ")),
						frappe.utils.escape_html(frm.doc.name),
					]),
					() => stock_addon_assign(frm, customer, true)
				);
				return;
			}
			if (result.status === "unchanged") {
				frappe.show_alert({ message: __("Already on this round."), indicator: "blue" });
			} else {
				frappe.show_alert({ message: __("Customer assigned."), indicator: "green" });
			}
			stock_addon_render_customers(frm);
		},
	});
}

// ── create a customer already on this round ────────────────────────
function stock_addon_create_customer(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("New Customer for {0}", [frm.doc.name]),
		fields: [
			{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"), reqd: 1 },
			{
				fieldname: "customer_type",
				fieldtype: "Select",
				label: __("Customer Type"),
				options: "Company\nIndividual\nPartnership",
				default: "Company",
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "customer_group",
				fieldtype: "Link",
				options: "Customer Group",
				label: __("Customer Group"),
			},
			{ fieldname: "territory", fieldtype: "Link", options: "Territory", label: __("Territory") },
			{ fieldtype: "Section Break" },
			{
				fieldname: "payment_terms",
				fieldtype: "Link",
				options: "Payment Terms Template",
				label: __("Payment Terms"),
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: `${SA_API}.create_customer`,
				args: { sales_person: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Creating customer..."),
				callback(r) {
					const created = (r.message || {}).customer;
					frappe.show_alert({
						message: __("Created {0}", [frappe.utils.escape_html(created || "")]),
						indicator: "green",
					});
					stock_addon_render_customers(frm);
				},
			});
		},
	});
	dialog.show();
}

function stock_addon_remove(frm, customer) {
	frappe.confirm(
		__("Take {0} off {1}'s round? The customer is not deleted — only the assignment is removed.", [
			frappe.utils.escape_html(customer),
			frappe.utils.escape_html(frm.doc.name),
		]),
		() => {
			frappe.call({
				method: `${SA_API}.unassign_customer`,
				args: { sales_person: frm.doc.name, customer },
				freeze: true,
				freeze_message: __("Removing..."),
				callback() {
					stock_addon_render_customers(frm);
				},
			});
		}
	);
}
