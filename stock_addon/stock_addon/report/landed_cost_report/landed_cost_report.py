# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Landed Cost Report.

One section per submitted Landed Cost Voucher:
  - item lines showing the FOB price (purchase rate/amount) next to the
    allocated landed cost and the resulting NEW landed rate/amount;
  - an expense breakdown listing every Applicable Charge (with its
    supplier and expense account) and the voucher's expense total;
  - a voucher total row.

A grand-total row, summary cards (FOB / LC expenses / landed value /
average uplift) and an expenses-by-charge chart round the report off.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    vouchers = get_vouchers(filters)
    data, totals, expense_totals = build_rows(vouchers, filters)
    chart = get_chart(expense_totals)
    summary = get_summary(totals)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "particulars", "label": _("Particulars"), "fieldtype": "Data", "width": 260},
        {"fieldname": "voucher", "label": _("Voucher"), "fieldtype": "Link", "options": "Landed Cost Voucher", "width": 130},
        {"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
        {"fieldname": "receipt_document", "label": _("Receipt Doc"), "fieldtype": "Data", "width": 130},
        {"fieldname": "party", "label": _("Supplier / Account"), "fieldtype": "Data", "width": 180},
        {"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 90},
        {"fieldname": "fob_rate", "label": _("FOB Rate"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "fob_amount", "label": _("FOB Amount"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "lc_amount", "label": _("Landed Cost Expense"), "fieldtype": "Currency", "width": 140},
        {"fieldname": "landed_rate", "label": _("New Landed Rate"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "landed_amount", "label": _("New Landed Amount"), "fieldtype": "Currency", "width": 140},
        {"fieldname": "uplift_pct", "label": _("Uplift %"), "fieldtype": "Percent", "width": 80},
    ]


def get_vouchers(filters):
    conditions = ["lcv.docstatus = 1"]
    values = {}
    if filters.get("from_date"):
        conditions.append("lcv.posting_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("lcv.posting_date <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("landed_cost_voucher"):
        conditions.append("lcv.name = %(lcv)s")
        values["lcv"] = filters.landed_cost_voucher
    if filters.get("company"):
        conditions.append("lcv.company = %(company)s")
        values["company"] = filters.company

    return frappe.db.sql(
        """
        SELECT lcv.name, lcv.posting_date, lcv.total_taxes_and_charges
        FROM `tabLanded Cost Voucher` lcv
        WHERE {conditions}
        ORDER BY lcv.posting_date, lcv.name
        """.format(conditions=" AND ".join(conditions)),
        values,
        as_dict=True,
    )


def build_rows(vouchers, filters):
    data = []
    totals = frappe._dict(fob=0.0, lc=0.0, landed=0.0)
    expense_totals = {}  # description -> amount, for the chart

    for lcv in vouchers:
        items = frappe.get_all(
            "Landed Cost Item",
            filters={"parent": lcv.name, "parenttype": "Landed Cost Voucher"},
            fields=["item_code", "description", "qty", "rate", "amount",
                    "applicable_charges", "receipt_document"],
            order_by="idx",
        )
        charges = frappe.get_all(
            "Landed Cost Taxes and Charges",
            filters={"parent": lcv.name, "parenttype": "Landed Cost Voucher"},
            fields=["description", "expense_account", "amount", "custom_supplier"],
            order_by="idx",
        )

        if filters.get("item_code") and not any(i.item_code == filters.item_code for i in items):
            continue
        if filters.get("supplier") and not any(c.custom_supplier == filters.supplier for c in charges):
            continue

        # ----- voucher header -----
        data.append({
            "particulars": _("Landed Cost Voucher"),
            "voucher": lcv.name,
            "posting_date": lcv.posting_date,
            "bold": 1,
            "is_header": 1,
        })

        # ----- item lines: FOB vs new landed price -----
        v_fob = v_lc = v_landed = 0.0
        for it in items:
            qty = flt(it.qty)
            fob_amount = flt(it.amount)
            lc_amount = flt(it.applicable_charges)
            landed_amount = fob_amount + lc_amount
            landed_rate = landed_amount / qty if qty else 0
            data.append({
                "particulars": "    " + (it.item_code or ""),
                "receipt_document": it.receipt_document,
                "qty": qty,
                "fob_rate": flt(it.rate),
                "fob_amount": fob_amount,
                "lc_amount": lc_amount,
                "landed_rate": landed_rate,
                "landed_amount": landed_amount,
                "uplift_pct": (lc_amount / fob_amount * 100) if fob_amount else 0,
            })
            v_fob += fob_amount
            v_lc += lc_amount
            v_landed += landed_amount

        # ----- expense breakdown -----
        data.append({"particulars": _("Landed Cost Expenses:"), "bold": 1, "is_subheader": 1})
        for ch in charges:
            data.append({
                "particulars": "    " + (ch.description or ch.expense_account or ""),
                "party": ch.custom_supplier or ch.expense_account,
                "lc_amount": flt(ch.amount),
                "is_expense": 1,
            })
            key = ch.description or ch.expense_account or _("Other")
            expense_totals[key] = expense_totals.get(key, 0) + flt(ch.amount)

        # ----- voucher total -----
        data.append({
            "particulars": _("Total — {0}").format(lcv.name),
            "fob_amount": v_fob,
            "lc_amount": v_lc,
            "landed_amount": v_landed,
            "uplift_pct": (v_lc / v_fob * 100) if v_fob else 0,
            "bold": 1,
            "is_total": 1,
        })
        data.append({})  # spacer

        totals.fob += v_fob
        totals.lc += v_lc
        totals.landed += v_landed

    if data:
        data.append({
            "particulars": _("GRAND TOTAL"),
            "fob_amount": totals.fob,
            "lc_amount": totals.lc,
            "landed_amount": totals.landed,
            "uplift_pct": (totals.lc / totals.fob * 100) if totals.fob else 0,
            "bold": 1,
            "is_grand_total": 1,
        })

    return data, totals, expense_totals


def get_chart(expense_totals):
    if not expense_totals:
        return None
    labels = list(expense_totals.keys())
    return {
        "data": {
            "labels": labels,
            "datasets": [{"name": _("Landed Cost Expenses"), "values": [expense_totals[k] for k in labels]}],
        },
        "type": "donut",
        "height": 260,
    }


def get_summary(totals):
    uplift = (totals.lc / totals.fob * 100) if totals.fob else 0
    return [
        {"value": totals.fob, "label": _("Total FOB Value"), "datatype": "Currency", "indicator": "Blue"},
        {"value": totals.lc, "label": _("Total Landed Cost Expenses"), "datatype": "Currency", "indicator": "Orange"},
        {"value": totals.landed, "label": _("Total Landed Value"), "datatype": "Currency", "indicator": "Green"},
        {"value": uplift, "label": _("Average Uplift"), "datatype": "Percent", "indicator": "Red"},
    ]
