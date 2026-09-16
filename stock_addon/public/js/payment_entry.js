// Stock Addon — Payment Entry: show what the party owes, fetch their open
// invoices and allocate the payment, without anyone pressing
// "Get Outstanding Invoices".
//
// Built on ERPNext's own form events rather than beside them, so the result
// is exactly what the button produces:
//   * get_outstanding_documents fetches and allocates the way the button does;
//   * frappe.flags.allocate_payment_amount is what makes a Paid Amount change
//     redistribute the allocations. With it off, the server does not merely
//     skip allocating — it sets every allocated amount to zero
//     (payment_entry.py, allocate_amount_to_references).
//
// Total Outstanding is the party's LEDGER balance, the same figure as Total
// Unpaid on their dashboard — not the sum of the invoices in the table. The
// two differ whenever a payment was posted without being matched to an
// invoice, and then the invoice sum tells a cashier to collect from a
// customer who is actually in credit.
//
// Wrapped in an IIFE: Frappe concatenates every Form client script for a
// doctype into a single new Function(), so nothing here may leak into that
// shared scope.
//
// Installed as a database Client Script by client_scripts.py, so it needs no
// bench build.

(function () {
	if (typeof frappe === "undefined") return;

	// The two ordinary directions only. A refund — paying a customer, or
	// receiving from a supplier — is rare and deliberate, and fetching for it
	// automatically would be a guess.
	const PARTY_FOR = { Receive: "Customer", Pay: "Supplier" };
	const TOTAL_FIELD = "custom_total_outstanding";
	const BALANCE_METHOD = "stock_addon.stock_addon.payment_entry_balance.get_party_balance";

	const POLL_MS = 150;
	const QUIET_TICKS = 3; // ~450 ms with nothing in flight
	const GIVE_UP_TICKS = 70; // ~10 s

	function party_account(frm) {
		return frm.doc.payment_type === "Receive" ? frm.doc.paid_from : frm.doc.paid_to;
	}

	function applicable(frm) {
		const d = frm.doc;
		return d.docstatus === 0 && PARTY_FOR[d.payment_type] === d.party_type && !!d.party;
	}

	function ready(frm) {
		const d = frm.doc;
		return applicable(frm) && !!d.company && !!d.posting_date && !!party_account(frm);
	}

	function set_total(frm, value) {
		if (!frm.fields_dict[TOTAL_FIELD]) return; // fixture not migrated yet
		if (flt(frm.doc[TOTAL_FIELD]) !== flt(value)) frm.set_value(TOTAL_FIELD, value);
	}

	// A new selection supersedes anything still waiting from the last one.
	function next_token(frm) {
		frm.__sa_outstanding_token = (frm.__sa_outstanding_token || 0) + 1;
		return frm.__sa_outstanding_token;
	}

	// Run fn once `condition` holds and nothing has been in flight for a short
	// quiet spell. Waiting for quiet rather than a fixed delay is what makes
	// this right on a slow server as well as a fast one.
	function when_settled(frm, token, condition, fn) {
		let quiet = 0;
		let ticks = 0;
		const tick = () => {
			if (frm.__sa_outstanding_token !== token) return; // a newer change took over
			if (++ticks > GIVE_UP_TICKS) return;
			const busy = ((frappe.request && frappe.request.ajax_count) || 0) > 0;
			quiet = busy || !condition() ? 0 : quiet + 1;
			if (quiet >= QUIET_TICKS) return fn();
			setTimeout(tick, POLL_MS);
		};
		setTimeout(tick, POLL_MS);
	}

	function fetch_and_allocate(frm) {
		frappe.flags.allocate_payment_amount = true;
		// No date filters: the button's dialog defaults to the last 30 days,
		// which silently drops older invoices from the table.
		frm.events.get_outstanding_documents(frm, { allocate_payment_amount: 1 }, true, false);
	}

	function load_balance(frm, token) {
		const d = frm.doc;
		// Remember whose balance this is. The same round trip described below
		// can put an earlier party back on the form, so a balance must only
		// ever land beside the party it was fetched for.
		const asked = { party_type: d.party_type, party: d.party, company: d.company };
		const call = frappe.call({ method: BALANCE_METHOD, args: asked });
		if (!call || typeof call.then !== "function") return;
		call.then((r) => {
			const balance = flt(r && r.message);
			// Land it only once every request has settled. ERPNext's allocation
			// is a server round trip that sends a copy of the form and writes
			// that copy back when it returns — a value set before then is
			// overwritten with the stale one. That is what left this at 0.00
			// beside an invoice allocated for 750.
			when_settled(frm, token, () => true, () => {
				const now_on_form = frm.doc;
				if (
					now_on_form.party !== asked.party ||
					now_on_form.party_type !== asked.party_type ||
					now_on_form.company !== asked.company
				) {
					return; // the form moved on; its own selection sets its own total
				}
				set_total(frm, balance);
			});
		});
	}

	frappe.ui.form.on("Payment Entry", {
		onload(frm) {
			// Recent ERPNext sets this on refresh; older builds never do, and on
			// those a Paid Amount change zeroes the allocations instead of
			// redistributing them.
			if (frm.doc.docstatus === 0) frappe.flags.allocate_payment_amount = true;
		},

		party(frm) {
			const token = next_token(frm);
			if (!applicable(frm)) {
				set_total(frm, 0);
				return;
			}
			// Wait for ERPNext's own party handler first. It looks up the
			// party's account and then CLEARS the references table; a fetch
			// that lands before that clear is wiped by it.
			when_settled(frm, token, () => ready(frm), () => {
				fetch_and_allocate(frm);
				load_balance(frm, token);
			});
		},
	});
})();
