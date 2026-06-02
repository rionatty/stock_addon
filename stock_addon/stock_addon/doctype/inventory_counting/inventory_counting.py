# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate


class InventoryCounting(Document):
	def validate(self):
		self.set_default_price_list()
		self.default_item_warehouse()
		self.refresh_in_warehouse_qty()
		self.set_uom_and_conversion()
		self.calculate_variance()
		self.validate_batches()
		self.set_status()

	def set_uom_and_conversion(self):
		"""Default the count UoM to the stock UoM and resolve the conversion
		factor (stock-UoM qty per 1 count UoM) for every row."""
		for row in self.items:
			if not row.item_code:
				continue
			if not row.stock_uom:
				row.stock_uom = frappe.db.get_value("Item", row.item_code, "stock_uom")
			if not row.uom:
				row.uom = row.stock_uom

			if row.uom == row.stock_uom:
				row.conversion_factor = 1.0
			else:
				row.conversion_factor = get_uom_conversion_factor(row.item_code, row.uom)

			if flt(row.conversion_factor) <= 0:
				frappe.throw(
					_("Row #{0}: No UoM conversion defined from {1} to {2} for item {3}.").format(
						row.idx,
						frappe.bold(row.uom),
						frappe.bold(row.stock_uom),
						frappe.bold(row.item_code),
					)
				)

	def set_default_price_list(self):
		if not self.price_list:
			self.price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")

	def default_item_warehouse(self):
		"""Items inherit the header warehouse when one isn't set on the row.

		This keeps the mobile app simple: the count is created for a warehouse,
		and any rows added (including extras found by the rep) belong to it."""
		if not self.warehouse:
			return
		for row in self.items:
			if not row.warehouse:
				row.warehouse = self.warehouse

	def on_submit(self):
		self.db_set("status", "Closed")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------ #
	# helpers
	# ------------------------------------------------------------------ #
	def refresh_in_warehouse_qty(self):
		"""Pull the on-hand qty (and valuation rate) as of the count date
		for every row that has an item + warehouse."""
		count_date = self.count_date or nowdate()
		for row in self.items:
			if not (row.item_code and row.warehouse):
				continue
			snapshot = get_stock_as_on(row.item_code, row.warehouse, count_date, row.batch_no)
			row.in_warehouse_qty = snapshot.get("qty")
			# keep the latest valuation rate so the inventory posting can value the variance
			row.valuation_rate = snapshot.get("valuation_rate")

	def calculate_variance(self):
		"""All stock maths is done in the STOCK UoM.

		counted_qty is entered in the count UoM, so it is first converted to the
		stock UoM (stock_qty). The on-hand qty and selling rate are already in
		stock-UoM terms, so the variance and its value are consistent.
		"""
		rate_cache = {}
		total_qty = 0.0
		total_value = 0.0
		for row in self.items:
			cf = flt(row.conversion_factor) or 1.0
			# counted qty (count UoM) -> stock UoM
			row.stock_qty = flt(row.counted_qty) * cf

			if row.counted:
				row.variance = flt(row.stock_qty) - flt(row.in_warehouse_qty)
			else:
				row.variance = 0

			# selling rate is resolved per STOCK UoM so it lines up with the
			# stock-UoM variance
			if row.item_code:
				if row.item_code not in rate_cache:
					rate_cache[row.item_code] = get_selling_rate(row.item_code, self.price_list)
				row.selling_rate = rate_cache[row.item_code]
			else:
				row.selling_rate = 0
			row.variance_value = flt(row.variance) * flt(row.selling_rate)

			total_qty += flt(row.variance)
			total_value += flt(row.variance_value)

		self.total_variance_qty = total_qty
		self.total_variance_value = total_value

	def validate_batches(self):
		"""Where the item is batch-tracked a batch must be supplied and must belong
		to the item; counted quantities may not be negative."""
		batch_cache = {}
		for row in self.items:
			if not row.item_code:
				continue

			if row.item_code not in batch_cache:
				batch_cache[row.item_code] = frappe.db.get_value(
					"Item", row.item_code, "has_batch_no"
				)
			has_batch = batch_cache[row.item_code]

			if has_batch and not row.batch_no:
				frappe.throw(
					_("Row #{0}: Batch No. is mandatory for batch-tracked item {1}.").format(
						row.idx, frappe.bold(row.item_code)
					)
				)

			if row.batch_no:
				batch_item = frappe.db.get_value("Batch", row.batch_no, "item")
				if batch_item and batch_item != row.item_code:
					frappe.throw(
						_("Row #{0}: Batch {1} does not belong to item {2}.").format(
							row.idx, frappe.bold(row.batch_no), frappe.bold(row.item_code)
						)
					)

			if flt(row.counted_qty) < 0:
				frappe.throw(_("Row #{0}: Counted Qty cannot be negative.").format(row.idx))

	def set_status(self):
		if self.docstatus == 2:
			self.status = "Cancelled"
		elif self.stock_reconciliation:
			self.status = "Posted"
		elif self.docstatus == 1:
			self.status = "Closed"
		else:
			self.status = "Open"


# ---------------------------------------------------------------------- #
# Stock snapshot
# ---------------------------------------------------------------------- #
@frappe.whitelist()
def get_stock_as_on(item_code, warehouse, count_date=None, batch_no=None):
	"""Return on-hand qty and valuation rate for an item/warehouse as of a date.

	If a batch is given, the qty is summed for that batch up to the count date.
	Otherwise the latest Stock Ledger Entry balance on or before the date is used.
	"""
	if not (item_code and warehouse):
		return {"qty": 0.0, "valuation_rate": 0.0}

	count_date = getdate(count_date or nowdate())

	if batch_no:
		params = {
			"item_code": item_code,
			"warehouse": warehouse,
			"batch_no": batch_no,
			"count_date": count_date,
		}
		qty = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(actual_qty), 0)
			FROM `tabStock Ledger Entry`
			WHERE item_code = %(item_code)s
			  AND warehouse = %(warehouse)s
			  AND batch_no = %(batch_no)s
			  AND is_cancelled = 0
			  AND docstatus < 2
			  AND posting_date <= %(count_date)s
			""",
			params,
		)[0][0]
		rate = frappe.db.sql(
			"""
			SELECT valuation_rate
			FROM `tabStock Ledger Entry`
			WHERE item_code = %(item_code)s
			  AND warehouse = %(warehouse)s
			  AND batch_no = %(batch_no)s
			  AND is_cancelled = 0
			  AND docstatus < 2
			  AND posting_date <= %(count_date)s
			ORDER BY posting_datetime DESC, creation DESC
			LIMIT 1
			""",
			params,
		)
		return {"qty": flt(qty), "valuation_rate": flt(rate[0][0]) if rate else 0.0}

	row = frappe.db.sql(
		"""
		SELECT qty_after_transaction, valuation_rate
		FROM `tabStock Ledger Entry`
		WHERE item_code = %(item_code)s
		  AND warehouse = %(warehouse)s
		  AND is_cancelled = 0
		  AND docstatus < 2
		  AND posting_date <= %(count_date)s
		ORDER BY posting_datetime DESC, creation DESC
		LIMIT 1
		""",
		{"item_code": item_code, "warehouse": warehouse, "count_date": count_date},
		as_dict=True,
	)

	if row:
		return {"qty": flt(row[0].qty_after_transaction), "valuation_rate": flt(row[0].valuation_rate)}
	return {"qty": 0.0, "valuation_rate": 0.0}


# ---------------------------------------------------------------------- #
# UoM conversion
# ---------------------------------------------------------------------- #
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_uom_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field search returning only the item's own UoMs (stock UoM plus any
	UOM Conversion Detail rows). Used to constrain the Count UoM field."""
	item_code = (filters or {}).get("item_code")
	if not item_code:
		return []

	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	uoms = set()
	if stock_uom:
		uoms.add(stock_uom)
	for d in frappe.get_all(
		"UOM Conversion Detail", filters={"parent": item_code}, fields=["uom"]
	):
		uoms.add(d.uom)

	txt = (txt or "").lower()
	return [[u] for u in sorted(uoms) if txt in (u or "").lower()]


@frappe.whitelist()
def get_uom_conversion_factor(item_code, uom):
	"""Return how many STOCK-UoM units make up 1 `uom` for the item.

	Looks up the item's UOM Conversion Detail. Returns 1 when uom is the stock
	UoM, or 0 when no conversion is defined (caller treats 0 as an error)."""
	if not (item_code and uom):
		return 1.0

	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	if uom == stock_uom:
		return 1.0

	cf = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)
	return flt(cf)


# ---------------------------------------------------------------------- #
# Selling price (for variance valuation on the printout)
# ---------------------------------------------------------------------- #
@frappe.whitelist()
def get_selling_rate(item_code, price_list=None):
	"""Selling price of an item, normalised PER STOCK UoM.

	Prefers the given (or default selling) price list, then any selling Item
	Price, then the item's standard rate. When the matched Item Price is quoted
	in a non-stock UoM, the rate is divided by that UoM's conversion factor so
	the result is always per stock UoM (consistent with the stock-UoM variance).
	"""
	if not item_code:
		return 0.0

	price_list = price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")

	def latest_price(filters):
		# pick the most recent valid Item Price (there can be several per item
		# differing by uom / qty / customer / validity)
		rows = frappe.get_all(
			"Item Price",
			filters=filters,
			fields=["price_list_rate", "uom"],
			order_by="valid_from desc, modified desc",
			limit=1,
		)
		return rows[0] if rows else None

	price = None
	if price_list:
		price = latest_price({"item_code": item_code, "selling": 1, "price_list": price_list})
	if not price:
		price = latest_price({"item_code": item_code, "selling": 1})

	if price:
		rate = flt(price.price_list_rate)
		# normalise a non-stock-UoM price down to the stock UoM
		if price.uom:
			cf = get_uom_conversion_factor(item_code, price.uom)
			if cf > 0:
				rate = rate / cf
		return flt(rate)

	# last resort: item master selling rate (already per stock UoM)
	return flt(frappe.db.get_value("Item", item_code, "standard_rate"))


# ---------------------------------------------------------------------- #
# Add Items
# ---------------------------------------------------------------------- #
@frappe.whitelist()
def get_items_for_count(
	warehouse=None, item_group=None, count_date=None, include_zero_qty=0, price_list=None
):
	"""Build the list of rows for the "Add Items" action.

	Pulls every item that currently has a Bin in the chosen warehouse (optionally
	filtered by item group). On-hand qty is taken as of `count_date`.
	"""
	include_zero_qty = int(include_zero_qty or 0)
	count_date = getdate(count_date or nowdate())

	conditions = ["b.warehouse IS NOT NULL"]
	values = {}
	if warehouse:
		conditions.append("b.warehouse = %(warehouse)s")
		values["warehouse"] = warehouse

	join = ""
	if item_group:
		ig = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
		if ig:
			join = "JOIN `tabItem` i ON b.item_code = i.name JOIN `tabItem Group` ig ON i.item_group = ig.name"
			conditions.append("ig.lft >= %(lft)s AND ig.rgt <= %(rgt)s")
			values["lft"] = ig.lft
			values["rgt"] = ig.rgt

	bins = frappe.db.sql(
		f"""
		SELECT b.item_code, b.warehouse
		FROM `tabBin` b
		{join}
		WHERE {' AND '.join(conditions)}
		ORDER BY b.item_code
		""",
		values,
		as_dict=True,
	)

	rows = []
	for b in bins:
		item = frappe.db.get_value(
			"Item", b.item_code, ["item_name", "stock_uom", "has_batch_no"], as_dict=True
		) or {}

		# Batch-tracked items -> one row per batch that has movement up to the count date
		batch_nos = []
		if item.get("has_batch_no"):
			batch_nos = [
				r[0]
				for r in frappe.db.sql(
					"""
					SELECT batch_no
					FROM `tabStock Ledger Entry`
					WHERE item_code = %(item_code)s
					  AND warehouse = %(warehouse)s
					  AND batch_no IS NOT NULL AND batch_no != ''
					  AND is_cancelled = 0 AND docstatus < 2
					  AND posting_date <= %(count_date)s
					GROUP BY batch_no
					""",
					{"item_code": b.item_code, "warehouse": b.warehouse, "count_date": count_date},
				)
			]

		selling_rate = get_selling_rate(b.item_code, price_list)

		batch_keys = batch_nos or [None]
		for batch_no in batch_keys:
			snapshot = get_stock_as_on(b.item_code, b.warehouse, count_date, batch_no)
			if not include_zero_qty and flt(snapshot["qty"]) == 0:
				continue
			rows.append(
				{
					"item_code": b.item_code,
					"item_name": item.get("item_name"),
					"warehouse": b.warehouse,
					"batch_no": batch_no,
					"uom": item.get("stock_uom"),
					"stock_uom": item.get("stock_uom"),
					"conversion_factor": 1.0,
					"in_warehouse_qty": snapshot["qty"],
					"valuation_rate": snapshot["valuation_rate"],
					"selling_rate": selling_rate,
					"counted": 0,
					"counted_qty": 0,
					"stock_qty": 0,
					"variance": 0,
					"variance_value": 0,
				}
			)
	return rows


# ---------------------------------------------------------------------- #
# Copy to Inventory Posting  ->  Stock Reconciliation
# ---------------------------------------------------------------------- #
@frappe.whitelist()
def make_inventory_posting(source_name, company=None):
	"""SAP B1 "Copy to Inventory Posting": create a Stock Reconciliation that
	sets each counted item to its counted qty. Only counted rows are posted."""
	doc = frappe.get_doc("Inventory Counting", source_name)

	if doc.docstatus != 1:
		frappe.throw(_("Submit the Inventory Counting before posting to inventory."))
	if doc.stock_reconciliation:
		frappe.throw(
			_("Inventory Counting {0} is already posted via {1}.").format(
				doc.name, doc.stock_reconciliation
			)
		)

	counted_rows = [r for r in doc.items if r.counted]
	if not counted_rows:
		frappe.throw(_("No counted rows to post. Tick 'Counted' and enter a Counted Qty first."))

	company = company or frappe.defaults.get_user_default("Company")

	sr = frappe.new_doc("Stock Reconciliation")
	sr.purpose = "Stock Reconciliation"
	sr.posting_date = doc.count_date
	if doc.count_time:
		sr.posting_time = doc.count_time
	if company:
		sr.company = company
	sr.set_posting_time = 1

	for r in counted_rows:
		# Stock Reconciliation works in the STOCK UoM, so post the converted
		# stock_qty (fall back to counted_qty * conversion_factor for safety)
		stock_qty = flt(r.stock_qty) or flt(r.counted_qty) * (flt(r.conversion_factor) or 1.0)
		item_row = {
			"item_code": r.item_code,
			"warehouse": r.warehouse,
			"qty": stock_qty,
			"valuation_rate": flt(r.valuation_rate),
		}
		if r.batch_no:
			item_row["batch_no"] = r.batch_no
			# tell ERPNext (v14/v15) to honour the plain batch_no field instead of
			# requiring a Serial and Batch Bundle to be created up-front
			item_row["use_serial_batch_fields"] = 1
		sr.append("items", item_row)

	sr.flags.ignore_permissions = True
	sr.insert()

	doc.db_set("stock_reconciliation", sr.name)
	doc.db_set("status", "Posted")

	return sr.name
