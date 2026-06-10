# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Patches applied to standard ERPNext reports on every ``bench migrate``.

We can't ship a custom ``general_ledger.js`` to ERPNext's reports folder, and
Frappe's ``Client Script`` doctype does not support ``script_type = "page"``.
But the ``Report`` doctype itself has a ``report_script`` field whose contents
``query_report.js`` evaluates whenever that report loads. That's a reliable
hook point we can write to from this app.

So on every migrate we (re-)push the patch JS into the standard
``General Ledger`` report's ``report_script`` field. The patch is idempotent on
the client side, so re-running it is harmless.
"""

import frappe


GL_REPORT_NAME = "General Ledger"


GL_PATCH_JS = r"""
// stock_addon: patches the standard General Ledger report:
//  1. Suppresses Frappe's broken auto-footer total (was summing the running
//     Balance column, which is meaningless).
//  2. Injects a Customer / Party Name column before Debit, resolved from the
//     party or the customer code in the "against" column.
//  3. Renames the Balance column to "Running Balance" for clarity.
//  4. Adds a Print PDF button matching the other Stock Addon reports.
(function () {
    function patch(report_obj) {
        if (!report_obj || report_obj._sa_patched) return;
        report_obj._sa_patched = true;

        // Suppress Frappe's broken auto-footer total.
        if (report_obj.report_doc) report_obj.report_doc.add_total_row = 0;
        report_obj.add_total_row = 0;

        // Print PDF button.
        if (report_obj.page && !report_obj._sa_print_added) {
            report_obj._sa_print_added = true;
            report_obj.page.add_inner_button(__("Print PDF"), function () {
                const data = report_obj.data || [];
                if (!data.length) { frappe.msgprint(__("Run the report first.")); return; }
                frappe.call({
                    method: "stock_addon.stock_addon.report.general_ledger_running_balance.general_ledger_running_balance.get_pdf_html",
                    args: {
                        filters: JSON.stringify(report_obj.get_values()),
                        data: JSON.stringify(data),
                        columns: JSON.stringify((report_obj.columns || []).map(function (c) {
                            return (typeof c === "object") ? c : { fieldname: c, label: c };
                        }))
                    },
                    freeze: true,
                    freeze_message: __("Generating PDF..."),
                    callback: function (r) {
                        if (r.message) {
                            var w = window.open();
                            w.document.write(r.message);
                            w.document.close();
                            setTimeout(function () { w.print(); }, 600);
                        }
                    }
                });
            }).addClass("btn-primary");
        }

        // Patch after_render to inject column + resolve names.
        var _orig = report_obj.after_render;
        report_obj.after_render = function () {
            if (typeof _orig === "function") _orig.apply(this, arguments);
            processRows(report_obj);
        };
        processRows(report_obj);
    }

    function processRows(report_obj) {
        var cols = report_obj.columns || [];

        // Inject Customer / Party Name column before Debit (idempotent).
        var hasCol = cols.some(function (c) {
            return (typeof c === "object" ? c.fieldname : c) === "party_name";
        });
        if (!hasCol) {
            var debitIdx = cols.findIndex(function (c) {
                return (typeof c === "object" ? c.fieldname : c) === "debit";
            });
            var newCol = { label: __("Customer / Party Name"), fieldname: "party_name", fieldtype: "Data", width: 200 };
            if (debitIdx === -1) cols.push(newCol);
            else cols.splice(debitIdx, 0, newCol);
        }

        // Relabel Balance → Running Balance.
        cols.forEach(function (c) {
            if (typeof c === "object" && c.fieldname === "balance") {
                c.label = __("Running Balance");
            }
        });

        // Resolve party names async, then re-render once.
        var partyCache = {};
        var againstCache = {};
        var nameField = { Customer: "customer_name", Supplier: "supplier_name", Employee: "employee_name" };

        var promises = (report_obj.data || []).map(function (row) {
            if (!row || typeof row !== "object") return Promise.resolve();
            if (row.party_name) return Promise.resolve();
            if (row.party_type && row.party) {
                var key = row.party_type + "|" + row.party;
                if (partyCache[key] !== undefined) { row.party_name = partyCache[key]; return Promise.resolve(); }
                var f = nameField[row.party_type];
                if (!f) { row.party_name = ""; return Promise.resolve(); }
                return frappe.db.get_value(row.party_type, row.party, f).then(function (r) {
                    var v = (r && r.message && r.message[f]) ? r.message[f] : (row.party || "");
                    partyCache[key] = v; row.party_name = v;
                }).catch(function () { row.party_name = row.party || ""; });
            } else if (row.against) {
                var first = (row.against.split(",")[0] || "").trim();
                if (!first) { row.party_name = ""; return Promise.resolve(); }
                if (againstCache[first] !== undefined) { row.party_name = againstCache[first]; return Promise.resolve(); }
                return frappe.db.get_value("Customer", first, "customer_name").then(function (r) {
                    var v = (r && r.message && r.message.customer_name) ? r.message.customer_name : "";
                    againstCache[first] = v; row.party_name = v;
                }).catch(function () { row.party_name = ""; });
            } else {
                row.party_name = "";
                return Promise.resolve();
            }
        });

        Promise.all(promises).then(function () {
            if (report_obj.render_datatable) report_obj.render_datatable();
            else if (report_obj.render_report) report_obj.render_report();
        });
    }

    // Wait until the report singleton is ready, then patch.
    var attempts = 0;
    var t = setInterval(function () {
        attempts += 1;
        var r = window.cur_report;
        if (r && r.data !== undefined && r.report_name === "General Ledger") {
            clearInterval(t);
            patch(r);
        } else if (attempts > 60) {
            clearInterval(t);
        }
    }, 200);
})();
""".strip()


def _cleanup_old_client_script():
	"""Remove the previously-installed (and ineffective) page-type Client
	Script. Safe to run repeatedly."""
	stale = "General Ledger Party Name and Print PDF"
	try:
		if frappe.db.exists("Client Script", stale):
			frappe.delete_doc("Client Script", stale, ignore_permissions=True)
			frappe.db.commit()
	except Exception:
		# Non-fatal: log and move on.
		frappe.log_error(
			title="stock_addon: stale Client Script cleanup failed",
			message=frappe.get_traceback(),
		)


def apply_general_ledger_patch():
	"""Push the GL patch JS into the standard report's ``report_script`` field."""
	_cleanup_old_client_script()
	if not frappe.db.exists("Report", GL_REPORT_NAME):
		return

	try:
		report = frappe.get_doc("Report", GL_REPORT_NAME)
		if (report.report_script or "").strip() == GL_PATCH_JS:
			return  # already up to date
		report.report_script = GL_PATCH_JS
		report.flags.ignore_permissions = True
		report.flags.ignore_validate = True
		report.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		# Don't break migrate if the patch can't be saved (e.g. permission
		# restrictions on the standard Report record).
		frappe.log_error(
			title="stock_addon: GL patch failed",
			message=frappe.get_traceback(),
		)
