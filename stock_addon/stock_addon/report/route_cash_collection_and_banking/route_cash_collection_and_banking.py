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
        {"label": _("Route"), "fieldname": "sales_person", "fieldtype": "Link",
         "options": "Sales Person", "width": 160},
        {"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
         "options": "Employee", "width": 110},
        {"label": _("Employee Name"), "fieldname": "employee_name",
         "fieldtype": "Data", "width": 160},
        {"label": _("User"), "fieldname": "user", "fieldtype": "Link",
         "options": "User", "width": 170},
        {"label": _("Cash Account"), "fieldname": "cash_account",
         "fieldtype": "Link", "options": "Account", "width": 220},
        {"label": _("Date"), "fieldname": "posting_date",
         "fieldtype": "Date", "width": 95},
        {"label": _("Type"), "fieldname": "entry_type",
         "fieldtype": "Data", "width": 100},
        {"label": _("Reference"), "fieldname": "reference",
         "fieldtype": "Dynamic Link", "options": "reference_doctype", "width": 150},
        {"label": _("Doctype"), "fieldname": "reference_doctype",
         "fieldtype": "Data", "width": 110, "hidden": 1},
        {"label": _("Party / Description"), "fieldname": "party",
         "fieldtype": "Data", "width": 200},
        {"label": _("Mode of Payment"), "fieldname": "mode_of_payment",
         "fieldtype": "Link", "options": "Mode of Payment", "width": 120},
        {"label": _("Collected"), "fieldname": "collected",
         "fieldtype": "Currency", "width": 120},
        {"label": _("Banked"), "fieldname": "banked",
         "fieldtype": "Currency", "width": 120},
        {"label": _("Bank Account"), "fieldname": "bank_account",
         "fieldtype": "Link", "options": "Account", "width": 200},
        {"label": _("Running Balance"), "fieldname": "running_balance",
         "fieldtype": "Currency", "width": 130},
    ]


# ============================================================
# DATA FETCHING
# ============================================================

def get_routes(filters):
    """Pull Sales Persons with their custom_cash_account, employee, user."""
    conditions = ""
    params = {}

    if filters.get("sales_person"):
        conditions += " AND sp.name = %(sales_person)s"
        params["sales_person"] = filters.get("sales_person")

    if filters.get("employee"):
        conditions += " AND sp.employee = %(employee)s"
        params["employee"] = filters.get("employee")

    query = """
        SELECT
            sp.name                   AS sales_person,
            sp.sales_person_name      AS route_name,
            sp.employee               AS employee,
            sp.custom_cash_account    AS cash_account,
            emp.employee_name         AS employee_name,
            emp.user_id               AS user
        FROM `tabSales Person` sp
        LEFT JOIN `tabEmployee` emp ON emp.name = sp.employee
        WHERE COALESCE(sp.enabled, 1) = 1
            AND sp.custom_cash_account IS NOT NULL
            AND sp.custom_cash_account != ''
            {conditions}
        ORDER BY sp.name
    """.format(conditions=conditions)

    routes = frappe.db.sql(query, params, as_dict=1) or []

    # Filter by user post-fetch (joined column)
    if filters.get("user"):
        routes = [r for r in routes if r.user == filters.get("user")]

    return routes


def get_gl_entries(cash_account, company, from_date, to_date):
    """All GL movements on the route's cash account in period."""
    return frappe.db.sql("""
        SELECT
            gle.posting_date,
            gle.voucher_type,
            gle.voucher_no,
            gle.debit,
            gle.credit,
            gle.against,
            gle.party_type,
            gle.party,
            gle.remarks
        FROM `tabGL Entry` gle
        WHERE gle.account = %(account)s
            AND gle.company = %(company)s
            AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND gle.is_cancelled = 0
        ORDER BY gle.posting_date, gle.creation
    """, {
        "account": cash_account,
        "company": company,
        "from_date": from_date,
        "to_date": to_date
    }, as_dict=1) or []


def enrich_entry(gle, cash_account):
    """Get mode of payment, party, bank account based on voucher type."""
    mode_of_payment = ""
    party_label = gle.party or ""
    bank_account = ""

    if gle.voucher_type == "Payment Entry":
        pe = frappe.db.get_value(
            "Payment Entry", gle.voucher_no,
            ["mode_of_payment", "party_name", "paid_from", "paid_to"],
            as_dict=1
        )
        if pe:
            mode_of_payment = pe.mode_of_payment or ""
            if not party_label:
                party_label = pe.party_name or ""
            if flt(gle.credit) > 0:
                bank_account = pe.paid_to or ""
            elif flt(gle.debit) > 0 and not party_label:
                party_label = pe.paid_from or ""

    elif gle.voucher_type == "Journal Entry":
        if flt(gle.credit) > 0:
            other = frappe.db.sql("""
                SELECT account FROM `tabJournal Entry Account`
                WHERE parent = %s AND account != %s AND debit > 0
                LIMIT 1
            """, (gle.voucher_no, cash_account))
            if other:
                bank_account = other[0][0]
        if not party_label:
            party_label = (gle.remarks or "")[:100]

    else:
        if not party_label:
            party_label = (gle.remarks or gle.against or "")[:100]

    return mode_of_payment, party_label, bank_account


def get_data(filters):
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))
    company = filters.get("company")
    show_zero = filters.get("show_zero_routes")

    routes = get_routes(filters)
    if not routes:
        return []

    data = []

    for route in routes:
        gl_entries = get_gl_entries(
            route.cash_account, company, from_date, to_date
        )

        if not gl_entries:
            if show_zero:
                data.append({
                    "sales_person": route.sales_person,
                    "employee": route.employee,
                    "employee_name": route.employee_name,
                    "user": route.user,
                    "cash_account": route.cash_account,
                    "entry_type": "No Activity",
                    "collected": 0,
                    "banked": 0,
                    "running_balance": 0,
                })
            continue

        # Route header marker (used for grouping in PDF)
        data.append({
            "sales_person": route.sales_person,
            "employee": route.employee,
            "employee_name": route.employee_name,
            "user": route.user,
            "cash_account": route.cash_account,
            "entry_type": "ROUTE",
        })

        running = 0.0
        route_collected = 0.0
        route_banked = 0.0

        for gle in gl_entries:
            collected = flt(gle.debit)
            banked = flt(gle.credit)
            running += collected - banked
            route_collected += collected
            route_banked += banked

            mode_of_payment, party_label, bank_account = enrich_entry(
                gle, route.cash_account
            )

            entry_type = "Collection" if collected > 0 else "Banking"

            data.append({
                "sales_person": route.sales_person,
                "employee": route.employee,
                "employee_name": route.employee_name,
                "user": route.user,
                "cash_account": route.cash_account,
                "posting_date": gle.posting_date,
                "entry_type": entry_type,
                "reference": gle.voucher_no,
                "reference_doctype": gle.voucher_type,
                "party": party_label,
                "mode_of_payment": mode_of_payment,
                "collected": collected,
                "banked": banked,
                "bank_account": bank_account,
                "running_balance": running,
            })

        # Route subtotal
        data.append({
            "sales_person": route.sales_person,
            "employee_name": "-- ROUTE TOTAL --",
            "entry_type": "SUBTOTAL",
            "collected": route_collected,
            "banked": route_banked,
            "running_balance": route_collected - route_banked,
        })

    return data


# ============================================================
# SUMMARY & CHART
# ============================================================

def get_report_summary(data):
    if not data:
        return []

    detail = [d for d in data if d.get("entry_type") in ("Collection", "Banking")]

    total_collected = sum(flt(d.get("collected")) for d in detail)
    total_banked = sum(flt(d.get("banked")) for d in detail)
    cash_on_hand = total_collected - total_banked

    routes_active = len(set(d.get("sales_person") for d in detail))
    txn_count = len(detail)

    return [
        {"value": total_collected, "label": _("Total Collected"),
         "datatype": "Currency", "indicator": "green"},
        {"value": total_banked, "label": _("Total Banked"),
         "datatype": "Currency", "indicator": "blue"},
        {"value": cash_on_hand, "label": _("Cash On Hand"),
         "datatype": "Currency",
         "indicator": "orange" if cash_on_hand > 0 else "green"},
        {"value": routes_active, "label": _("Active Routes"),
         "datatype": "Int", "indicator": "purple"},
        {"value": txn_count, "label": _("Transactions"),
         "datatype": "Int", "indicator": "grey"},
    ]


def get_chart_data(data):
    if not data:
        return None

    detail = [d for d in data if d.get("entry_type") in ("Collection", "Banking")]
    if not detail:
        return None

    totals = {}
    for row in detail:
        sp = row.get("sales_person") or "Unassigned"
        totals.setdefault(sp, {"collected": 0, "banked": 0})
        totals[sp]["collected"] += flt(row.get("collected"))
        totals[sp]["banked"] += flt(row.get("banked"))

    sorted_routes = sorted(
        totals.items(),
        key=lambda x: x[1]["collected"],
        reverse=True
    )[:10]

    return {
        "data": {
            "labels": [r[0] for r in sorted_routes],
            "datasets": [
                {"name": "Collected", "values": [r[1]["collected"] for r in sorted_routes]},
                {"name": "Banked", "values": [r[1]["banked"] for r in sorted_routes]},
            ]
        },
        "type": "bar",
        "colors": ["#7cc99a", "#7ab8e8"],
        "barOptions": {"stacked": 0, "spaceRatio": 0.3}
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

    # Group data by route
    by_route = {}
    for row in data:
        et = row.get("entry_type")

        if et == "ROUTE":
            sp = row.get("sales_person")
            if sp and sp not in by_route:
                by_route[sp] = {
                    "route": sp,
                    "employee": row.get("employee"),
                    "employee_name": row.get("employee_name"),
                    "user": row.get("user"),
                    "cash_account": row.get("cash_account"),
                    "rows": [],
                    "totals": {"collected": 0, "banked": 0}
                }
            continue

        if et not in ("Collection", "Banking"):
            continue

        sp = row.get("sales_person") or "Unassigned"
        if sp not in by_route:
            by_route[sp] = {
                "route": sp,
                "employee": row.get("employee"),
                "employee_name": row.get("employee_name"),
                "user": row.get("user"),
                "cash_account": row.get("cash_account"),
                "rows": [],
                "totals": {"collected": 0, "banked": 0}
            }
        by_route[sp]["rows"].append(row)
        by_route[sp]["totals"]["collected"] += flt(row.get("collected"))
        by_route[sp]["totals"]["banked"] += flt(row.get("banked"))

    total_collected = sum(r["totals"]["collected"] for r in by_route.values())
    total_banked = sum(r["totals"]["banked"] for r in by_route.values())
    total_balance = total_collected - total_banked

    # Filter summary
    filter_html = ""
    filter_map = [
        ("From Date", formatdate(filters.get("from_date")) if filters.get("from_date") else ""),
        ("To Date", formatdate(filters.get("to_date")) if filters.get("to_date") else ""),
        ("Company", filters.get("company") or ""),
        ("Route", filters.get("sales_person") or "All"),
        ("Employee", filters.get("employee") or "All"),
        ("User", filters.get("user") or "All"),
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
    for sp, info in by_route.items():
        if not info["rows"]:
            continue
        route_balance = info["totals"]["collected"] - info["totals"]["banked"]

        rows_html += (
            '<tr class="route-header">'
            '<td colspan="9">'
            '<strong>ROUTE: ' + str(info["route"]) + '</strong> &nbsp;|&nbsp; '
            'Employee: <strong>' + str(info["employee"] or "-") + '</strong> '
            '(' + str(info["employee_name"] or "-") + ') &nbsp;|&nbsp; '
            'User: <strong>' + str(info["user"] or "-") + '</strong> &nbsp;|&nbsp; '
            'Cash A/c: ' + str(info["cash_account"] or "-") +
            '</td>'
            '</tr>'
        )

        running = 0.0
        for idx, row in enumerate(info["rows"], 1):
            running += flt(row.get("collected")) - flt(row.get("banked"))
            row_class = "even" if idx % 2 == 0 else "odd"
            entry_type = row.get("entry_type") or ""
            type_color = "#5cb37e" if entry_type == "Collection" else "#e8884a"
            type_badge = (
                f'<span style="background:{type_color};color:white;'
                f'padding:2px 6px;border-radius:3px;font-size:7pt;'
                f'font-weight:600">{entry_type}</span>'
            )

            rows_html += (
                '<tr class="' + row_class + '">'
                '<td class="text-center">' + str(idx) + '</td>'
                '<td class="text-center">' + (formatdate(row.get('posting_date')) if row.get('posting_date') else '-') + '</td>'
                '<td class="text-center">' + type_badge + '</td>'
                '<td>' + str(row.get('reference') or '-') + '</td>'
                '<td>' + str(row.get('party') or '-') + '</td>'
                '<td>' + str(row.get('mode_of_payment') or '-') + '</td>'
                '<td class="text-right collected-col">' + (fmt_money(row.get('collected'), currency=currency) if flt(row.get('collected')) else '-') + '</td>'
                '<td class="text-right banked-col">' + (fmt_money(row.get('banked'), currency=currency) if flt(row.get('banked')) else '-') + '</td>'
                '<td class="text-right"><strong>' + fmt_money(running, currency=currency) + '</strong></td>'
                '</tr>'
            )

        rows_html += (
            '<tr class="route-subtotal">'
            '<td colspan="6" class="text-right"><strong>ROUTE TOTAL: ' + str(sp) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(info["totals"]["collected"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(info["totals"]["banked"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_balance, currency=currency) + '</strong></td>'
            '</tr>'
        )

    totals_html = (
        '<tr class="totals-row">'
        '<td colspan="6" class="text-right"><strong>GRAND TOTAL</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_collected, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_banked, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_balance, currency=currency) + '</strong></td>'
        '</tr>'
    )

    now = format_datetime(get_datetime(), "dd MMM yyyy HH:mm")

    css = """
        @page { size: A4 landscape; margin: 8mm; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 8pt;
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
        .card-purple { background: linear-gradient(135deg, #b08ee0, #9374c8); }
        .card-grey { background: linear-gradient(135deg, #95a3b8, #7a8aa3); }
        .summary-card .label { font-size: 7pt; opacity: 0.95; margin-bottom: 3px;
                              text-transform: uppercase; letter-spacing: 0.4px; font-weight: 500; }
        .summary-card .value { font-size: 12pt; font-weight: 700; }
        table.report-table { width: 100%; border-collapse: collapse; font-size: 7.5pt; margin-bottom: 10px; }
        table.report-table thead { background: linear-gradient(135deg, #4299e1 0%, #667eea 100%); color: white; }
        table.report-table th { padding: 6px 4px; text-align: left; font-weight: 600;
                                font-size: 7.5pt; text-transform: uppercase; letter-spacing: 0.2px;
                                border: 1px solid #5a92d4; }
        table.report-table th.text-right { text-align: right; }
        table.report-table th.text-center { text-align: center; }
        table.report-table td { padding: 4px; border: 1px solid #e2e8f0; }
        table.report-table tr.odd { background: #ffffff; }
        table.report-table tr.even { background: #f7fafc; }
        table.report-table tr.route-header { background: linear-gradient(135deg, #4299e1, #667eea); color: white; }
        table.report-table tr.route-header td { padding: 8px 10px; font-size: 8.5pt; letter-spacing: 0.3px; }
        table.report-table .text-right { text-align: right; }
        table.report-table .text-center { text-align: center; }
        .collected-col { color: #2f855a; font-weight: 600; }
        .banked-col { color: #c05621; font-weight: 600; }
        tr.route-subtotal { background: #d6e3f5 !important; font-weight: 700; color: #1a365d; }
        tr.route-subtotal td { padding: 7px 4px; border-color: #7ab8e8; font-size: 8pt; }
        tr.totals-row { background: linear-gradient(135deg, #4299e1, #667eea) !important; color: white; font-weight: 700; }
        tr.totals-row td { padding: 9px 4px; border-color: #5a92d4; font-size: 8.5pt; }
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

    letter_head_html = ('<div class="letter-head">' + letter_head + '</div>') if letter_head else ''

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<title>Route Cash Collection and Banking</title>'
        '<style>' + css + '</style></head><body>'
        + letter_head_html +
        '<div class="report-header">'
            '<div class="report-title">'
                '<h1>Route Cash Collection &amp; Banking</h1>'
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
            '<div class="summary-card card-green">'
                '<div class="label">Total Collected</div>'
                '<div class="value">' + fmt_money(total_collected, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-blue">'
                '<div class="label">Total Banked</div>'
                '<div class="value">' + fmt_money(total_banked, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-orange">'
                '<div class="label">Cash On Hand</div>'
                '<div class="value">' + fmt_money(total_balance, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-purple">'
                '<div class="label">Active Routes</div>'
                '<div class="value">' + str(len(by_route)) + '</div>'
            '</div>'
            '<div class="summary-card card-grey">'
                '<div class="label">Transactions</div>'
                '<div class="value">' + str(sum(len(r["rows"]) for r in by_route.values())) + '</div>'
            '</div>'
        '</div>'

        '<table class="report-table"><thead><tr>'
            '<th class="text-center">#</th>'
            '<th class="text-center">Date</th>'
            '<th class="text-center">Type</th>'
            '<th>Reference</th>'
            '<th>Party / Description</th>'
            '<th>Mode</th>'
            '<th class="text-right">Collected</th>'
            '<th class="text-right">Banked</th>'
            '<th class="text-right">Running Bal.</th>'
        '</tr></thead><tbody>' + rows_html + totals_html + '</tbody></table>'

        '<div class="signature-section">'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Prepared By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Verified By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Approved By</strong></div></div>'
        '</div>'

        '<div class="footer">'
            '<div>Route Cash Collection &amp; Banking - ' + str(filters.get('company') or '') + '</div>'
            '<div>Generated by ' + str(frappe.session.user) + ' on ' + str(now) + '</div>'
        '</div>'

        '</body></html>'
    )

    return html