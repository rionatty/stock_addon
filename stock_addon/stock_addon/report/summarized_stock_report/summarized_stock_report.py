# Copyright (c) 2025, mohtashim and contributors
# For license information, please see license.txt

"""Summarized Stock Report — Opening, In, Out, Closing.

Shows one row per item + warehouse with:
  Opening qty  = sum of all SLE actual_qty strictly before From Date
  In qty       = positive SLE movements during the period
  Out qty      = absolute value of negative SLE movements during the period
  Closing qty  = Opening + In - Out (always reconciles)

Filters: From Date, To Date, Warehouse, Item Group, Item.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Item"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 130,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 200,
		},
		{
			"label": _("Stock UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 80,
		},
		{
			"label": _("Opening Qty"),
			"fieldname": "opening_qty",
			"fieldtype": "Float",
			"width": 110,
		},
		{"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 90},
		{
			"label": _("Closing Qty"),
			"fieldname": "closing_qty",
			"fieldtype": "Float",
			"width": 110,
		},
	]


def get_data(filters):
	common_conditions, values = _base_conditions(filters)
	ig_join = _item_group_join(filters, values)

	# ── Opening ────────────────────────────────────────────────────────────────
	opening_rows = {}
	if filters.get("from_date"):
		rows = frappe.db.sql(
			f"""
			SELECT sle.item_code, sle.warehouse, SUM(sle.actual_qty) AS qty
			FROM `tabStock Ledger Entry` sle
			{ig_join}
			WHERE sle.is_cancelled = 0 AND sle.docstatus < 2
			  AND sle.posting_date < %(from_date)s
			  {_warehouse_cond(filters)}
			  {_item_cond(filters)}
			  {_company_cond(filters)}
			  {_ig_cond(ig_join)}
			GROUP BY sle.item_code, sle.warehouse
			""",
			values,
			as_dict=True,
		)
		for r in rows:
			opening_rows[(r.item_code, r.warehouse)] = flt(r.qty)

	# ── Period movements ────────────────────────────────────────────────────────
	period_in = {}
	period_out = {}
	if filters.get("from_date") and filters.get("to_date"):
		prows = frappe.db.sql(
			f"""
			SELECT sle.item_code, sle.warehouse, sle.actual_qty
			FROM `tabStock Ledger Entry` sle
			{ig_join}
			WHERE sle.is_cancelled = 0 AND sle.docstatus < 2
			  AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
			  {_warehouse_cond(filters)}
			  {_item_cond(filters)}
			  {_company_cond(filters)}
			  {_ig_cond(ig_join)}
			""",
			values,
			as_dict=True,
		)
		for r in prows:
			key = (r.item_code, r.warehouse)
			qty = flt(r.actual_qty)
			if qty >= 0:
				period_in[key] = period_in.get(key, 0.0) + qty
			else:
				period_out[key] = period_out.get(key, 0.0) + abs(qty)

	# ── Closing snapshot (for rows with no opening/period data) ────────────────
	# Use the latest SLE per item+warehouse as of to_date to discover all relevant rows.
	snapshot_rows = frappe.db.sql(
		f"""
		SELECT sle.item_code, sle.warehouse, sle.qty_after_transaction
		FROM `tabStock Ledger Entry` sle
		{ig_join}
		INNER JOIN (
			SELECT item_code, warehouse, MAX(posting_datetime) AS mpd
			FROM `tabStock Ledger Entry`
			WHERE is_cancelled = 0 AND docstatus < 2
			  {'AND posting_date <= %(to_date)s' if filters.get('to_date') else ''}
			GROUP BY item_code, warehouse
		) latest ON sle.item_code = latest.item_code
		         AND sle.warehouse = latest.warehouse
		         AND sle.posting_datetime = latest.mpd
		WHERE sle.is_cancelled = 0 AND sle.docstatus < 2
		  {_warehouse_cond(filters)}
		  {_item_cond(filters)}
		  {_company_cond(filters)}
		  {_ig_cond(ig_join)}
		ORDER BY sle.item_code, sle.warehouse
		""",
		values,
		as_dict=True,
	)

	# ── Merge into result set ──────────────────────────────────────────────────
	# Collect all keys from opening and period data too (items that moved to zero)
	all_keys = set()
	for r in snapshot_rows:
		all_keys.add((r.item_code, r.warehouse))
	all_keys |= set(opening_rows.keys())
	all_keys |= set(period_in.keys())
	all_keys |= set(period_out.keys())

	# Bulk fetch item names + UOMs
	item_codes = list({k[0] for k in all_keys})
	item_info = {}
	if item_codes:
		for r in frappe.get_all(
			"Item",
			filters=[["name", "in", item_codes]],
			fields=["name", "item_name", "stock_uom"],
		):
			item_info[r.name] = r

	data = []
	for key in sorted(all_keys):
		item_code, warehouse = key
		opening = flt(opening_rows.get(key, 0.0))
		in_qty = flt(period_in.get(key, 0.0))
		out_qty = flt(period_out.get(key, 0.0))
		closing = opening + in_qty - out_qty

		info = item_info.get(item_code, frappe._dict())
		data.append(
			{
				"item_code": item_code,
				"item_name": info.get("item_name") or item_code,
				"warehouse": warehouse,
				"stock_uom": info.get("stock_uom") or "",
				"opening_qty": opening,
				"in_qty": in_qty,
				"out_qty": out_qty,
				"closing_qty": closing,
			}
		)

	return data


# ── helpers ────────────────────────────────────────────────────────────────────


def _base_conditions(filters):
	values = {}
	if filters.get("from_date"):
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		values["to_date"] = filters.to_date
	if filters.get("warehouse"):
		values["warehouse"] = filters.warehouse
	if filters.get("item_code"):
		values["item_code"] = filters.item_code
	if filters.get("company"):
		values["company"] = filters.company
	if filters.get("item_group"):
		ig = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"], as_dict=True)
		if ig:
			values["ig_lft"] = ig.lft
			values["ig_rgt"] = ig.rgt
	return [], values


def _item_group_join(filters, values):
	if filters.get("item_group") and "ig_lft" in values:
		return "JOIN `tabItem` _i ON sle.item_code = _i.name JOIN `tabItem Group` _ig ON _i.item_group = _ig.name"
	return ""


def _ig_cond(ig_join):
	if ig_join:
		return "AND _ig.lft >= %(ig_lft)s AND _ig.rgt <= %(ig_rgt)s"
	return ""


def _warehouse_cond(filters):
	return "AND sle.warehouse = %(warehouse)s" if filters.get("warehouse") else ""


def _item_cond(filters):
	return "AND sle.item_code = %(item_code)s" if filters.get("item_code") else ""


def _company_cond(filters):
	return "AND sle.company = %(company)s" if filters.get("company") else ""


@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
	from stock_addon.stock_addon.report.report_print_utils import render_report_pdf
	return render_report_pdf("Summarized Stock Report", filters, columns or get_columns(), data)
