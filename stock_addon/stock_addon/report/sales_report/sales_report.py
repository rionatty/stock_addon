# -*- coding: utf-8 -*-
# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from frappe.utils import (
    flt, getdate, fmt_money, formatdate,
    format_datetime, get_datetime
)


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)

    columns = get_columns(filters)
    data = get_data(filters)
    report_summary = get_report_summary(data)
    chart = get_chart_data(data, filters)

    return columns, data, None, chart, report_summary


def validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are mandatory"))
    if getdate(filters.get("from_date")) > getdate(filters.get("to_date")):
        frappe.throw(_("From Date cannot be greater than To Date"))
    if not filters.get("company"):
        frappe.throw(_("Company is mandatory"))


# ============================================================
# COLUMNS
# ============================================================

def get_columns(filters):
    group_by = filters.get("group_by") or "Detailed"

    if group_by == "Route":
        return [
            {"label": _("Route"), "fieldname": "sales_person", "fieldtype": "Link",
             "options": "Sales Person", "width": 200},
            {"label": _("Invoices"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 90},
            {"label": _("Customers"), "fieldname": "customer_count", "fieldtype": "Int", "width": 90},
            {"label": _("Qty Sold"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
            {"label": _("Gross Total"), "fieldname": "gross_total", "fieldtype": "Currency", "width": 130},
            {"label": _("Discount"), "fieldname": "discount", "fieldtype": "Currency", "width": 110},
            {"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 130},
            {"label": _("Tax"), "fieldname": "tax", "fieldtype": "Currency", "width": 110},
            {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 140},
            {"label": _("Paid"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 130},
            {"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 130},
        ]

    if group_by == "Customer":
        return [
            {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
             "options": "Customer", "width": 160},
            {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
            {"label": _("Group"), "fieldname": "customer_group", "fieldtype": "Link",
             "options": "Customer Group", "width": 130},
            {"label": _("Invoices"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 80},
            {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
            {"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 130},
            {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 140},
            {"label": _("Paid"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 130},
            {"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 130},
        ]

    if group_by == "Item":
        return [
            {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link",
             "options": "Item", "width": 160},
            {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
            {"label": _("Group"), "fieldname": "item_group", "fieldtype": "Link",
             "options": "Item Group", "width": 130},
            {"label": _("Brand"), "fieldname": "brand", "fieldtype": "Link",
             "options": "Brand", "width": 100},
            {"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link",
             "options": "UOM", "width": 80},
            {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
            {"label": _("Avg Rate"), "fieldname": "avg_rate", "fieldtype": "Currency", "width": 110},
            {"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 130},
            {"label": _("# Invoices"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 90},
        ]

    if group_by == "Item Group":
        return [
            {"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link",
             "options": "Item Group", "width": 200},
            {"label": _("Items"), "fieldname": "item_count", "fieldtype": "Int", "width": 80},
            {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
            {"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 130},
            {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 140},
        ]

    if group_by == "Date":
        return [
            {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
            {"label": _("Invoices"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 90},
            {"label": _("Customers"), "fieldname": "customer_count", "fieldtype": "Int", "width": 90},
            {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
            {"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 130},
            {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 140},
            {"label": _("Paid"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 130},
            {"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 130},
        ]

    # DETAILED
    return [
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 90},
        {"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 130},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Route"), "fieldname": "sales_person", "fieldtype": "Link",
         "options": "Sales Person", "width": 130},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
         "options": "Customer", "width": 140},
        {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 160},
        {"label": _("Payment Terms"), "fieldname": "payment_terms_template",
         "fieldtype": "Link", "options": "Payment Terms Template", "width": 140},
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link",
         "options": "Item", "width": 130},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": _("Group"), "fieldname": "item_group", "fieldtype": "Link",
         "options": "Item Group", "width": 110},
        {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80},
        {"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link",
         "options": "UOM", "width": 70},
        {"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "width": 100},
        {"label": _("Discount"), "fieldname": "discount_amount", "fieldtype": "Currency", "width": 100},
        {"label": _("Net Amount"), "fieldname": "net_total", "fieldtype": "Currency", "width": 120},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 130},
        {"label": _("Paid"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 120},
    ]


# ============================================================
# DATA FETCHING
# ============================================================

def build_conditions(filters):
    conditions = " si.docstatus = 1 AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s "
    params = {
        "from_date": getdate(filters.get("from_date")),
        "to_date": getdate(filters.get("to_date")),
    }

    if filters.get("company"):
        conditions += " AND si.company = %(company)s "
        params["company"] = filters.get("company")

    if filters.get("customer"):
        conditions += " AND si.customer = %(customer)s "
        params["customer"] = filters.get("customer")

    if filters.get("customer_group"):
        conditions += " AND si.customer_group = %(customer_group)s "
        params["customer_group"] = filters.get("customer_group")

    if filters.get("territory"):
        conditions += " AND si.territory = %(territory)s "
        params["territory"] = filters.get("territory")

    if filters.get("item_code"):
        conditions += " AND sii.item_code = %(item_code)s "
        params["item_code"] = filters.get("item_code")

    if filters.get("item_group"):
        conditions += " AND sii.item_group = %(item_group)s "
        params["item_group"] = filters.get("item_group")

    if filters.get("brand"):
        conditions += " AND sii.brand = %(brand)s "
        params["brand"] = filters.get("brand")

    if filters.get("warehouse"):
        conditions += " AND sii.warehouse = %(warehouse)s "
        params["warehouse"] = filters.get("warehouse")

    if filters.get("sales_person"):
        conditions += " AND st.sales_person = %(sales_person)s "
        params["sales_person"] = filters.get("sales_person")

    # ===== PAYMENT TERMS FILTERS =====
    if filters.get("payment_terms_template"):
        conditions += " AND si.payment_terms_template = %(payment_terms_template)s "
        params["payment_terms_template"] = filters.get("payment_terms_template")

    if filters.get("payment_term"):
        conditions += """ AND EXISTS (
            SELECT 1 FROM `tabPayment Schedule` ps
            WHERE ps.parent = si.name
              AND ps.payment_term = %(payment_term)s
        ) """
        params["payment_term"] = filters.get("payment_term")

    if not filters.get("include_returns"):
        conditions += " AND COALESCE(si.is_return, 0) = 0 "

    payment_status = filters.get("payment_status") or []
    if payment_status and isinstance(payment_status[0], dict):
        payment_status = [s.get("value") for s in payment_status]

    if payment_status:
        placeholders = ", ".join([f"%(status_{i})s" for i in range(len(payment_status))])
        conditions += f" AND si.status IN ({placeholders}) "
        for i, s in enumerate(payment_status):
            params[f"status_{i}"] = s

    return conditions, params


def get_raw_data(filters):
    """Pull invoice line items joined with sales team."""
    conditions, params = build_conditions(filters)

    query = f"""
        SELECT
            si.name                                    AS invoice,
            si.posting_date                            AS posting_date,
            si.status                                  AS status,
            si.payment_terms_template                  AS payment_terms_template,
            si.customer                                AS customer,
            si.customer_name                           AS customer_name,
            si.customer_group                          AS customer_group,
            si.territory                               AS territory,
            si.grand_total                             AS si_grand_total,
            si.outstanding_amount                      AS si_outstanding,
            si.is_return                               AS is_return,
            COALESCE(st.sales_person, 'Unassigned')    AS sales_person,
            COALESCE(st.allocated_percentage, 100)     AS allocated_percentage,
            sii.item_code                              AS item_code,
            sii.item_name                              AS item_name,
            sii.item_group                             AS item_group,
            sii.brand                                  AS brand,
            sii.uom                                    AS uom,
            sii.qty                                    AS qty,
            sii.rate                                   AS rate,
            sii.discount_amount                        AS discount_amount,
            sii.amount                                 AS net_total,
            sii.base_amount                            AS base_amount,
            sii.warehouse                              AS warehouse
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        LEFT JOIN `tabSales Team` st ON st.parent = si.name
        WHERE {conditions}
        ORDER BY si.posting_date, si.name, sii.idx
    """

    rows = frappe.db.sql(query, params, as_dict=1) or []

    # Apply allocation percentage to amount fields
    for r in rows:
        alloc = flt(r.allocated_percentage) / 100.0
        r.qty = flt(r.qty) * alloc
        r.net_total = flt(r.net_total) * alloc
        r.discount_amount = flt(r.discount_amount) * alloc
        r.allocated_grand_total = flt(r.si_grand_total) * alloc
        r.allocated_outstanding = flt(r.si_outstanding) * alloc
        r.allocated_paid = (flt(r.si_grand_total) - flt(r.si_outstanding)) * alloc

    return rows


def get_data(filters):
    raw = get_raw_data(filters)
    if not raw:
        return []

    group_by = filters.get("group_by") or "Detailed"

    if group_by == "Detailed":
        return build_detailed(raw)
    if group_by == "Route":
        return build_grouped(raw, key_field="sales_person")
    if group_by == "Customer":
        return build_grouped(raw, key_field="customer", label_field="customer_name",
                             extra_fields=["customer_group"])
    if group_by == "Item":
        return build_grouped(raw, key_field="item_code", label_field="item_name",
                             extra_fields=["item_group", "brand", "uom"], compute_avg_rate=True)
    if group_by == "Item Group":
        return build_grouped(raw, key_field="item_group")
    if group_by == "Date":
        return build_grouped(raw, key_field="posting_date")

    return build_detailed(raw)


def build_detailed(raw):
    """One row per invoice line."""
    data = []
    seen_invoice = {}

    for r in raw:
        # Invoice-level paid/outstanding only on first line of each invoice
        if r.invoice in seen_invoice:
            paid_amt = 0
            outstanding = 0
            grand_total = 0
        else:
            seen_invoice[r.invoice] = True
            paid_amt = flt(r.allocated_paid)
            outstanding = flt(r.allocated_outstanding)
            grand_total = flt(r.allocated_grand_total)

        data.append({
            "posting_date": r.posting_date,
            "invoice": r.invoice,
            "status": r.status or "",
            "payment_terms_template": r.payment_terms_template or "",
            "sales_person": r.sales_person,
            "customer": r.customer,
            "customer_name": r.customer_name,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "item_group": r.item_group,
            "qty": flt(r.qty),
            "uom": r.uom,
            "rate": flt(r.rate),
            "discount_amount": flt(r.discount_amount),
            "net_total": flt(r.net_total),
            "grand_total": grand_total,
            "paid_amount": paid_amt,
            "outstanding": outstanding,
        })
    return data


def build_grouped(raw, key_field, label_field=None, extra_fields=None, compute_avg_rate=False):
    """Aggregate rows by a single key."""
    extra_fields = extra_fields or []
    groups = {}
    invoice_seen_by_group = {}

    for r in raw:
        key = r.get(key_field)
        if key is None or key == "":
            key = "Unassigned"

        if key not in groups:
            g = {
                key_field: key,
                "qty": 0,
                "gross_total": 0,
                "discount": 0,
                "net_total": 0,
                "tax": 0,
                "grand_total": 0,
                "paid_amount": 0,
                "outstanding": 0,
                "invoice_count": 0,
                "customer_count": 0,
                "item_count": 0,
                "_invoices": set(),
                "_customers": set(),
                "_items": set(),
                "_rate_sum": 0,
                "_rate_qty": 0,
            }
            if label_field:
                g[label_field] = r.get(label_field)
            for f in extra_fields:
                g[f] = r.get(f)
            groups[key] = g
            invoice_seen_by_group[key] = set()

        g = groups[key]
        g["qty"] += flt(r.qty)
        g["discount"] += flt(r.discount_amount)
        g["net_total"] += flt(r.net_total)
        g["_invoices"].add(r.invoice)
        if r.customer:
            g["_customers"].add(r.customer)
        if r.item_code:
            g["_items"].add(r.item_code)

        if compute_avg_rate:
            g["_rate_sum"] += flt(r.rate) * flt(r.qty)
            g["_rate_qty"] += flt(r.qty)

        # Invoice-level totals only once per (group, invoice)
        if r.invoice not in invoice_seen_by_group[key]:
            invoice_seen_by_group[key].add(r.invoice)
            g["grand_total"] += flt(r.allocated_grand_total)
            g["paid_amount"] += flt(r.allocated_paid)
            g["outstanding"] += flt(r.allocated_outstanding)

    # Finalize
    data = []
    for key, g in groups.items():
        g["invoice_count"] = len(g["_invoices"])
        g["customer_count"] = len(g["_customers"])
        g["item_count"] = len(g["_items"])
        g["gross_total"] = g["net_total"] + g["discount"]
        g["tax"] = g["grand_total"] - g["net_total"]
        if compute_avg_rate and g["_rate_qty"]:
            g["avg_rate"] = g["_rate_sum"] / g["_rate_qty"]
        else:
            g["avg_rate"] = 0

        for k in ("_invoices", "_customers", "_items", "_rate_sum", "_rate_qty"):
            g.pop(k, None)

        data.append(g)

    if key_field == "posting_date":
        data.sort(key=lambda x: x.get("posting_date") or "")
    else:
        data.sort(key=lambda x: flt(x.get("net_total")), reverse=True)

    return data


# ============================================================
# SUMMARY & CHART
# ============================================================

def get_report_summary(data):
    if not data:
        return []

    total_net = sum(flt(d.get("net_total")) for d in data)
    total_grand = sum(flt(d.get("grand_total")) for d in data)
    total_paid = sum(flt(d.get("paid_amount")) for d in data)
    total_outstanding = sum(flt(d.get("outstanding")) for d in data)
    total_qty = sum(flt(d.get("qty")) for d in data)

    return [
        {"value": total_net, "label": _("Net Sales"),
         "datatype": "Currency", "indicator": "blue"},
        {"value": total_grand, "label": _("Grand Total"),
         "datatype": "Currency", "indicator": "purple"},
        {"value": total_paid, "label": _("Total Paid"),
         "datatype": "Currency", "indicator": "green"},
        {"value": total_outstanding, "label": _("Outstanding"),
         "datatype": "Currency",
         "indicator": "red" if total_outstanding > 0 else "green"},
        {"value": total_qty, "label": _("Total Qty"),
         "datatype": "Float", "indicator": "grey"},
    ]


def get_chart_data(data, filters):
    if not data:
        return None

    group_by = filters.get("group_by") or "Detailed"

    if group_by == "Date":
        sorted_d = sorted(data, key=lambda x: x.get("posting_date") or "")
        return {
            "data": {
                "labels": [formatdate(d.get("posting_date")) for d in sorted_d],
                "datasets": [
                    {"name": "Net Sales", "values": [flt(d.get("net_total")) for d in sorted_d]},
                ]
            },
            "type": "line",
            "colors": ["#5e72e4"],
            "lineOptions": {"regionFill": 1, "hideDots": 0}
        }

    if group_by == "Detailed":
        totals = {}
        for r in data:
            sp = r.get("sales_person") or "Unassigned"
            totals[sp] = totals.get(sp, 0) + flt(r.get("net_total"))
        sorted_x = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "data": {
                "labels": [x[0] for x in sorted_x],
                "datasets": [{"name": "Net Sales", "values": [x[1] for x in sorted_x]}]
            },
            "type": "bar",
            "colors": ["#7ab8e8"],
        }

    label_key = (
        "sales_person" if group_by == "Route" else
        "customer" if group_by == "Customer" else
        "item_code" if group_by == "Item" else
        "item_group"
    )

    sorted_d = sorted(data, key=lambda x: flt(x.get("net_total")), reverse=True)[:10]

    return {
        "data": {
            "labels": [str(d.get(label_key) or "") for d in sorted_d],
            "datasets": [
                {"name": "Net Sales", "values": [flt(d.get("net_total")) for d in sorted_d]},
            ]
        },
        "type": "bar",
        "colors": ["#7ab8e8"],
        "barOptions": {"spaceRatio": 0.3}
    }


# ============================================================
# PDF EXPORT
# ============================================================

@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
    if isinstance(filters, str):
        filters = json.loads(filters)
    if isinstance(data, str):
        data = json.loads(data)

    company = filters.get("company")
    company_doc = frappe.get_doc("Company", company) if company else None
    currency = company_doc.default_currency if company_doc else "USD"

    letter_head = ""
    if company_doc and company_doc.default_letter_head:
        try:
            lh = frappe.get_doc("Letter Head", company_doc.default_letter_head)
            letter_head = lh.content or ""
        except Exception:
            letter_head = ""

    group_by = filters.get("group_by") or "Detailed"

    total_net = sum(flt(d.get("net_total")) for d in data)
    total_grand = sum(flt(d.get("grand_total")) for d in data)
    total_paid = sum(flt(d.get("paid_amount")) for d in data)
    total_outstanding = sum(flt(d.get("outstanding")) for d in data)
    total_qty = sum(flt(d.get("qty")) for d in data)

    # Filter summary
    filter_html = ""
    payment_status_list = filters.get("payment_status") or []
    if payment_status_list and isinstance(payment_status_list[0], dict):
        payment_status_list = [s.get("value") for s in payment_status_list]

    filter_map = [
        ("From Date", formatdate(filters.get("from_date")) if filters.get("from_date") else ""),
        ("To Date", formatdate(filters.get("to_date")) if filters.get("to_date") else ""),
        ("Company", filters.get("company") or ""),
        ("Group By", group_by),
        ("Route", filters.get("sales_person") or "All"),
        ("Customer", filters.get("customer") or "All"),
        ("Item", filters.get("item_code") or "All"),
        ("Item Group", filters.get("item_group") or "All"),
        ("Brand", filters.get("brand") or "All"),
        ("Payment Status", ", ".join(payment_status_list) or "All"),
        ("Payment Terms Template", filters.get("payment_terms_template") or "All"),
        ("Payment Term", filters.get("payment_term") or "All"),
    ]
    for label, value in filter_map:
        if value:
            filter_html += (
                '<div class="filter-item">'
                '<span class="filter-label">' + str(label) + ':</span> '
                '<span class="filter-value">' + str(value) + '</span>'
                '</div>'
            )

    headers_html, rows_html, totals_html = build_pdf_table(
        data, group_by, currency, total_qty, total_net,
        total_grand, total_paid, total_outstanding
    )

    now = format_datetime(get_datetime(), "dd MMM yyyy HH:mm")
    css = get_pdf_css()
    letter_head_html = ('<div class="letter-head">' + letter_head + '</div>') if letter_head else ''

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<title>Sales Report</title>'
        '<style>' + css + '</style></head><body>'
        + letter_head_html +
        '<div class="report-header">'
            '<div class="report-title">'
                '<h1>Sales Report</h1>'
                '<div class="subtitle">Period: ' + formatdate(filters.get('from_date')) + ' to ' + formatdate(filters.get('to_date')) +
                ' &nbsp;|&nbsp; Grouped by: ' + group_by + '</div>'
            '</div>'
            '<div class="report-meta">'
                '<div><span class="label">Company:</span> <strong>' + str(filters.get('company') or '') + '</strong></div>'
                '<div><span class="label">Generated:</span> ' + str(now) + '</div>'
                '<div><span class="label">By:</span> ' + str(frappe.session.user) + '</div>'
            '</div>'
        '</div>'

        '<div class="filters-section">'
            '<div class="filters-title">Applied Filters</div>'
            '<div class="filters-grid">' + filter_html + '</div>'
        '</div>'

        '<div class="summary-cards">'
            '<div class="summary-card card-blue">'
                '<div class="label">Net Sales</div>'
                '<div class="value">' + fmt_money(total_net, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-purple">'
                '<div class="label">Grand Total</div>'
                '<div class="value">' + fmt_money(total_grand, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-green">'
                '<div class="label">Total Paid</div>'
                '<div class="value">' + fmt_money(total_paid, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-red">'
                '<div class="label">Outstanding</div>'
                '<div class="value">' + fmt_money(total_outstanding, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-grey">'
                '<div class="label">Total Qty</div>'
                '<div class="value">' + "{:,.2f}".format(total_qty) + '</div>'
            '</div>'
        '</div>'

        '<table class="report-table"><thead>' + headers_html + '</thead>'
        '<tbody>' + rows_html + totals_html + '</tbody></table>'

        '<div class="signature-section">'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Prepared By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Verified By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Approved By</strong></div></div>'
        '</div>'

        '<div class="footer">'
            '<div>Sales Report - ' + str(filters.get('company') or '') + '</div>'
            '<div>Generated by ' + str(frappe.session.user) + ' on ' + str(now) + '</div>'
        '</div>'

        '</body></html>'
    )

    return html


def build_pdf_table(data, group_by, currency, total_qty, total_net,
                    total_grand, total_paid, total_outstanding):
    """Build headers, rows and totals based on group_by."""

    fmt = lambda v: fmt_money(v, currency=currency) if flt(v) else "-"
    qty_fmt = lambda v: "{:,.2f}".format(flt(v)) if flt(v) else "-"

    # ====== ROUTE ======
    if group_by == "Route":
        headers_html = (
            '<tr>'
            '<th class="text-center">#</th>'
            '<th>Route</th>'
            '<th class="text-center">Invoices</th>'
            '<th class="text-center">Customers</th>'
            '<th class="text-right">Qty</th>'
            '<th class="text-right">Net Total</th>'
            '<th class="text-right">Grand Total</th>'
            '<th class="text-right">Paid</th>'
            '<th class="text-right">Outstanding</th>'
            '</tr>'
        )
        rows_html = ""
        for idx, r in enumerate(data, 1):
            cls = "even" if idx % 2 == 0 else "odd"
            rows_html += (
                f'<tr class="{cls}">'
                f'<td class="text-center">{idx}</td>'
                f'<td><strong>{r.get("sales_person") or "-"}</strong></td>'
                f'<td class="text-center">{r.get("invoice_count") or 0}</td>'
                f'<td class="text-center">{r.get("customer_count") or 0}</td>'
                f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
                f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("grand_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("paid_amount"))}</td>'
                f'<td class="text-right">{fmt(r.get("outstanding"))}</td>'
                f'</tr>'
            )
        totals_html = (
            '<tr class="totals-row">'
            '<td colspan="4" class="text-right"><strong>GRAND TOTAL</strong></td>'
            f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_grand)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_paid)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_outstanding)}</strong></td>'
            '</tr>'
        )
        return headers_html, rows_html, totals_html

    # ====== CUSTOMER ======
    if group_by == "Customer":
        headers_html = (
            '<tr>'
            '<th class="text-center">#</th>'
            '<th>Customer</th>'
            '<th>Customer Name</th>'
            '<th>Group</th>'
            '<th class="text-center">Inv</th>'
            '<th class="text-right">Qty</th>'
            '<th class="text-right">Net Total</th>'
            '<th class="text-right">Grand Total</th>'
            '<th class="text-right">Paid</th>'
            '<th class="text-right">Outstanding</th>'
            '</tr>'
        )
        rows_html = ""
        for idx, r in enumerate(data, 1):
            cls = "even" if idx % 2 == 0 else "odd"
            rows_html += (
                f'<tr class="{cls}">'
                f'<td class="text-center">{idx}</td>'
                f'<td>{r.get("customer") or "-"}</td>'
                f'<td>{r.get("customer_name") or "-"}</td>'
                f'<td>{r.get("customer_group") or "-"}</td>'
                f'<td class="text-center">{r.get("invoice_count") or 0}</td>'
                f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
                f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("grand_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("paid_amount"))}</td>'
                f'<td class="text-right">{fmt(r.get("outstanding"))}</td>'
                f'</tr>'
            )
        totals_html = (
            '<tr class="totals-row">'
            '<td colspan="5" class="text-right"><strong>GRAND TOTAL</strong></td>'
            f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_grand)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_paid)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_outstanding)}</strong></td>'
            '</tr>'
        )
        return headers_html, rows_html, totals_html

    # ====== ITEM ======
    if group_by == "Item":
        headers_html = (
            '<tr>'
            '<th class="text-center">#</th>'
            '<th>Item Code</th>'
            '<th>Item Name</th>'
            '<th>Group</th>'
            '<th>Brand</th>'
            '<th>UOM</th>'
            '<th class="text-right">Qty</th>'
            '<th class="text-right">Avg Rate</th>'
            '<th class="text-right">Net Total</th>'
            '<th class="text-center">Inv</th>'
            '</tr>'
        )
        rows_html = ""
        for idx, r in enumerate(data, 1):
            cls = "even" if idx % 2 == 0 else "odd"
            rows_html += (
                f'<tr class="{cls}">'
                f'<td class="text-center">{idx}</td>'
                f'<td>{r.get("item_code") or "-"}</td>'
                f'<td>{r.get("item_name") or "-"}</td>'
                f'<td>{r.get("item_group") or "-"}</td>'
                f'<td>{r.get("brand") or "-"}</td>'
                f'<td>{r.get("uom") or "-"}</td>'
                f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
                f'<td class="text-right">{fmt(r.get("avg_rate"))}</td>'
                f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
                f'<td class="text-center">{r.get("invoice_count") or 0}</td>'
                f'</tr>'
            )
        totals_html = (
            '<tr class="totals-row">'
            '<td colspan="6" class="text-right"><strong>GRAND TOTAL</strong></td>'
            f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
            '<td></td>'
            f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
            '<td></td>'
            '</tr>'
        )
        return headers_html, rows_html, totals_html

    # ====== ITEM GROUP ======
    if group_by == "Item Group":
        headers_html = (
            '<tr>'
            '<th class="text-center">#</th>'
            '<th>Item Group</th>'
            '<th class="text-center">Items</th>'
            '<th class="text-right">Qty</th>'
            '<th class="text-right">Net Total</th>'
            '<th class="text-right">Grand Total</th>'
            '</tr>'
        )
        rows_html = ""
        for idx, r in enumerate(data, 1):
            cls = "even" if idx % 2 == 0 else "odd"
            rows_html += (
                f'<tr class="{cls}">'
                f'<td class="text-center">{idx}</td>'
                f'<td><strong>{r.get("item_group") or "-"}</strong></td>'
                f'<td class="text-center">{r.get("item_count") or 0}</td>'
                f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
                f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("grand_total"))}</td>'
                f'</tr>'
            )
        totals_html = (
            '<tr class="totals-row">'
            '<td colspan="3" class="text-right"><strong>GRAND TOTAL</strong></td>'
            f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_grand)}</strong></td>'
            '</tr>'
        )
        return headers_html, rows_html, totals_html

    # ====== DATE ======
    if group_by == "Date":
        headers_html = (
            '<tr>'
            '<th class="text-center">#</th>'
            '<th class="text-center">Date</th>'
            '<th class="text-center">Invoices</th>'
            '<th class="text-center">Customers</th>'
            '<th class="text-right">Qty</th>'
            '<th class="text-right">Net Total</th>'
            '<th class="text-right">Grand Total</th>'
            '<th class="text-right">Paid</th>'
            '<th class="text-right">Outstanding</th>'
            '</tr>'
        )
        rows_html = ""
        for idx, r in enumerate(data, 1):
            cls = "even" if idx % 2 == 0 else "odd"
            rows_html += (
                f'<tr class="{cls}">'
                f'<td class="text-center">{idx}</td>'
                f'<td class="text-center">{formatdate(r.get("posting_date")) if r.get("posting_date") else "-"}</td>'
                f'<td class="text-center">{r.get("invoice_count") or 0}</td>'
                f'<td class="text-center">{r.get("customer_count") or 0}</td>'
                f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
                f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("grand_total"))}</td>'
                f'<td class="text-right">{fmt(r.get("paid_amount"))}</td>'
                f'<td class="text-right">{fmt(r.get("outstanding"))}</td>'
                f'</tr>'
            )
        totals_html = (
            '<tr class="totals-row">'
            '<td colspan="4" class="text-right"><strong>GRAND TOTAL</strong></td>'
            f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_grand)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_paid)}</strong></td>'
            f'<td class="text-right"><strong>{fmt(total_outstanding)}</strong></td>'
            '</tr>'
        )
        return headers_html, rows_html, totals_html

    # ====== DETAILED (default) - WITH PAYMENT TERMS COLUMN ======
    headers_html = (
        '<tr>'
        '<th class="text-center">#</th>'
        '<th class="text-center">Date</th>'
        '<th>Invoice</th>'
        '<th class="text-center">Status</th>'
        '<th>Route</th>'
        '<th>Customer</th>'
        '<th>Payment Terms</th>'
        '<th>Item Code</th>'
        '<th>Item Name</th>'
        '<th class="text-right">Qty</th>'
        '<th>UOM</th>'
        '<th class="text-right">Rate</th>'
        '<th class="text-right">Disc</th>'
        '<th class="text-right">Net Amt</th>'
        '<th class="text-right">Grand Total</th>'
        '<th class="text-right">Paid</th>'
        '<th class="text-right">Outstanding</th>'
        '</tr>'
    )

    status_colors = {
        "Paid": "#7cc99a",
        "Unpaid": "#f08585",
        "Partly Paid": "#f5a96b",
        "Overdue": "#e06868",
        "Return": "#95a3b8",
        "Credit Note Issued": "#95a3b8"
    }

    rows_html = ""
    for idx, r in enumerate(data, 1):
        cls = "even" if idx % 2 == 0 else "odd"
        status = r.get("status") or ""
        status_bg = status_colors.get(status, "#95a3b8")
        status_html = (
            f'<span style="background:{status_bg};color:white;padding:2px 6px;'
            f'border-radius:3px;font-size:7pt;font-weight:600">{status}</span>'
        ) if status else "-"

        rows_html += (
            f'<tr class="{cls}">'
            f'<td class="text-center">{idx}</td>'
            f'<td class="text-center">{formatdate(r.get("posting_date")) if r.get("posting_date") else "-"}</td>'
            f'<td>{r.get("invoice") or "-"}</td>'
            f'<td class="text-center">{status_html}</td>'
            f'<td>{r.get("sales_person") or "-"}</td>'
            f'<td>{(r.get("customer_name") or r.get("customer") or "-")}</td>'
            f'<td>{r.get("payment_terms_template") or "-"}</td>'
            f'<td>{r.get("item_code") or "-"}</td>'
            f'<td>{r.get("item_name") or "-"}</td>'
            f'<td class="text-right">{qty_fmt(r.get("qty"))}</td>'
            f'<td>{r.get("uom") or "-"}</td>'
            f'<td class="text-right">{fmt(r.get("rate"))}</td>'
            f'<td class="text-right">{fmt(r.get("discount_amount"))}</td>'
            f'<td class="text-right">{fmt(r.get("net_total"))}</td>'
            f'<td class="text-right">{fmt(r.get("grand_total"))}</td>'
            f'<td class="text-right">{fmt(r.get("paid_amount"))}</td>'
            f'<td class="text-right">{fmt(r.get("outstanding"))}</td>'
            f'</tr>'
        )

    totals_html = (
        '<tr class="totals-row">'
        '<td colspan="9" class="text-right"><strong>GRAND TOTAL</strong></td>'
        f'<td class="text-right"><strong>{qty_fmt(total_qty)}</strong></td>'
        '<td></td>'
        '<td></td>'
        '<td></td>'
        f'<td class="text-right"><strong>{fmt(total_net)}</strong></td>'
        f'<td class="text-right"><strong>{fmt(total_grand)}</strong></td>'
        f'<td class="text-right"><strong>{fmt(total_paid)}</strong></td>'
        f'<td class="text-right"><strong>{fmt(total_outstanding)}</strong></td>'
        '</tr>'
    )

    return headers_html, rows_html, totals_html


def get_pdf_css():
    return """
        @page { size: A4 landscape; margin: 8mm; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 7.5pt;
               color: #2d3748; background: #fff; line-height: 1.3; }
        .letter-head { margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #5e72e4; }
        .report-header { display: flex; justify-content: space-between; align-items: flex-start;
                         margin-bottom: 10px; padding: 12px 16px;
                         background: linear-gradient(135deg, #4299e1 0%, #667eea 50%, #764ba2 100%);
                         color: white; border-radius: 8px;
                         box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .report-title h1 { font-size: 16pt; font-weight: 700; margin-bottom: 3px; letter-spacing: 0.3px; }
        .report-title .subtitle { font-size: 8.5pt; opacity: 0.92; font-weight: 400; }
        .report-meta { text-align: right; font-size: 7.5pt; }
        .report-meta .label { opacity: 0.85; }
        .filters-section { background: #f0f4ff; border-left: 4px solid #667eea;
                          padding: 8px 12px; margin-bottom: 10px; border-radius: 4px; }
        .filters-title { font-size: 8pt; font-weight: 700; color: #5e72e4;
                        margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
        .filters-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px 12px; }
        .filter-item { font-size: 7.5pt; }
        .filter-label { font-weight: 600; color: #4a5568; }
        .filter-value { color: #2d3748; }
        .summary-cards { display: grid; grid-template-columns: repeat(5, 1fr);
                        gap: 8px; margin-bottom: 10px; }
        .summary-card { padding: 10px; border-radius: 6px; color: white; text-align: center;
                        box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
        .card-blue { background: linear-gradient(135deg, #7ab8e8, #5a92d4); }
        .card-green { background: linear-gradient(135deg, #7cc99a, #5cb37e); }
        .card-orange { background: linear-gradient(135deg, #f5a96b, #e8884a); }
        .card-red { background: linear-gradient(135deg, #f08585, #e06868); }
        .card-purple { background: linear-gradient(135deg, #b08ee0, #9374c8); }
        .card-grey { background: linear-gradient(135deg, #95a3b8, #7a8aa3); }
        .summary-card .label { font-size: 7pt; opacity: 0.95; margin-bottom: 3px;
                              text-transform: uppercase; letter-spacing: 0.4px; font-weight: 500; }
        .summary-card .value { font-size: 12pt; font-weight: 700; }
        table.report-table { width: 100%; border-collapse: collapse; font-size: 6.8pt; margin-bottom: 10px; }
        table.report-table thead { background: linear-gradient(135deg, #4299e1 0%, #667eea 100%); color: white; }
        table.report-table th { padding: 6px 3px; text-align: left; font-weight: 600;
                                font-size: 6.8pt; text-transform: uppercase; letter-spacing: 0.2px;
                                border: 1px solid #5a92d4; }
        table.report-table th.text-right { text-align: right; }
        table.report-table th.text-center { text-align: center; }
        table.report-table td { padding: 4px 3px; border: 1px solid #e2e8f0; }
        table.report-table tr.odd { background: #ffffff; }
        table.report-table tr.even { background: #f7fafc; }
        table.report-table .text-right { text-align: right; }
        table.report-table .text-center { text-align: center; }
        tr.totals-row { background: linear-gradient(135deg, #4299e1, #667eea) !important; color: white; font-weight: 700; }
        tr.totals-row td { padding: 9px 3px; border-color: #5a92d4; font-size: 8pt; }
        .footer { margin-top: 14px; padding-top: 6px; border-top: 1px solid #e2e8f0;
                 display: flex; justify-content: space-between; font-size: 6.5pt; color: #718096; }
        .signature-section { display: grid; grid-template-columns: repeat(3, 1fr);
                            gap: 25px; margin-top: 30px; padding-top: 6px; }
        .signature-box { text-align: center; font-size: 7.5pt; color: #4a5568; }
        .signature-line { border-top: 1px solid #2d3748; margin: 22px 15px 4px 15px; }
        @media print {
            body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
            tr { page-break-inside: avoid; }
            thead { display: table-header-group; }
            .summary-cards { page-break-inside: avoid; }
        }
    """