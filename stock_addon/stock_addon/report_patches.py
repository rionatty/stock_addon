# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Server-side patches for standard ERPNext reports.

We can't reliably inject browser-side JS into a standard ERPNext report
(Frappe's Client Script doctype has no "page" script_type, and
``Report.report_script`` is for Script-Report Python code). And
``override_whitelisted_methods`` doesn't intercept report execution either —
Frappe resolves the report's ``execute`` function with ``frappe.get_attr``
based on the report's module path, never going through whitelisted method
routing.

So we do the cleanest thing that actually works: **monkey-patch the report's
``execute`` function in place at app-import time.** When Frappe loads our
hooks.py at boot, it imports ``stock_addon`` which runs our ``__init__.py``,
which installs the wrappers. From that point on every call to the standard
report goes through our post-processor.

For the General Ledger we:
  * Strip Frappe's auto-footer Total row (it summed the running Balance
    column — producing a meaningless negative number).
  * Inject a "Customer / Party Name" column immediately before Debit,
    resolved from (party_type, party) or from the Customer code in the
    "against" field for cash/bank rows. Lookups are batched per type.
  * Relabel the Balance column to "Running Balance".
"""

import frappe


# ─── Helpers ────────────────────────────────────────────────────────────────
def _is_redundant_total_row(row):
	"""Frappe's auto-footer arrives as a dict with ``account`` set to "Total"
	(sometimes wrapped in quotes) and **no** posting_date. The Python GL's
	own intra-period Total + Closing rows have posting_date set, so we can
	tell them apart."""
	if not isinstance(row, dict):
		return False
	account = (row.get("account") or "")
	if not isinstance(account, str):
		return False
	stripped = account.replace("'", "").strip().lower()
	return stripped == "total" and not row.get("posting_date")


def _name_field_for(party_type):
	return {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Employee": "employee_name",
	}.get(party_type)


def _resolve_party_names(data):
	"""Populate ``party_name`` on every data row using batched DB lookups."""
	party_keys = set()
	against_codes = set()

	for row in data:
		if not isinstance(row, dict):
			continue
		if row.get("party_type") and row.get("party"):
			party_keys.add((row["party_type"], row["party"]))
		elif row.get("against"):
			against = (row.get("against") or "").strip()
			if against:
				first = against.split(",")[0].strip()
				if first:
					against_codes.add(first)

	# Party → name (batched per type).
	party_cache = {}
	by_type = {}
	for ptype, p in party_keys:
		by_type.setdefault(ptype, set()).add(p)
	for ptype, parties in by_type.items():
		field = _name_field_for(ptype)
		if not field or not parties:
			continue
		try:
			rows = frappe.get_all(
				ptype,
				filters={"name": ["in", list(parties)]},
				fields=["name", field],
			)
			for r in rows:
				party_cache[(ptype, r["name"])] = r.get(field) or r["name"]
		except Exception:
			pass

	# Customer code → name (for cash/bank rows whose "against" carries the
	# customer code, e.g. C10073 on payment-entry GL lines).
	against_cache = {}
	if against_codes:
		try:
			rows = frappe.get_all(
				"Customer",
				filters={"name": ["in", list(against_codes)]},
				fields=["name", "customer_name"],
			)
			for r in rows:
				against_cache[r["name"]] = r.get("customer_name") or ""
		except Exception:
			pass

	# Stamp party_name onto each row.
	for row in data:
		if not isinstance(row, dict):
			continue
		name = ""
		if row.get("party_type") and row.get("party"):
			name = party_cache.get(
				(row["party_type"], row["party"]), row.get("party") or ""
			)
		elif row.get("against"):
			against = (row.get("against") or "").strip()
			first = against.split(",")[0].strip() if against else ""
			name = against_cache.get(first, "")
		row["party_name"] = name


def _fieldname(col):
	if isinstance(col, dict):
		return col.get("fieldname")
	if isinstance(col, str) and ":" in col:
		return frappe.scrub(col.split(":", 1)[0])
	return None


def _patch_columns(columns):
	"""Insert the Customer/Party Name column immediately before ``debit`` and
	relabel ``balance`` to "Running Balance". Idempotent."""
	party_col = {
		"label": "Customer / Party Name",
		"fieldname": "party_name",
		"fieldtype": "Data",
		"width": 200,
	}

	# Already injected? Only relabel balance.
	if any(_fieldname(c) == "party_name" for c in columns):
		out = []
		for c in columns:
			if isinstance(c, dict) and c.get("fieldname") == "balance":
				c = dict(c)
				c["label"] = "Running Balance"
			out.append(c)
		return out

	new_cols = []
	inserted = False
	for c in columns:
		if not inserted and _fieldname(c) == "debit":
			new_cols.append(party_col)
			inserted = True
		if isinstance(c, dict) and c.get("fieldname") == "balance":
			c = dict(c)
			c["label"] = "Running Balance"
		new_cols.append(c)
	if not inserted:
		new_cols.append(party_col)
	return new_cols


# ─── Public wrapper (installed by stock_addon/__init__.py) ──────────────────
def wrap_general_ledger_execute(original_execute):
	"""Return a wrapped version of the standard GL ``execute``.

	The wrapper calls the original, then post-processes the result to add the
	Customer/Party Name column, strip the broken auto-footer Total, and
	relabel the Balance column.
	"""

	def patched_execute(filters=None):
		result = original_execute(filters)
		if not result:
			return result

		try:
			columns = list(result[0]) if result[0] else []
			data = list(result[1]) if len(result) > 1 else []
			rest = list(result[2:]) if len(result) > 2 else []

			data = [r for r in data if not _is_redundant_total_row(r)]
			columns = _patch_columns(columns)
			_resolve_party_names(data)

			return tuple([columns, data] + rest)
		except Exception:
			# Never break the report on a post-processing error — return the
			# raw result so users still see their ledger.
			frappe.log_error(
				title="stock_addon: GL post-process failed",
				message=frappe.get_traceback(),
			)
			return result

	patched_execute._stock_addon_patched = True
	patched_execute._stock_addon_original = original_execute
	return patched_execute
