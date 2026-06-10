# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Stock Entry document events.

When a Stock Entry is built from a Material Request (drag-in via "Get Items
from Material Request" or the bench command), each Stock Entry Detail row
carries the source Material Request name and the source row's name. We use
those to look up the original sales price and copy it into the new
``custom_sales_price`` field on Stock Entry Detail — so the value flows
automatically through the request → transfer chain.
"""

import frappe


def copy_sales_price_from_material_request(doc, method=None):
	"""Populate `custom_sales_price` on each row from the source MR Item."""
	for row in doc.items or []:
		# Don't clobber a value the user (or another hook) already set.
		if row.get("custom_sales_price"):
			continue

		mr_item = row.get("material_request_item")
		if not mr_item:
			continue

		try:
			price = frappe.db.get_value(
				"Material Request Item", mr_item, "custom_sales_price"
			)
		except Exception:
			price = None

		if price:
			row.custom_sales_price = price
