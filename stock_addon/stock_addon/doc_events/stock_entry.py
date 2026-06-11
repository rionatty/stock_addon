# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Stock Entry document events.

On every save/submit (wired to ``validate`` in hooks.py) we:

1. Populate ``custom_sales_price`` (unit selling price per STOCK UoM):
     a. rows pulled in via "Get Items From" a Material Request arrive with the
        MR line TOTAL auto-copied into this field by Frappe's doc mapper —
        normalise it to the unit price (total / MR stock qty);
     b. otherwise fill an empty price from the MR line or the default selling
        Item Price (same per-stock-UoM logic the Inventory Counting doctype
        uses). A user-edited price is never clobbered.
2. Compute ``custom_total_amount_sales_price`` per row
   (= sales price x stock qty).
3. Roll the document totals up into the header:
   ``custom_total_qty``  (sum of the Qty column) and
   ``custom_total_sales_amount`` (sum of the row totals).

The client script in public/js/stock_entry.js mirrors this live in the form;
this server hook is the authoritative recalculation on save.
"""

import frappe
from frappe import _
from frappe.utils import flt


def _row_stock_qty(row):
	"""Row quantity expressed in the STOCK UoM."""
	transfer_qty = flt(row.get("transfer_qty"))
	if transfer_qty:
		return transfer_qty
	return flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1.0)


def _mr_price_info(row):
	"""(unit_price, line_total) from the source Material Request line.

	MR Item.custom_sales_price holds the line TOTAL (that's what the mobile
	app sends), so unit = total / MR stock qty. Returns (None, None) when the
	row has no MR source or the MR line carries no price."""
	mr_item = row.get("material_request_item")
	if not mr_item:
		return None, None
	try:
		mr = frappe.db.get_value(
			"Material Request Item",
			mr_item,
			["custom_sales_price", "stock_qty", "qty", "conversion_factor"],
			as_dict=True,
		)
	except Exception:
		return None, None
	if not mr or not mr.get("custom_sales_price"):
		return None, None

	line_total = flt(mr.custom_sales_price)
	stock_qty = flt(mr.get("stock_qty")) or (
		flt(mr.get("qty")) * (flt(mr.get("conversion_factor")) or 1.0)
	)
	if stock_qty <= 0:
		return None, None
	return line_total / stock_qty, line_total


def _unit_price_from_price_list(item_code, cache):
	"""Unit selling price per stock UoM from the default selling Item Price
	(reuses Inventory Counting's normalised lookup)."""
	if item_code in cache:
		return cache[item_code]
	try:
		from stock_addon.stock_addon.doctype.inventory_counting.inventory_counting import (
			get_selling_rate,
		)
		rate = flt(get_selling_rate(item_code))
	except Exception:
		rate = 0.0
	cache[item_code] = rate
	return rate


def set_sales_prices_and_totals(doc, method=None):
	"""Fill unit sales prices, row totals and the document-level totals."""
	rate_cache = {}
	total_qty = 0.0
	total_sales = 0.0

	for row in doc.items or []:
		if not row.get("item_code"):
			continue

		# 1. Unit selling price. Frappe's doc mapper auto-copies the MR line's
		#    custom_sales_price (a line TOTAL) into this field on Get Items
		#    From, so a value equal to the raw MR total is normalised to the
		#    unit price; an empty value is filled from the MR or the default
		#    selling Item Price. A genuinely user-edited price is left alone.
		current = flt(row.get("custom_sales_price"))
		mr_unit, mr_total = _mr_price_info(row)
		if mr_unit is not None and (not current or abs(current - mr_total) < 0.005):
			row.custom_sales_price = flt(mr_unit)
		elif not current:
			row.custom_sales_price = flt(
				_unit_price_from_price_list(row.item_code, rate_cache)
			)

		# 2. Row total = unit price (per stock UoM) x stock qty.
		stock_qty = _row_stock_qty(row)
		row.custom_total_amount_sales_price = flt(row.custom_sales_price) * stock_qty

		# A row with a sales price must never be saved with an empty total.
		if flt(row.custom_sales_price) and stock_qty and not flt(
			row.custom_total_amount_sales_price
		):
			frappe.throw(
				_("Row #{0} ({1}): Total Amount (Sales Price) could not be calculated even though a Sales Price exists. The document was not saved.").format(
					row.idx, row.item_code
				)
			)

		# 3. Accumulate document totals.
		total_qty += flt(row.get("qty"))
		total_sales += flt(row.custom_total_amount_sales_price)

	doc.custom_total_qty = total_qty
	doc.custom_total_sales_amount = total_sales


# Backwards-compatible alias (older hooks referenced this name).
copy_sales_price_from_material_request = set_sales_prices_and_totals
