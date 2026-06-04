# -*- coding: utf-8 -*-
# Copyright (c) 2024, Your Company and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from frappe.utils import flt, getdate, fmt_money, formatdate, format_datetime, get_datetime, date_diff, add_days


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
        {"label": _("Route / Sales Person"), "fieldname": "sales_person", "fieldtype": "Link",
         "options": "Sales Person", "width": 160},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
         "options": "Customer", "width": 160},
        {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 170},
        {"label": _("Type"), "fieldname": "entry_type", "fieldtype": "Data", "width": 90},
        {"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 130},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
        {"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 95},
        {"label": _("Days"), "fieldname": "days_overdue", "fieldtype": "Int", "width": 75},
        {"label": _("Invoice Amt"), "fieldname": "invoice_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Paid Amt"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 110},
        {"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 110},
        {"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 120},
        {"label": _("Current"), "fieldname": "range0", "fieldtype": "Currency", "width": 100},
        {"label": _("1-30 Days"), "fieldname": "range1", "fieldtype": "Currency", "width": 100},
        {"label": _("31-60 Days"), "fieldname": "range2", "fieldtype": "Currency", "width": 100},
        {"label": _("61-90 Days"), "fieldname": "range3", "fieldtype": "Currency", "width": 100},
        {"label": _("Over 90 Days"), "fieldname": "range4", "fieldtype": "Currency", "width": 110},
    ]


def get_data(filters):
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))
    bbf_cutoff = add_days(from_date, -1)
    range1 = flt(filters.get("range1") or 30)
    range2 = flt(filters.get("range2") or 60)
    range3 = flt(filters.get("range3") or 90)

    show_unreconciled_only = filters.get("show_unreconciled_only")
    payment_status = filters.get("payment_status") or []

    if payment_status and isinstance(payment_status[0], dict):
        payment_status = [s.get("value") for s in payment_status]

    conditions = " AND si.docstatus = 1 "

    if show_unreconciled_only:
        conditions += " AND si.outstanding_amount > 0 "

    status_conditions = ""
    if payment_status:
        status_list = ", ".join([f"%(status_{i})s" for i in range(len(payment_status))])
        status_conditions = f" AND si.status IN ({status_list}) "

    if filters.get("company"):
        conditions += " AND si.company = %(company)s "
    if filters.get("customer"):
        conditions += " AND si.customer = %(customer)s "
    if filters.get("sales_person"):
        conditions += " AND st.sales_person = %(sales_person)s "

    base_params = {
        "company": filters.get("company"),
        "customer": filters.get("customer"),
        "sales_person": filters.get("sales_person"),
    }

    for i, s in enumerate(payment_status):
        base_params[f"status_{i}"] = s

    # 1. BALANCE BROUGHT FORWARD
    bbf_params = dict(base_params)
    bbf_params["bbf_cutoff"] = bbf_cutoff

    bbf_query = f"""
        SELECT
            COALESCE(st.sales_person, 'Unassigned') AS sales_person,
            si.customer,
            si.customer_name,
            SUM(si.outstanding_amount * COALESCE(st.allocated_percentage, 100) / 100) AS bbf_amount
        FROM `tabSales Invoice` si
        LEFT JOIN `tabSales Team` st ON st.parent = si.name
        WHERE si.posting_date <= %(bbf_cutoff)s
          AND si.docstatus = 1
          AND si.outstanding_amount > 0
          {(' AND si.company = %(company)s ' if filters.get('company') else '')}
          {(' AND si.customer = %(customer)s ' if filters.get('customer') else '')}
          {(' AND st.sales_person = %(sales_person)s ' if filters.get('sales_person') else '')}
        GROUP BY st.sales_person, si.customer, si.customer_name
        HAVING bbf_amount != 0
    """
    bbf_rows = frappe.db.sql(bbf_query, bbf_params, as_dict=1)

    # 2. INVOICES IN PERIOD
    period_params = dict(base_params)
    period_params["from_date"] = from_date
    period_params["to_date"] = to_date

    period_query = f"""
        SELECT
            COALESCE(st.sales_person, 'Unassigned') AS sales_person,
            si.customer,
            si.customer_name,
            si.name AS invoice,
            si.status,
            si.posting_date,
            si.due_date,
            si.grand_total AS invoice_amount,
            si.outstanding_amount,
            (si.grand_total - si.outstanding_amount) AS paid_amount,
            si.is_return,
            COALESCE(st.allocated_percentage, 100) AS allocated_percentage
        FROM `tabSales Invoice` si
        LEFT JOIN `tabSales Team` st ON st.parent = si.name
        WHERE si.posting_date BETWEEN %(from_date)s AND %(to_date)s
          {conditions}
          {status_conditions}
        ORDER BY st.sales_person, si.customer, si.posting_date
    """
    period_rows = frappe.db.sql(period_query, period_params, as_dict=1)

    bbf_map = {}
    for row in bbf_rows:
        key = (row.sales_person, row.customer)
        bbf_map[key] = {
            "amount": flt(row.bbf_amount),
            "customer_name": row.customer_name,
        }

    all_keys = set(bbf_map.keys())
    customer_activity = {}

    for row in period_rows:
        key = (row.sales_person, row.customer)
        all_keys.add(key)
        customer_activity.setdefault(key, []).append(row)

    ageing_basis = filters.get("ageing_based_on") or "Due Date"
    data = []

    sorted_keys = sorted(all_keys, key=lambda x: (x[0] or "", x[1] or ""))

    for key in sorted_keys:
        sp, cust = key
        bbf_info = bbf_map.get(key)
        bbf_amount = flt(bbf_info["amount"]) if bbf_info else 0.0
        cust_name = (
            (bbf_info["customer_name"] if bbf_info else None)
            or (customer_activity.get(key, [{}])[0].get("customer_name") if customer_activity.get(key) else "")
        )

        running_balance = 0.0

        # ---- BBF ROW ----
        if bbf_amount != 0:
            bbf_debit = bbf_amount if bbf_amount > 0 else 0.0
            bbf_credit = abs(bbf_amount) if bbf_amount < 0 else 0.0
            running_balance = bbf_amount

            data.append({
                "sales_person": sp,
                "customer": cust,
                "customer_name": cust_name,
                "entry_type": "BBF",
                "status": "",
                "invoice": "",
                "posting_date": None,
                "due_date": None,
                "days_overdue": None,
                "invoice_amount": 0,
                "paid_amount": 0,
                "debit": bbf_debit,
                "credit": bbf_credit,
                "balance": running_balance,
                "range0": 0,
                "range1": 0,
                "range2": 0,
                "range3": 0,
                "range4": 0,
                "is_bbf": 1,
            })

        # ---- PERIOD INVOICE ROWS ----
        for inv in customer_activity.get(key, []):
            allocation = flt(inv.allocated_percentage) / 100.0
            allocated_outstanding = flt(inv.outstanding_amount) * allocation
            allocated_invoice = flt(inv.invoice_amount) * allocation
            allocated_paid = flt(inv.paid_amount) * allocation

            if ageing_basis == "Posting Date":
                age_ref_date = inv.posting_date
            else:
                age_ref_date = inv.due_date or inv.posting_date

            days_overdue = date_diff(to_date, age_ref_date)

            if inv.is_return:
                debit = 0
                credit = abs(allocated_outstanding)
                running_balance -= abs(allocated_outstanding)
            else:
                debit = allocated_outstanding
                credit = 0
                running_balance += allocated_outstanding

            data.append({
                "sales_person": sp,
                "customer": cust,
                "customer_name": inv.customer_name,
                "entry_type": "Invoice" if not inv.is_return else "Return",
                "status": inv.status or "",
                "invoice": inv.invoice,
                "posting_date": inv.posting_date,
                "due_date": inv.due_date,
                "days_overdue": max(0, days_overdue),
                "invoice_amount": allocated_invoice,
                "paid_amount": allocated_paid,
                "debit": debit,
                "credit": credit,
                "balance": running_balance,
                "range0": 0,
                "range1": 0,
                "range2": 0,
                "range3": 0,
                "range4": 0,
                "is_bbf": 0,
                "_raw_outstanding": allocated_outstanding,
                "_age_days": max(0, days_overdue),
            })

    for row in data:
        if row.get("is_bbf"):
            bbf_age_days = date_diff(to_date, bbf_cutoff)
            amt = flt(row["balance"])
            distribute_ageing(row, amt, bbf_age_days, range1, range2, range3)
        else:
            amt = flt(row.get("_raw_outstanding") or 0)
            age = flt(row.get("_age_days") or 0)
            distribute_ageing(row, amt, age, range1, range2, range3)

        row.pop("_raw_outstanding", None)
        row.pop("_age_days", None)
        row.pop("is_bbf", None)

    return data


def distribute_ageing(row, amount, days, range1, range2, range3):
    """Distribute a given amount into ageing buckets based on days."""
    if not amount:
        return
    if days <= 0:
        row["range0"] = amount
    elif days <= range1:
        row["range1"] = amount
    elif days <= range2:
        row["range2"] = amount
    elif days <= range3:
        row["range3"] = amount
    else:
        row["range4"] = amount


def get_report_summary(data):
    if not data:
        return []

    total_debit = sum(flt(d.get("debit")) for d in data)
    total_credit = sum(flt(d.get("credit")) for d in data)
    total_outstanding = total_debit - total_credit
    total_critical = sum(flt(d.get("range4")) for d in data)
    total_current = sum(flt(d.get("range0")) for d in data)

    return [
        {"value": total_outstanding, "label": _("Net Outstanding"),
         "datatype": "Currency", "indicator": "blue"},
        {"value": total_debit, "label": _("Total Debit"),
         "datatype": "Currency", "indicator": "orange"},
        {"value": total_credit, "label": _("Total Credit"),
         "datatype": "Currency", "indicator": "green"},
        {"value": total_critical, "label": _("Over 90 Days"),
         "datatype": "Currency", "indicator": "red"},
        {"value": len([d for d in data if d.get("entry_type") in ("Invoice", "Return")]),
         "label": _("Invoices in Period"),
         "datatype": "Int", "indicator": "grey"}
    ]


def get_chart_data(data):
    if not data:
        return None

    sp_totals = {}
    for row in data:
        sp = row.get("sales_person") or "Unassigned"
        if sp not in sp_totals:
            sp_totals[sp] = {"current": 0, "r1": 0, "r2": 0, "r3": 0, "r4": 0}
        sp_totals[sp]["current"] += flt(row.get("range0"))
        sp_totals[sp]["r1"] += flt(row.get("range1"))
        sp_totals[sp]["r2"] += flt(row.get("range2"))
        sp_totals[sp]["r3"] += flt(row.get("range3"))
        sp_totals[sp]["r4"] += flt(row.get("range4"))

    sorted_sp = sorted(
        sp_totals.items(),
        key=lambda x: sum(x[1].values()),
        reverse=True
    )[:10]

    return {
        "data": {
            "labels": [sp[0] for sp in sorted_sp],
            "datasets": [
                {"name": "Current", "values": [sp[1]["current"] for sp in sorted_sp]},
                {"name": "1-30", "values": [sp[1]["r1"] for sp in sorted_sp]},
                {"name": "31-60", "values": [sp[1]["r2"] for sp in sorted_sp]},
                {"name": "61-90", "values": [sp[1]["r3"] for sp in sorted_sp]},
                {"name": "90+", "values": [sp[1]["r4"] for sp in sorted_sp]},
            ]
        },
        "type": "bar",
        "colors": ["#7cc99a", "#7ab8e8", "#f5a96b", "#f08585", "#e06868"],
        "barOptions": {"stacked": 1, "spaceRatio": 0.3}
    }


# ============================================================
# PDF EXPORT
# ============================================================

@frappe.whitelist()
def get_pdf_html(filters, data, columns=None):
    """Generate beautiful HTML for PDF printing."""

    if isinstance(filters, str):
        filters = json.loads(filters)
    if isinstance(data, str):
        data = json.loads(data)

    # Filter out auto-added Total row
    data = [d for d in data if d.get("entry_type") in ("BBF", "Invoice", "Return")]

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

    # Compute totals
    total_debit = sum(flt(d.get("debit")) for d in data)
    total_credit = sum(flt(d.get("credit")) for d in data)
    total_balance = total_debit - total_credit
    total_invoice = sum(flt(d.get("invoice_amount")) for d in data)
    total_paid = sum(flt(d.get("paid_amount")) for d in data)
    total_r0 = sum(flt(d.get("range0")) for d in data)
    total_r1 = sum(flt(d.get("range1")) for d in data)
    total_r2 = sum(flt(d.get("range2")) for d in data)
    total_r3 = sum(flt(d.get("range3")) for d in data)
    total_r4 = sum(flt(d.get("range4")) for d in data)

    # Group by route -> customer
    by_route = {}
    for row in data:
        sp = row.get("sales_person") or "Unassigned"
        cust = row.get("customer") or ""
        by_route.setdefault(sp, {})
        by_route[sp].setdefault(cust, {
            "rows": [],
            "customer_name": row.get("customer_name") or "",
            "totals": {"debit": 0, "credit": 0, "invoice_amount": 0, "paid_amount": 0,
                       "range0": 0, "range1": 0, "range2": 0, "range3": 0, "range4": 0}
        })
        by_route[sp][cust]["rows"].append(row)
        for k in ["debit", "credit", "invoice_amount", "paid_amount",
                  "range0", "range1", "range2", "range3", "range4"]:
            by_route[sp][cust]["totals"][k] += flt(row.get(k))

    # Build filter summary
    payment_status_list = filters.get("payment_status") or []
    if payment_status_list and isinstance(payment_status_list[0], dict):
        payment_status_list = [s.get("value") for s in payment_status_list]

    filter_html = ""
    filter_map = [
        ("From Date", formatdate(filters.get("from_date")) if filters.get("from_date") else ""),
        ("To Date", formatdate(filters.get("to_date")) if filters.get("to_date") else ""),
        ("Company", filters.get("company") or ""),
        ("Route", filters.get("sales_person") or "All"),
        ("Customer", filters.get("customer") or "All"),
        ("Payment Status", ", ".join(payment_status_list) or "All"),
        ("Unreconciled Only", "Yes" if filters.get("show_unreconciled_only") else "No"),
        ("Ageing Basis", filters.get("ageing_based_on") or "Due Date"),
    ]
    for label, value in filter_map:
        if value:
            filter_html += (
                '<div class="filter-item">'
                '<span class="filter-label">' + str(label) + ':</span> '
                '<span class="filter-value">' + str(value) + '</span>'
                '</div>'
            )

    # Status colors helper
    status_colors = {
        "Paid": "#7cc99a",
        "Unpaid": "#f08585",
        "Partly Paid": "#f5a96b",
        "Overdue": "#e06868",
        "Return": "#95a3b8",
        "Credit Note Issued": "#95a3b8"
    }

    # Build table rows grouped by route then customer
    rows_html = ""
    for sp_name, customers in by_route.items():
        # Calculate route totals
        route_totals = {"debit": 0, "credit": 0, "invoice_amount": 0, "paid_amount": 0,
                        "range0": 0, "range1": 0, "range2": 0, "range3": 0, "range4": 0}
        for cust_data in customers.values():
            for k in route_totals:
                route_totals[k] += cust_data["totals"][k]
        route_balance = route_totals["debit"] - route_totals["credit"]

        # Route header
        rows_html += (
            '<tr class="route-header">'
            '<td colspan="17"><strong>ROUTE: ' + str(sp_name) + '</strong></td>'
            '</tr>'
        )

        for cust_code, cust_data in customers.items():
            # Customer header
            rows_html += (
                '<tr class="customer-header">'
                '<td colspan="17"><strong>' + str(cust_code) + ' - ' + str(cust_data["customer_name"]) + '</strong></td>'
                '</tr>'
            )

            for idx, row in enumerate(cust_data["rows"], 1):
                row_class = "even" if idx % 2 == 0 else "odd"
                entry_type = row.get("entry_type") or ""
                type_class = "bbf-row" if entry_type == "BBF" else ""

                days = row.get("days_overdue")
                days_class = ""
                if days is not None and days > 90:
                    days_class = "critical"
                elif days is not None and days > 60:
                    days_class = "warning"
                elif days is not None and days > 30:
                    days_class = "caution"

                status = row.get('status') or ''
                status_bg = status_colors.get(status, "#95a3b8")
                status_html = (
                    f'<span style="background:{status_bg};color:white;padding:2px 6px;'
                    f'border-radius:3px;font-size:7pt;font-weight:600">{status}</span>'
                ) if status else '-'

                rows_html += (
                    '<tr class="' + row_class + ' ' + type_class + '">'
                    '<td class="text-center">' + str(idx) + '</td>'
                    '<td class="text-muted">' + str(row.get('sales_person') or '') + '</td>'
                    '<td>' + str(row.get('customer') or '') + '</td>'
                    '<td>' + str(row.get('customer_name') or '') + '</td>'
                    '<td class="text-center"><strong>' + entry_type + '</strong></td>'
                    '<td>' + str(row.get('invoice') or '') + '</td>'
                    '<td class="text-center">' + status_html + '</td>'
                    '<td class="text-center">' + (formatdate(row.get('posting_date')) if row.get('posting_date') else '-') + '</td>'
                    '<td class="text-center">' + (formatdate(row.get('due_date')) if row.get('due_date') else '-') + '</td>'
                    '<td class="text-center ' + days_class + '">' + (str(days) if days is not None else '-') + '</td>'
                    '<td class="text-right">' + (fmt_money(row.get('invoice_amount'), currency=currency) if row.get('invoice_amount') else '-') + '</td>'
                    '<td class="text-right">' + (fmt_money(row.get('paid_amount'), currency=currency) if row.get('paid_amount') else '-') + '</td>'
                    '<td class="text-right">' + (fmt_money(row.get('debit'), currency=currency) if row.get('debit') else '-') + '</td>'
                    '<td class="text-right">' + (fmt_money(row.get('credit'), currency=currency) if row.get('credit') else '-') + '</td>'
                    '<td class="text-right"><strong>' + fmt_money(row.get('balance'), currency=currency) + '</strong></td>'
                    '<td class="text-right current-col">' + (fmt_money(row.get('range0'), currency=currency) if row.get('range0') else '-') + '</td>'
                    '<td class="text-right r4-col">' + (fmt_money(row.get('range4'), currency=currency) if row.get('range4') else '-') + '</td>'
                    '</tr>'
                )

            # Customer subtotal
            t = cust_data["totals"]
            cust_balance = t["debit"] - t["credit"]
            rows_html += (
                '<tr class="customer-subtotal">'
                '<td colspan="10" class="text-right"><strong>Customer Total: ' + str(cust_code) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["invoice_amount"], currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["paid_amount"], currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["debit"], currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["credit"], currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(cust_balance, currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["range0"], currency=currency) + '</strong></td>'
                '<td class="text-right"><strong>' + fmt_money(t["range4"], currency=currency) + '</strong></td>'
                '</tr>'
            )

        # Route subtotal
        rows_html += (
            '<tr class="route-subtotal">'
            '<td colspan="10" class="text-right"><strong>ROUTE TOTAL: ' + str(sp_name) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["invoice_amount"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["paid_amount"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["debit"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["credit"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_balance, currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["range0"], currency=currency) + '</strong></td>'
            '<td class="text-right"><strong>' + fmt_money(route_totals["range4"], currency=currency) + '</strong></td>'
            '</tr>'
        )

    # Grand total
    totals_html = (
        '<tr class="totals-row">'
        '<td colspan="10" class="text-right"><strong>GRAND TOTAL</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_invoice, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_paid, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_debit, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_credit, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_balance, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_r0, currency=currency) + '</strong></td>'
        '<td class="text-right"><strong>' + fmt_money(total_r4, currency=currency) + '</strong></td>'
        '</tr>'
    )

    now = format_datetime(get_datetime(), "dd MMM yyyy HH:mm")

    css = """
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
        .summary-card { padding: 10px 10px; border-radius: 6px; color: white; text-align: center;
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
        table.report-table tr.bbf-row { background: #fff8e6 !important; font-weight: 600; }
        table.report-table tr.bbf-row td { border-color: #f5a96b; }
        table.report-table tr.route-header { background: linear-gradient(135deg, #4299e1, #667eea); color: white; }
        table.report-table tr.route-header td { padding: 8px 10px; font-size: 9pt; letter-spacing: 0.5px; }
        table.report-table tr.customer-header { background: #a3b8d8; color: #1a365d; }
        table.report-table tr.customer-header td { padding: 5px 10px; font-size: 8pt; }
        table.report-table .text-right { text-align: right; }
        table.report-table .text-center { text-align: center; }
        table.report-table .text-muted { color: #718096; font-size: 6.5pt; }
        .current-col { color: #5cb37e; }
        .r4-col { color: #e06868; font-weight: bold; }
        .caution { color: #e8884a; }
        .warning { color: #e06868; }
        .critical { color: #c53030; background: #fed7d7; padding: 1px 4px; border-radius: 3px; }
        tr.customer-subtotal { background: #edf2f7 !important; font-weight: 600; }
        tr.customer-subtotal td { padding: 5px 3px; border-color: #cbd5e0; font-size: 7.2pt; }
        tr.route-subtotal { background: #d6e3f5 !important; font-weight: 700; color: #1a365d; }
        tr.route-subtotal td { padding: 7px 3px; border-color: #7ab8e8; font-size: 7.5pt; }
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

    letter_head_html = ('<div class="letter-head">' + letter_head + '</div>') if letter_head else ''

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<title>Route Ageing Report</title>'
        '<style>' + css + '</style></head><body>'
        + letter_head_html +
        '<div class="report-header">'
            '<div class="report-title">'
                '<h1>Route Ageing Report</h1>'
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
                '<div class="label">Net Outstanding</div>'
                '<div class="value">' + fmt_money(total_balance, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-orange">'
                '<div class="label">Total Debit</div>'
                '<div class="value">' + fmt_money(total_debit, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-green">'
                '<div class="label">Total Credit</div>'
                '<div class="value">' + fmt_money(total_credit, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-red">'
                '<div class="label">Over 90 Days</div>'
                '<div class="value">' + fmt_money(total_r4, currency=currency) + '</div>'
            '</div>'
            '<div class="summary-card card-purple">'
                '<div class="label">Current</div>'
                '<div class="value">' + fmt_money(total_r0, currency=currency) + '</div>'
            '</div>'
        '</div>'

        '<table class="report-table"><thead><tr>'
            '<th class="text-center">#</th>'
            '<th>Route</th>'
            '<th>Customer</th>'
            '<th>Name</th>'
            '<th class="text-center">Type</th>'
            '<th>Invoice</th>'
            '<th class="text-center">Status</th>'
            '<th class="text-center">Posted</th>'
            '<th class="text-center">Due</th>'
            '<th class="text-center">Days</th>'
            '<th class="text-right">Inv Amt</th>'
            '<th class="text-right">Paid</th>'
            '<th class="text-right">Debit</th>'
            '<th class="text-right">Credit</th>'
            '<th class="text-right">Balance</th>'
            '<th class="text-right">Current</th>'
            '<th class="text-right">90+</th>'
        '</tr></thead><tbody>' + rows_html + totals_html + '</tbody></table>'

        '<div class="signature-section">'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Prepared By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Verified By</strong></div></div>'
            '<div class="signature-box"><div class="signature-line"></div><div><strong>Approved By</strong></div></div>'
        '</div>'

        '<div class="footer">'
            '<div>Route Ageing Report - ' + str(filters.get('company') or '') + '</div>'
            '<div>Generated by ' + str(frappe.session.user) + ' on ' + str(now) + '</div>'
        '</div>'

        '</body></html>'
    )

    return html