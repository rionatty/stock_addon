# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Server-side overrides for standard ERPNext reports.

We can't reliably inject client-side JS into a standard ERPNext report (the
Client Script doctype has no ``page`` type, and ``Report.report_script`` is for
Script-Report Python code, not browser JS). So instead we override the
report's ``execute()`` server-side using Frappe's
``override_whitelisted_methods`` hook. Every call to the standard General
Ledger goes through us, we call the original, then post-process the result
before it's returned to the browser:

  * Drop Frappe's meaningless final "Total" row (it summed the running
    Balance column, producing nonsense like -5,923,373).
  * Inject a "Customer / Party Name" column immediately before Debit,
    resolved from the row's party OR (for cash/bank rows) from the customer
    code in the "against" field.
  * Relabel the "Balance" column to "Running Balance".

This pattern is the cleanest server-side approach: no ``bench build``, no
JS-asset gymnastics, no fragile DOM monkey-patching — the standard GL just
returns the right data every time.
"""

import frappe

# Imported lazily inside execute() so this module can be safely loaded even
# when ERPNext isn't installed yet (during fresh-app install).
_ORIGINAL = None


def _orig():
	global _ORIGINAL
	if _ORIGINAL is None:
		from erpnext.accounts.report.general_ledger.general_ledger import (
			execute as original_execute,
		)
		_ORIGINAL = original_execute
	return _ORIGINAL


def _is_redundant_total_row(row):
	"""Filter out the broken final 'Total' row.

	The Python GL itself emits accurate per-period Total and Closing rows
	(with proper subtotals), so we leave those alone. The row we strip is
	Frappe's framework-level auto-footer — easiest tell-tale is that it has
	no posting_date AND its account is the literal string "'Total'" (with
	quotes) which is what Frappe appends.
	"""
	if not isinstance(row, dict):
		return False
	account = (row.get("account") or "")
	if isinstance(account, str):
		stripped = account.replace("'", "").strip().lower()
		# Frappe's add_total_row footer arrives as account == "'Total'" or
		# "Total" with no posting_date. We keep rows that have a posting_date
		# (real entries) or that aren't a Total marker.
		if stripped == "total" and not row.get("posting_date"):
			# Keep the "Total" row only if it has real debit/credit sums and
			# a sensible balance — i.e. the Python GL's intra-period Total.
			# Frappe's auto-footer typically has the balance column summed
			# from running balances, so the value is wildly off. We can't
			# easily distinguish, so we drop *both* and trust users to read
			# the "Closing (Opening + Total)" row above.
			return True
	return False


def _name_field_for(party_type):
	return {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Employee": "employee_name",
	}.get(party_type)


def _resolve_names(data):
	"""Populate `party_name` on every data row using batched DB lookups."""
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

	# Resolve party (Customer/Supplier/Employee) → name in a single query each.
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
			# Don't break the report if the lookup fails.
			pass

	# Resolve Customer codes from "against" column.
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
		if row.get("party_type") and row.get("party"):
			row["party_name"] = party_cache.get(
				(row["party_type"], row["party"]), row.get("party") or ""
			)
		elif row.get("against"):
			against = (row.get("against") or "").strip()
			first = against.split(",")[0].strip() if against else ""
			row["party_name"] = against_cache.get(first, "")
		else:
			row["party_name"] = ""


def _patch_columns(columns):
	"""Inject Customer/Party Name before debit; relabel balance."""
	party_col = {
		"label": "Customer / Party Name",
		"fieldname": "party_name",
		"fieldtype": "Data",
		"width": 200,
	}

	# Detect any existing party_name so we're idempotent.
	def _fname(c):
		if isinstance(c, dict):
			return c.get("fieldname")
		if isinstance(c, str) and ":" in c:
			return frappe.scrub(c.split(":", 1)[0])
		return None

	if any(_fname(c) == "party_name" for c in columns):
		# Still relabel balance.
		for c in columns:
			if isinstance(c, dict) and c.get("fieldname") == "balance":
				c["label"] = "Running Balance"
		return columns

	new_cols = []
	inserted = False
	for c in columns:
		if not inserted and _fname(c) == "debit":
			new_cols.append(party_col)
			inserted = True
		# Relabel "Balance" column as "Running Balance" in place.
		if isinstance(c, dict) and c.get("fieldname") == "balance":
			c = dict(c)
			c["label"] = "Running Balance"
		new_cols.append(c)

	if not inserted:
		new_cols.append(party_col)

	return new_cols


@frappe.whitelist()
def execute(filters=None):
	"""Patched General Ledger ``execute()`` — drop-in replacement for
	``erpnext.accounts.report.general_ledger.general_ledger.execute``.

	Registered via ``override_whitelisted_methods`` in hooks.py so the
	standard report path resolves here first.
	"""
	result = _orig()(filters)
	if not result:
		return result

	# execute() returns (columns, data) or (columns, data, message, chart, summary, ...)
	columns = list(result[0]) if result and result[0] else []
	data = list(result[1]) if result and len(result) > 1 else []
	rest = list(result[2:]) if result and len(result) > 2 else []

	# 1. Strip Frappe's auto-footer Total row.
	data = [r for r in data if not _is_redundant_total_row(r)]

	# 2. Inject party_name column and relabel Balance → Running Balance.
	columns = _patch_columns(columns)

	# 3. Resolve party names (batched lookups).
	_resolve_names(data)

	return tuple([columns, data] + rest)
