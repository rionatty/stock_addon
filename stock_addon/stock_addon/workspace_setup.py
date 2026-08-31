# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Workspace setup — runs on ``after_migrate`` to make sure all Stock Addon
doctypes and reports are registered under the right standard ERPNext
workspaces, self-healing if those workspaces are reinstalled.

Reports are grouped by purpose:
  * Stock workspace  — inventory / movement / counting (Van Stock Movement,
                       stock_balance/ledger/report, transfer & manufacture
                       analysis, Inventory Counting doctype).
  * Accounts workspace — money: AR aging, cash collection, banking, daily
                         route status, sales analysis, GL running balance.
"""

import frappe

STOCK_WORKSPACE = "Stock"
CARD = "Stock Transactions"
LINK_TO = "Inventory Counting"
# place the new link right after this transaction in the card
ANCHOR_AFTER = "Stock Entry"


# ─── Report registrations ───────────────────────────────────────────────────
# Each report is added (idempotently) under a new "Stock Addon Reports" /
# "Finance — Route & Sales" card on the appropriate workspace.

STOCK_REPORTS = [
	# (report_name, label shown in the workspace)
	("Van Stock Movement", "Van Stock Movement"),
	("Stock Balance Report", "Stock Balance Report"),
	("Stock Ledger Report", "Stock Ledger Report"),
	("Summarized Stock Report", "Summarized Stock Report"),
	("Stock Transfer and Manufacture Analysis", "Stock Transfer & Manufacture Analysis"),
]

FINANCE_REPORTS = [
	("Daily Summary Report", "Daily Summary Report"),
	("Route Ageing Report", "Route Ageing Report"),
	("Route Cash Collection and Banking", "Route Cash Collection & Banking"),
	("Route Status Report Daily", "Route Status Report (Daily)"),
	("Van Banking Report", "Van Banking Report"),
	("Sales Report", "Sales Report"),
	("General Ledger Running Balance", "General Ledger (Running Balance)"),
]

STOCK_REPORTS_CARD = "Stock Addon Reports"
FINANCE_REPORTS_CARD = "Route & Sales Reports"
# accounting workspace name varies by ERPNext version — first match wins
ACCOUNTS_WORKSPACE_CANDIDATES = ["Accounts", "Accounting", "Invoicing", "Receivables"]


def _accounts_workspace():
	for name in ACCOUNTS_WORKSPACE_CANDIDATES:
		if frappe.db.exists("Workspace", name):
			return name
	return None


def add_inventory_counting_to_stock_workspace():
	"""Idempotently add an 'Inventory Counting' link under the 'Stock
	Transactions' card of the standard Stock workspace.

	Runs on `after_migrate`, so it self-heals if the standard workspace is
	reinstalled/updated.
	"""
	if not frappe.db.exists("Workspace", STOCK_WORKSPACE):
		return
	if not frappe.db.exists("DocType", LINK_TO):
		return

	ws = frappe.get_doc("Workspace", STOCK_WORKSPACE)

	# already present? nothing to do
	if any(l.type == "Link" and l.link_to == LINK_TO for l in ws.links):
		return

	# locate the "Stock Transactions" card and the boundary of its links
	card_start = None
	next_card = None
	anchor_idx = None
	for i, l in enumerate(ws.links):
		if l.type == "Card Break":
			if l.label == CARD:
				card_start = i
			elif card_start is not None and next_card is None:
				next_card = i
				break
		elif card_start is not None and l.link_to == ANCHOR_AFTER:
			anchor_idx = i

	if card_start is None:
		# card not found — don't guess, leave the workspace untouched
		return

	# decide insertion point: just after the anchor, else end of the card
	if anchor_idx is not None:
		insert_at = anchor_idx + 1
	elif next_card is not None:
		insert_at = next_card
	else:
		insert_at = len(ws.links)

	# build a proper child row, then move it to the desired position
	ws.append(
		"links",
		{
			"type": "Link",
			"label": LINK_TO,
			"link_type": "DocType",
			"link_to": LINK_TO,
			"onboard": 0,
			"is_query_report": 0,
			"hidden": 0,
		},
	)
	row = ws.links.pop()  # the row we just appended (at the end)
	ws.links.insert(insert_at, row)

	# re-sequence idx so the card renders in the intended order
	for idx, l in enumerate(ws.links, start=1):
		l.idx = idx

	ws.flags.ignore_permissions = True
	ws.save()
	frappe.db.commit()


# ─── Generic helpers ────────────────────────────────────────────────────────
def _ensure_content_block(ws, block_type, name_key, name_value, extra=None):
	"""Make sure the workspace ``content`` JSON has a block referencing the
	given card/shortcut. In v15/v16 the desk renders strictly from content
	blocks — a card that only exists in the links table never shows up."""
	import json

	try:
		content = json.loads(ws.content or "[]")
	except Exception:
		content = []

	for block in content:
		if block.get("type") == block_type and (block.get("data") or {}).get(name_key) == name_value:
			return False

	block_id = frappe.scrub(name_value)[:20] or block_type
	data = {name_key: name_value, "col": 4}
	if extra:
		data.update(extra)
	content.append({"id": f"{block_type}_{block_id}", "type": block_type, "data": data})
	ws.content = json.dumps(content)
	return True


def _ensure_card_with_reports(workspace_name, card_label, reports):
	"""Idempotently add a Card Break with the given label to the workspace,
	then append a Report link for each (name, label) in ``reports`` that
	actually exists and isn't already on the workspace.

	Self-healing: if the workspace was rebuilt and lost the card, we add it
	again on the next migrate.
	"""
	if not frappe.db.exists("Workspace", workspace_name):
		return

	ws = frappe.get_doc("Workspace", workspace_name)
	changed = False

	# Find where our card begins. If it doesn't exist, append one + a header
	# card-break at the end of the workspace.
	card_start = next(
		(i for i, l in enumerate(ws.links)
		 if l.type == "Card Break" and (l.label or "") == card_label),
		None,
	)
	if card_start is None:
		ws.append("links", {
			"type": "Card Break",
			"label": card_label,
			"icon": "report",
			"hidden": 0,
		})
		card_start = len(ws.links) - 1
		changed = True

	# Collect every link already on the workspace by report_name so we don't
	# add the same one twice.
	present = {
		(l.link_to or "")
		for l in ws.links
		if l.type == "Link" and l.link_type == "Report"
	}

	for report_name, label in reports:
		if not frappe.db.exists("Report", report_name):
			continue
		if report_name in present:
			continue
		ws.append("links", {
			"type": "Link",
			"label": label,
			"link_type": "Report",
			"link_to": report_name,
			"is_query_report": 1,
			"hidden": 0,
			"onboard": 0,
		})
		changed = True

	# v16 renders from content blocks — register the card there too
	if _ensure_content_block(ws, "card", "card_name", card_label):
		changed = True

	if not changed:
		return

	# Re-index idx so the workspace renders in the order we built it.
	for idx, l in enumerate(ws.links, start=1):
		l.idx = idx

	ws.flags.ignore_permissions = True
	ws.save()
	frappe.db.commit()


def add_stock_addon_reports_to_stock_workspace():
	"""Register inventory / stock reports under the Stock workspace."""
	_ensure_card_with_reports(STOCK_WORKSPACE, STOCK_REPORTS_CARD, STOCK_REPORTS)


def add_summarized_stock_report_shortcut():
	"""Idempotently add a 'Summarized Stock Report' shortcut tile to the
	Stock workspace so it sits at the top with the other quick links."""
	report = "Summarized Stock Report"
	if not frappe.db.exists("Workspace", STOCK_WORKSPACE):
		return
	if not frappe.db.exists("Report", report):
		return

	ws = frappe.get_doc("Workspace", STOCK_WORKSPACE)
	changed = False

	if not any(s.link_to == report for s in ws.shortcuts):
		ws.append("shortcuts", {
			"type": "Report",
			"label": report,
			"link_to": report,
			"doc_view": "",
		})
		changed = True

	# v16 renders from content blocks — the tile needs a shortcut block too
	if _ensure_content_block(ws, "shortcut", "shortcut_name", report, extra={"col": 3}):
		changed = True

	if not changed:
		return

	ws.flags.ignore_permissions = True
	ws.save()
	frappe.db.commit()


def add_route_and_sales_reports_to_accounts_workspace():
	"""Register money / route / GL reports under the accounting workspace
	(named Accounts / Accounting / Invoicing depending on ERPNext version)."""
	workspace = _accounts_workspace()
	if workspace:
		_ensure_card_with_reports(workspace, FINANCE_REPORTS_CARD, FINANCE_REPORTS)


def add_journey_plan_to_accounts_workspace():
	"""Idempotently add a 'Journey Plan' DocType link under a 'Route Management'
	card on the accounting workspace so field managers can open it from the desk."""
	workspace = _accounts_workspace()
	if not workspace:
		return
	if not frappe.db.exists("DocType", "Journey Plan"):
		return

	ws = frappe.get_doc("Workspace", workspace)
	changed = False

	CARD_LABEL = "Route Management"
	LINK_TO = "Journey Plan"

	# find or create the card
	card_start = next(
		(i for i, l in enumerate(ws.links)
		 if l.type == "Card Break" and (l.label or "") == CARD_LABEL),
		None,
	)
	if card_start is None:
		ws.append("links", {
			"type": "Card Break",
			"label": CARD_LABEL,
			"icon": "route",
			"hidden": 0,
		})
		changed = True

	# add the Journey Plan link if not already there
	already_present = any(
		l.type == "Link" and l.link_to == LINK_TO for l in ws.links
	)
	if not already_present:
		ws.append("links", {
			"type": "Link",
			"label": "Journey Plan",
			"link_type": "DocType",
			"link_to": LINK_TO,
			"is_query_report": 0,
			"hidden": 0,
			"onboard": 0,
		})
		changed = True

	# v16 content block
	if _ensure_content_block(ws, "card", "card_name", CARD_LABEL):
		changed = True

	if not changed:
		return

	for idx, l in enumerate(ws.links, start=1):
		l.idx = idx

	ws.flags.ignore_permissions = True
	ws.save()
	frappe.db.commit()


# ─── SAP Integration workspace ──────────────────────────────────────────────

def ensure_sap_integration_workspace():
	"""Self-heal the "SAP Integration" home tile from the JSON shipped in
	workspace/sap_integration/. Frappe's own workspace sync normally does
	this during migrate, but this guarantees the tile exists (and its
	layout stays current) even when that sync is skipped."""
	import json
	import os

	path = os.path.join(
		frappe.get_app_path("stock_addon"),
		"stock_addon", "workspace", "sap_integration", "sap_integration.json",
	)
	if not os.path.exists(path):
		return

	with open(path) as f:
		data = json.load(f)
	for key in ("creation", "modified", "modified_by", "owner", "docstatus", "idx"):
		data.pop(key, None)

	name = data.get("name") or "SAP Integration"
	if frappe.db.exists("Workspace", name):
		ws = frappe.get_doc("Workspace", name)
		ws.content = data.get("content")
		ws.public = 1
		ws.is_hidden = 0
		ws.set("shortcuts", [])
		ws.set("links", [])
		for row in data.get("shortcuts", []):
			ws.append("shortcuts", row)
		for row in data.get("links", []):
			ws.append("links", row)
		ws.flags.ignore_permissions = True
		ws.save()
	else:
		doc = frappe.get_doc(data)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
