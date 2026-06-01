# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate


class InventoryCounting(Document):
	def validate(self):
		self.refresh_in_warehouse_qty()
		self.calculate_variance()
		self.set_status()

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
		for row in self.items:
			if row.counted:
				row.variance = flt(row.counted_qty) - flt(row.in_warehouse_qty)
			else:
				# not counted yet -> no meaningful variance
				row.counted_qty = flt(row.counted_qty)
				row.variance = 0

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
# Add Items
# ---------------------------------------------------------------------- #
@frappe.whitelist()
def get_items_for_count(warehouse=None, item_group=None, count_date=None, include_zero_qty=0):
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
					"in_warehouse_qty": snapshot["qty"],
					"valuation_rate": snapshot["valuation_rate"],
					"counted": 0,
					"counted_qty": 0,
					"variance": 0,
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
		sr.append(
			"items",
			{
				"item_code": r.item_code,
				"warehouse": r.warehouse,
				"batch_no": r.batch_no,
				"qty": flt(r.counted_qty),
				"valuation_rate": flt(r.valuation_rate),
			},
		)

	sr.flags.ignore_permissions = True
	sr.insert()

	doc.db_set("stock_reconciliation", sr.name)
	doc.db_set("status", "Posted")

	return sr.name
