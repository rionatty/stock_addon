# -*- coding: utf-8 -*-
# Copyright (c) 2024, Your Company and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from frappe.utils import flt, getdate, fmt_money, formatdate, format_datetime, get_datetime


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)

    columns = get_columns()
    data = get_data(filters)
    report_summary = get_report_summary(data)
    chart = get_chart_data(data)

    return columns, data, None, chart, report_summary


def validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are mandatory"))

    if getdate(filters.get("from_date")) > getdate(filters.get("to_date")):
        frappe.throw(_("From Date cannot be greater than To Date"))

    if not filters.get("company"):
        frappe.throw(_("Company is mandatory"))


def get_columns():
    return [
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link",
         "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
        {"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link",
         "options": "Item Group", "width": 120},
        {"label": _("Brand"), "fieldname": "brand", "fieldtype": "Link",
         "options": "Brand", "width": 100},
        {"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link",
         "options": "UOM", "width": 80},
        {"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link",
         "options": "Warehouse", "width": 160},
        {"label": _("Selling Rate"), "fieldname": "selling_rate", "fieldtype": "Currency", "width": 110},
        {"label": _("Opening Qty"), "fieldname": "opening_qty", "fieldtype": "Float",
         "width": 110, "precision": 2},
        {"label": _("Opening Value"), "fieldname": "opening_value", "fieldtype": "Currency", "width": 120},
        {"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float",
         "width": 100, "precision": 2},
        {"label": _("In Value"), "fieldname": "in_value", "fieldtype": "Currency", "width": 120},
        {"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float",
         "width": 100, "precision": 2},
        {"label": _("Out Value"), "fieldname": "out_value", "fieldtype": "Currency", "width": 120},
        {"label": _("Closing Qty"), "fieldname": "closing_qty", "fieldtype": "Float",
         "width": 110, "precision": 2},
        {"label": _("Closing Value"), "fieldname": "closing_value", "fieldtype": "Currency", "width": 130}
    ]


def get_data(filters):
    item_codes = get_filtered_items(filters)

    if filters.get("item_code") or filters.get("item_group") or filters.get("brand"):
        if not item_codes:
            return []

    opening = get_opening_balance(filters, item_codes)
    movements = get_period_movements(filters, item_codes)

    result = {}

    for row in opening:
        key = (row.item_code, row.warehouse)
        result[key] = {
            "item_code": row.item_code,
            "warehouse": row.warehouse,
            "opening_qty": flt(row.opening_qty),
            "in_qty": 0.0,
            "out_qty": 0.0,
        }

    for row in movements:
        key = (row.item_code, row.warehouse)
        if key not in result:
            result[key] = {
                "item_code": row.item_code,
                "warehouse": row.warehouse,
                "opening_qty": 0.0,
                "in_qty": 0.0,
                "out_qty": 0.0,
            }
        result[key]["in_qty"] = flt(row.in_qty)
        result[key]["out_qty"] = flt(row.out_qty)

    item_list = list({k[0] for k in result.keys()})

    item_details = get_item_details(item_list)
    selling_prices = get_selling_prices(
        item_list,
        filters.get("price_list") or "Standard Selling",
        filters.get("to_date")
    )

    data = []
    show_zero = filters.get("show_zero_balance")

    for key, row in result.items():
        item_info = item_details.get(row["item_code"], {})
        row["item_name"] = item_info.get("item_name")
        row["item_group"] = item_info.get("item_group")
        row["brand"] = item_info.get("brand")
        row["stock_uom"] = item_info.get("stock_uom")

        selling_rate = flt(selling_prices.get(row["item_code"], 0))
        row["selling_rate"] = selling_rate

        row["closing_qty"] = flt(
            row["opening_qty"] + row["in_qty"] - row["out_qty"], 2
        )

        row["opening_value"] = flt(row["opening_qty"] * selling_rate, 2)
        row["in_value"] = flt(row["in_qty"] * selling_rate, 2)
        row["out_value"] = flt(row["out_qty"] * selling_rate, 2)
        row["closing_value"] = flt(row["closing_qty"] * selling_rate, 2)

        has_activity = (
            row["opening_qty"] or row["in_qty"]
            or row["out_qty"] or row["closing_qty"]
        )
        if has_activity or show_zero:
            data.append(row)

    data.sort(key=lambda x: (x["warehouse"] or "", x["item_code"] or ""))
    return data


def get_filtered_items(filters):
    item_filters = {"disabled": 0}

    if filters.get("item_code"):
        item_filters["name"] = filters["item_code"]

    if filters.get("brand"):
        item_filters["brand"] = filters["brand"]

    if filters.get("item_group"):
        lft, rgt = frappe.db.get_value(
            "Item Group", filters["item_group"], ["lft", "rgt"]
        )
        if lft is not None:
            descendant_groups = frappe.db.sql_list("""
                SELECT name FROM `tabItem Group`
                WHERE lft >= %s AND rgt <= %s
            """, (lft, rgt))
            item_filters["item_group"] = ["in", descendant_groups]

    items = frappe.get_all("Item", filters=item_filters, pluck="name")
    return items


def get_opening_balance(filters, item_codes=None):
    base_conditions = " AND sle.is_cancelled = 0 AND sle.docstatus < 2 "

    if filters.get("company"):
        base_conditions += " AND sle.company = %(company)s "
    if filters.get("warehouse"):
        base_conditions += " AND sle.warehouse = %(warehouse)s "

    item_filter_outer = ""
    query_params = {
        "from_date": filters.get("from_date"),
        "company": filters.get("company"),
        "warehouse": filters.get("warehouse"),
    }

    if item_codes:
        item_codes_str = ", ".join([f"%(item_{i})s" for i in range(len(item_codes))])
        item_filter_outer = f" AND sle2.item_code IN ({item_codes_str}) "
        for i, code in enumerate(item_codes):
            query_params[f"item_{i}"] = code

    query = f"""
        SELECT
            sle.item_code,
            sle.warehouse,
            sle.qty_after_transaction AS opening_qty
        FROM `tabStock Ledger Entry` sle
        INNER JOIN (
            SELECT
                sle2.item_code,
                sle2.warehouse,
                MAX(sle2.posting_datetime) AS max_dt
            FROM `tabStock Ledger Entry` sle2
            WHERE sle2.posting_date < %(from_date)s
              AND sle2.is_cancelled = 0
              AND sle2.docstatus < 2
              {('AND sle2.company = %(company)s ' if filters.get("company") else '')}
              {('AND sle2.warehouse = %(warehouse)s ' if filters.get("warehouse") else '')}
              {item_filter_outer}
            GROUP BY sle2.item_code, sle2.warehouse
        ) latest
            ON sle.item_code = latest.item_code
           AND sle.warehouse = latest.warehouse
           AND sle.posting_datetime = latest.max_dt
        WHERE 1=1 {base_conditions}
    """

    return frappe.db.sql(query, query_params, as_dict=1)


def get_period_movements(filters, item_codes=None):
    conditions = " AND sle.is_cancelled = 0 AND sle.docstatus < 2 "
    query_params = {
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
        "company": filters.get("company"),
        "warehouse": filters.get("warehouse"),
    }

    if filters.get("company"):
        conditions += " AND sle.company = %(company)s "
    if filters.get("warehouse"):
        conditions += " AND sle.warehouse = %(warehouse)s "

    if item_codes:
        placeholders = ", ".join([f"%(item_{i})s" for i in range(len(item_codes))])
        conditions += f" AND sle.item_code IN ({placeholders}) "
        for i, code in enumerate(item_codes):
            query_params[f"item_{i}"] = code

    query = f"""
        SELECT
            sle.item_code,
            sle.warehouse,
            SUM(CASE WHEN sle.actual_qty > 0
                THEN sle.actual_qty ELSE 0 END) AS in_qty,
            SUM(CASE WHEN sle.actual_qty < 0
                THEN ABS(sle.actual_qty) ELSE 0 END) AS out_qty
        FROM `tabStock Ledger Entry` sle
        WHERE sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
        {conditions}
        GROUP BY sle.item_code, sle.warehouse
    """

    return frappe.db.sql(query, query_params, as_dict=1)


def get_item_details(item_codes):
    if not item_codes:
        return {}

    items = frappe.get_all(
        "Item",
        filters={"name": ["in", item_codes]},
        fields=["name", "item_name", "item_group", "brand", "stock_uom"]
    )
    return {item.name: item for item in items}


def get_selling_prices(item_codes, price_list, as_on_date=None):
    if not item_codes:
        return {}

    if not as_on_date:
        as_on_date = getdate()

    placeholders = ", ".join(["%s"] * len(item_codes))
    params = list(item_codes) + [price_list, as_on_date, as_on_date]

    query = f"""
        SELECT
            ip.item_code,
            ip.price_list_rate
        FROM `tabItem Price` ip
        INNER JOIN (
            SELECT
                item_code,
                MAX(valid_from) AS max_valid_from
            FROM `tabItem Price`
            WHERE item_code IN ({placeholders})
              AND price_list = %s
              AND selling = 1
              AND (valid_from IS NULL OR valid_from <= %s)
              AND (valid_upto IS NULL OR valid_upto >= %s)
            GROUP BY item_code
        ) latest
            ON ip.item_code = latest.item_code
           AND (ip.valid_from = latest.max_valid_from
                OR (ip.valid_from IS NULL AND latest.max_valid_from IS NULL))
        WHERE ip.price_list = %s
          AND ip.selling = 1
    """
    params.append(price_list)

    rows = frappe.db.sql(query, params, as_dict=1)

    result = {row.item_code: flt(row.price_list_rate) for row in rows}

    missing = [code for code in item_codes if code not in result]
    if missing:
        placeholders2 = ", ".join(["%s"] * len(missing))
        fallback_query = f"""
            SELECT item_code, price_list_rate, valid_from
            FROM `tabItem Price`
            WHERE item_code IN ({placeholders2})
              AND price_list = %s
              AND selling = 1
            ORDER BY valid_from DESC
        """
        fallback_rows = frappe.db.sql(
            fallback_query,
            list(missing) + [price_list],
            as_dict=1
        )
        for row in fallback_rows:
            if row.item_code not in result:
                result[row.item_code] = flt(row.price_list_rate)

    return result


def get_report_summary(data):
    if not data:
        return []

    total_opening_value = sum(flt(d.get("opening_value")) for d in data)
    total_in_value = sum(flt(d.get("in_value")) for d in data)
    total_out_value = sum(flt(d.get("out_value")) for d in data)
    total_closing_value = sum(flt(d.get("closing_value")) for d in data)

    return [
        {"value": total_opening_value, "label": _("Opening Value"),
         "datatype": "Currency", "indicator": "blue"},
        {"value": total_in_value, "label": _("Total In Value"),
         "datatype": "Currency", "indicator": "green"},
        {"value": total_out_value, "label": _("Total Out Value"),
         "datatype": "Currency", "indicator": "orange"},
        {"value": total_closing_value, "label": _("Closing Value"),
         "datatype": "Currency", "indicator": "purple"},
        {"value": len(data), "label": _("Total Items"),
         "datatype": "Int", "indicator": "grey"}
    ]


def get_chart_data(data):
    if not data:
        return None

    sorted_data = sorted(
        data, key=lambda x: flt(x.get("closing_value")), reverse=True
    )[:10]

    return {
        "data": {
            "labels": [d.get("item_name") or d.get("item_code") for d in sorted_data],
            "datasets": [
                {
                    "name": _("Closing Value"),
                    "values": [flt(d.get("closing_value")) for d in sorted_data]
                }
            ]
        },
        "type": "bar",
        "colors": ["#5e64ff"],
        "barOptions": {"spaceRatio": 0.3}
    }


# ============================================================
# PDF EXPORT - Beautiful Print Format
# ============================================================

@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
    """Generate beautiful HTML for PDF printing."""

    if isinstance(filters, str):
        filters = json.loads(filters)
    if isinstance(data, str):
        data = json.loads(data)

    # Filter out auto-added "Total" row from Script Report (add_total_row)
    data = [d for d in data if d.get("item_code") and d.get("item_code") != "Total"]

    company = filters.get("company")
    company_doc = frappe.get_doc("Company", company) if company else None
    currency = company_doc.default_currency if company_doc else "USD"

    # Letter head
    letter_head = ""
    if company_doc and company_doc.default_letter_head:
        try:
            lh = frappe.get_doc("Letter Head", company_doc.default_letter_head)
            letter_head = lh.content or ""
        except Exception:
            letter_head = ""

    # Compute totals
    total_opening_qty = sum(flt(d.get("opening_qty")) for d in data)
    total_opening_value = sum(flt(d.get("opening_value")) for d in data)
    total_in_qty = sum(flt(d.get("in_qty")) for d in data)
    total_in_value = sum(flt(d.get("in_value")) for d in data)
    total_out_qty = sum(flt(d.get("out_qty")) for d in data)
    total_out_value = sum(flt(d.get("out_value")) for d in data)
    total_closing_qty = sum(flt(d.get("closing_qty")) for d in data)
    total_closing_value = sum(flt(d.get("closing_value")) for d in data)

    # Build filter summary
    filter_html = ""
    filter_map = [
        ("From Date", formatdate(filters.get("from_date")) if filters.get("from_date") else ""),
        ("To Date", formatdate(filters.get("to_date")) if filters.get("to_date") else ""),
        ("Company", filters.get("company") or ""),
        ("Warehouse", filters.get("warehouse") or "All"),
        ("Price List", filters.get("price_list") or "Standard Selling"),
        ("Item Group", filters.get("item_group") or "All"),
        ("Brand", filters.get("brand") or "All"),
    ]
    for label, value in filter_map:
        if value:
            filter_html += (
                '<div class="filter-item">'
                '<span class="filter-label">' + str(label) + ':</span> '
                '<span class="filter-value">' + str(value) + '</span>'
                '</div>'
            )

    # Build table rows
    rows_html = ""
    for idx, row in enumerate(data, 1):
        row_class = "even" if idx % 2 == 0 else "odd"
        closing_qty_class = "negative" if flt(row.get("closing_qty")) < 0 else ""

        rows_html += (
            '<tr class="' + row_class + '">'
            '<td class="text-center">' + str(idx) + '</td>'
            '<td><strong>' + str(row.get('item_code') or '') + '</strong></td>'
            '<td>' + str(row.get('item_name') or '') + '</td>'
            '<td class="text-muted">' + str(row.get('item_group') or '') + '</td>'
            '<td class="text-muted">' + str(row.get('brand') or '') + '</td>'
            '<td class="text-center">' + str(row.get('stock_uom') or '') + '</td>'
            '<td class="text-right">' + fmt_money(row.get('selling_rate'), currency=currency) + '</td>'
            '<td class="text-right">' + f"{flt(row.get('opening_qty')):,.2f}" + '</td>'
            '<td class="text-right">' + fmt_money(row.get('opening_value'), currency=currency) + '</td>'
            '<td class="text-right qty-in">' + f"{flt(row.get('in_qty')):,.2f}" + '</td>'
            '<td class="text-right qty-in">' + fmt_money(row.get('in_value'), currency=currency) + '</td>'
            '<td class="text-right qty-out">' + f"{flt(row.get('out_qty')):,.2f}" + '</td>'
            '<td class="text-right qty-out">' + fmt_money(row.get('out_value'), currency=currency) + '</td>'
            '<td class="text-right ' + closing_qty_class + '"><strong>' + f"{flt(row.get('closing_qty')):,.2f}" + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(row.get('closing_value'), currency=currency) + '</strong></td>'
            '</tr>'
        )

    # Build totals row
    totals_html = (
        '<tr class="totals-row">'
        '<td colspan="7" class="text-right"><strong>TOTAL</strong></td>'
        '<td class="text-right"><strong>' + f"{total_opening_qty:,.2f}" + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_opening_value, currency=currency) + '</strong></td>'
        '<td class="text-right qty-in"><strong>' + f"{total_in_qty:,.2f}" + '</strong></td>'
        '<td class="text-right qty-in"><strong>' + fmt_money(total_in_value, currency=currency) + '</strong></td>'
        '<td class="text-right qty-out"><strong>' + f"{total_out_qty:,.2f}" + '</strong></td>'
        '<td class="text-right qty-out"><strong>' + fmt_money(total_out_value, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + f"{total_closing_qty:,.2f}" + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_closing_value, currency=currency) + '</strong></td>'
        '</tr>'
    )

    # Use format_datetime for timestamps with hours/minutes
    now = format_datetime(get_datetime(), "dd MMM yyyy HH:mm")

    css = """
        @page { size: A4 landscape; margin: 12mm; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 9pt;
               color: #2d3748; background: #fff; line-height: 1.4; }
        .letter-head { margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #2490ef; }
        .report-header { display: flex; justify-content: space-between; align-items: flex-start;
                         margin-bottom: 15px; padding: 12px;
                         background: linear-gradient(135deg, #2490ef 0%, #5e64ff 100%);
                         color: white; border-radius: 6px; }
        .report-title h1 { font-size: 18pt; font-weight: 700; margin-bottom: 4px; }
        .report-title .subtitle { font-size: 9pt; opacity: 0.9; }
        .report-meta { text-align: right; font-size: 8pt; }
        .report-meta .label { opacity: 0.85; }
        .filters-section { background: #f7fafc; border-left: 4px solid #2490ef;
                          padding: 10px 14px; margin-bottom: 15px; border-radius: 4px; }
        .filters-title { font-size: 9pt; font-weight: 700; color: #2490ef;
                        margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
        .filters-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px 16px; }
        .filter-item { font-size: 8pt; }
        .filter-label { font-weight: 600; color: #4a5568; }
        .filter-value { color: #2d3748; }
        .summary-cards { display: grid; grid-template-columns: repeat(5, 1fr);
                        gap: 10px; margin-bottom: 15px; }
        .summary-card { padding: 10px 12px; border-radius: 6px; color: white; text-align: center; }
        .card-blue { background: linear-gradient(135deg, #4299e1, #2b6cb0); }
        .card-green { background: linear-gradient(135deg, #48bb78, #2f855a); }
        .card-orange { background: linear-gradient(135deg, #ed8936, #c05621); }
        .card-purple { background: linear-gradient(135deg, #9f7aea, #6b46c1); }
        .card-grey { background: linear-gradient(135deg, #718096, #4a5568); }
        .summary-card .label { font-size: 8pt; opacity: 0.95; margin-bottom: 3px;
                              text-transform: uppercase; letter-spacing: 0.5px; }
        .summary-card .value { font-size: 13pt; font-weight: 700; }
        table.report-table { width: 100%; border-collapse: collapse; font-size: 8pt; margin-bottom: 15px; }
        table.report-table thead { background: #2d3748; color: white; }
        table.report-table th { padding: 8px 6px; text-align: left; font-weight: 600;
                                font-size: 8pt; text-transform: uppercase; letter-spacing: 0.3px;
                                border: 1px solid #1a202c; }
        table.report-table th.text-right { text-align: right; }
        table.report-table th.text-center { text-align: center; }
        table.report-table td { padding: 6px; border: 1px solid #e2e8f0; }
        table.report-table tr.odd { background: #ffffff; }
        table.report-table tr.even { background: #f7fafc; }
        table.report-table .text-right { text-align: right; }
        table.report-table .text-center { text-align: center; }
        table.report-table .text-muted { color: #718096; font-size: 7.5pt; }
        .qty-in { color: #2f855a; }
        .qty-out { color: #c53030; }
        .negative { color: #c53030; }
        tr.totals-row { background: #edf2f7 !important; font-weight: 700;
                       border-top: 2px solid #2d3748; }
        tr.totals-row td { padding: 10px 6px; border-color: #2d3748; font-size: 9pt; }
        .footer { margin-top: 20px; padding-top: 10px; border-top: 1px solid #e2e8f0;
                 display: flex; justify-content: space-between; font-size: 7.5pt; color: #718096; }
        .signature-section { display: grid; grid-template-columns: repeat(3, 1fr);
                            gap: 30px; margin-top: 40px; padding-top: 10px; }
        .signature-box { text-align: center; font-size: 8pt; }
        .signature-line { border-top: 1px solid #2d3748; margin: 30px 20px 4px 20px; }
        @media print {
            .no-print { display: none; }
            body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
            tr { page-break-inside: avoid; }
            thead { display: table-header-group; }
            .summary-cards { page-break-inside: avoid; }
        }
    """

    letter_head_html = ('<div class="letter-head">' + letter_head + '</div>') if letter_head else ''

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<title>Van Stock Movement Report</title>'
        '<style>' + css + '</style></head><body>'
        + letter_head_html +
        '<div class="report-header">'
            '<div class="report-title">'
                '<h1>Van Stock Movement Report</h1>'
                '<div class="subtitle">Period: ' + formatdate(filters.get('from_date')) + ' to ' + formatdate(filters.get('to_date')) + '</div>'
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
                '<div class="label">Opening Value</div>'
                '<div class="value">' + fmt_money(total_opening_value, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-green">'
                '<div class="label">Total In</div>'
                '<div class="value">' + fmt_money(total_in_value, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-orange">'
                '<div class="label">Total Out</div>'
                '<div class="value">' + fmt_money(total_out_value, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-purple">'
                '<div class="label">Closing Value</div>'
                '<div class="value">' + fmt_money(total_closing_value, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-grey">'
                '<div class="label">Total Items</div>'
                '<div class="value">' + str(len(data)) + '</div>'
            '</div>'
        '</div>'

        '<table class="report-table"><thead><tr>'
            '<th class="text-center">#</th>'
            '<th>Item Code</th>'
            '<th>Item Name</th>'
            '<th>Group</th>'
            '<th>Brand</th>'
            '<th class="text-center">UOM</th>'
            '<th class="text-right">Rate</th>'
            '<th class="text-right">Open Qty</th>'
            '<th class="text-right">Open Value</th>'
            '<th class="text-right">In Qty</th>'
            '<th class="text-right">In Value</th>'
            '<th class="text-right">Out Qty</th>'
            '<th class="text-right">Out Value</th>'
            '<th class="text-right">Close Qty</th>'
            '<th class="text-right">Close Value</th>'
        '</tr></thead><tbody>' + rows_html + totals_html + '</tbody></table>'

        '<div class="signature-section">'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Prepared By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Verified By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Approved By</strong></div></div>'
        '</div>'

        '<div class="footer">'
            '<div>Van Stock Movement Report - ' + str(filters.get('company') or '') + '</div>'
            '<div>Generated by ' + str(frappe.session.user) + ' on ' + str(now) + '</div>'
        '</div>'

        '</body></html>'
    )

    return html