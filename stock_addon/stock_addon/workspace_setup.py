# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

import frappe

STOCK_WORKSPACE = "Stock"
CARD = "Stock Transactions"
LINK_TO = "Inventory Counting"
# place the new link right after this transaction in the card
ANCHOR_AFTER = "Stock Entry"


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
