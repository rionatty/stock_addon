// Stock Addon — the "Customers" tab on Sales Person.
//
// Lists the customers on this rep's round: what each owes, their customer
// group, their payment terms and their contribution percentage — and lets
// you put a customer on the round, existing or newly created.
//
// "On the round" is the customer's Sales Team table, the same thing the
// Sales Pro app filters on, so this tab and the rep's phone read one
// fact. Each row links straight to that table on the customer.
//
// Everything is wrapped in an IIFE on purpose: Frappe concatenates EVERY
// Form Client Script for a doctype into one string and runs the lot
// through a single new Function(), so a bare top-level declaration here
// shares scope with every other app's script on this doctype — one name
// collision and the whole blob fails to parse, taking this tab with it.
//
// Installed as a database Client Script by client_scripts.py, so it needs
// no bench build.

(function () {
	if (typeof frappe === "undefined") return;

	const API = "stock_addon.stock_addon.sales_person_customers";
	const FIELD = "custom_customer_list";

	function box(frm) {
		const field = frm.get_field(FIELD) || (frm.fields_dict || {})[FIELD];
		return field && field.$wrapper ? field.$wrapper : null;
	}

	function render(frm) {
		const $wrapper = box(frm);
		if (!$wrapper) return; // fixture not migrated yet

		$wrapper.empty();

		if (frm.is_new()) {
			$wrapper.append(
				`<p class="text-muted">${__(
					"Save this sales person first — customers are assigned to a record that exists."
				)}</p>`
			);
			return;
		}

		$wrapper.append(`<div class="sa-customers"><p class="text-muted">${__("Loading...")}</p></div>`);
		const $box = $wrapper.find(".sa-customers");

		frappe.call({
			method: `${API}.get_customers`,
			args: { sales_person: frm.doc.name },
			callback(r) {
				paint(frm, $box, r.message || []);
			},
			error() {
				$box.html(
					`<p class="text-muted">${__("Could not load customers — see the browser console.")}</p>`
				);
			},
		});
	}

	function paint(frm, $box, rows) {
		const esc = frappe.utils.escape_html;
		const money = (v) => format_currency(v, frappe.defaults.get_default("currency"));
		const owed = rows.reduce((sum, row) => sum + (row.balance || 0), 0);

		const header = `<div class="d-flex align-items-center justify-content-between mb-3" style="gap:8px;flex-wrap:wrap">
				<div><b>${rows.length}</b> ${__("customer(s)")}${
			rows.length ? ` &nbsp;·&nbsp; ${__("Outstanding")}: <b>${money(owed)}</b>` : ""
		}</div>
				<div>
					<button class="btn btn-sm btn-default sa-add-existing">${__("Assign Existing Customer")}</button>
					<button class="btn btn-sm btn-primary sa-add-new">${__("New Customer")}</button>
				</div>
			</div>`;

		let body;
		if (!rows.length) {
			body = `<p class="text-muted">${__(
				"No customers assigned yet. Use the buttons above to put one on this round."
			)}</p>`;
		} else {
			const cells = rows
				.map((row) => {
					const url = `/app/customer/${encodeURIComponent(row.name)}`;
					return `<tr>
						<td class="text-muted">${esc(row.code || "")}</td>
						<td><a href="${url}">${esc(row.customer_name || row.name)}</a>${
						row.disabled ? ` <span class="text-muted">(${__("disabled")})</span>` : ""
					}</td>
						<td>${esc(row.customer_group || "")}</td>
						<td>${esc(row.payment_terms || "")}</td>
						<td class="text-right"><a href="${url}#sales_team_tab" title="${__(
						"Open the Sales Team table on this customer"
					)}">${format_number(row.contribution || 0)}%</a></td>
						<td class="text-right">${money(row.balance)}</td>
						<td class="text-right"><button class="btn btn-xs btn-default sa-remove" data-customer="${esc(
							row.name
						)}">${__("Remove")}</button></td>
					</tr>`;
				})
				.join("");

			body = `<div style="overflow-x:auto"><table class="table table-bordered" style="margin:0">
					<thead><tr>
						<th>${__("Code")}</th>
						<th>${__("Customer")}</th>
						<th>${__("Customer Group")}</th>
						<th>${__("Payment Terms")}</th>
						<th class="text-right">${__("Contribution")}</th>
						<th class="text-right">${__("Outstanding")}</th>
						<th></th>
					</tr></thead>
					<tbody>${cells}</tbody>
				</table></div>`;
		}

		$box.html(header + body);
		$box.find(".sa-add-existing").on("click", () => assign_existing(frm));
		$box.find(".sa-add-new").on("click", () => create_customer(frm));
		$box.find(".sa-remove").on("click", function () {
			remove(frm, $(this).data("customer"));
		});
	}

	// ── assign an existing customer ────────────────────────────────
	function assign_existing(frm) {
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
						"The customer is put on this sales person's round at 100% contribution. If another sales person holds them, you will be told who before they are moved."
					)}</p>`,
				},
			],
			primary_action_label: __("Assign"),
			primary_action(values) {
				dialog.hide();
				assign(frm, values.customer, false);
			},
		});
		dialog.show();
	}

	function assign(frm, customer, replace) {
		frappe.call({
			method: `${API}.assign_customer`,
			args: { sales_person: frm.doc.name, customer, replace: replace ? 1 : 0 },
			freeze: true,
			freeze_message: __("Assigning..."),
			callback(r) {
				const result = r.message || {};
				if (result.status === "held_by") {
					frappe.confirm(
						__("{0} is currently assigned to {1}. Move them to {2}?", [
							frappe.utils.escape_html(customer),
							frappe.utils.escape_html((result.held_by || []).join(", ")),
							frappe.utils.escape_html(frm.doc.name),
						]),
						() => assign(frm, customer, true)
					);
					return;
				}
				frappe.show_alert({
					message:
						result.status === "unchanged"
							? __("Already on this round.")
							: __("Customer assigned at 100%."),
					indicator: result.status === "unchanged" ? "blue" : "green",
				});
				render(frm);
			},
		});
	}

	// ── create a customer already on this round ────────────────────
	function create_customer(frm) {
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
				{
					fieldname: "territory",
					fieldtype: "Link",
					options: "Territory",
					label: __("Territory"),
				},
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
					method: `${API}.create_customer`,
					args: { sales_person: frm.doc.name, ...values },
					freeze: true,
					freeze_message: __("Creating customer..."),
					callback(r) {
						frappe.show_alert({
							message: __("Created {0}", [
								frappe.utils.escape_html((r.message || {}).customer || ""),
							]),
							indicator: "green",
						});
						render(frm);
					},
				});
			},
		});
		dialog.show();
	}

	function remove(frm, customer) {
		frappe.confirm(
			__("Take {0} off {1}'s round? The customer is not deleted — only the assignment is removed.", [
				frappe.utils.escape_html(customer),
				frappe.utils.escape_html(frm.doc.name),
			]),
			() => {
				frappe.call({
					method: `${API}.unassign_customer`,
					args: { sales_person: frm.doc.name, customer },
					freeze: true,
					freeze_message: __("Removing..."),
					callback() {
						render(frm);
					},
				});
			}
		);
	}

	frappe.ui.form.on("Sales Person", { refresh: render });
})();
