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
        {"label": _("Expenses"), "fieldname": "expense",
         "fieldtype": "Currency", "width": 120},
        {"label": _("Other Paid Out"), "fieldname": "other_out",
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


# Money into a route's cash account is a collection. Money out of it counts
# as banked only for the part that went to a Bank ledger in the same voucher.
# The part that went to an Expense account is an expense; anything else — a
# supplier paid in cash, cash handed to another cash account — is paid out,
# but not banked. A voucher that mixes them (a deposit with the bank charge
# taken from the same cash) becomes one row for each part.
DETAIL_TYPES = ("Collection", "Banking", "Expense", "Cash Transfer", "Payment")
MONEY_FIELDS = ("collected", "banked", "expense", "other_out")


def get_debit_splits(gl_entries):
    """Where the money paid out of the cash account went, per voucher.

    One query for all of a route's vouchers: the voucher's debits, divided
    into those to Bank accounts, Expense accounts and Cash accounts.
    """
    vouchers = {gle.voucher_no for gle in gl_entries if flt(gle.credit)}
    if not vouchers:
        return {}

    rows = frappe.db.sql("""
        SELECT
            gle.voucher_type,
            gle.voucher_no,
            gle.account,
            gle.debit,
            acc.account_type,
            acc.root_type
        FROM `tabGL Entry` gle
        JOIN `tabAccount` acc ON acc.name = gle.account
        WHERE gle.voucher_no IN %(vouchers)s
            AND gle.is_cancelled = 0
            AND gle.debit > 0
        ORDER BY gle.creation
    """, {"vouchers": tuple(vouchers)}, as_dict=1)

    splits = {}
    for row in rows:
        split = splits.setdefault(
            (row.voucher_type, row.voucher_no),
            frappe._dict(total=0.0, bank=0.0, expense=0.0, cash=0.0, bank_account=""),
        )
        amount = flt(row.debit)
        split.total += amount
        if row.account_type == "Bank":
            split.bank += amount
            split.bank_account = split.bank_account or row.account
        elif row.root_type == "Expense":
            split.expense += amount
        elif row.account_type == "Cash":
            split.cash += amount
    return splits


def get_payment_details(gl_entries):
    """Mode of payment and party name of the route's Payment Entries, in one query."""
    names = list({gle.voucher_no for gle in gl_entries if gle.voucher_type == "Payment Entry"})
    if not names:
        return {}
    return {
        pe.name: pe
        for pe in frappe.get_all(
            "Payment Entry",
            filters={"name": ["in", names]},
            fields=["name", "mode_of_payment", "party_name"],
        )
    }


def describe(gle, payments):
    """Mode of payment, and a party or description, for one GL line."""
    if gle.voucher_type == "Payment Entry":
        pe = payments.get(gle.voucher_no) or frappe._dict()
        return pe.mode_of_payment or "", gle.party or pe.party_name or ""
    return "", gle.party or (gle.remarks or gle.against or "")[:100]


def split_entry(gle, splits):
    """The report rows one GL line on the cash account becomes."""

    def part(entry_type, bank_account="", **money):
        row = {"entry_type": entry_type, "bank_account": bank_account}
        row.update({f: flt(money.get(f)) for f in MONEY_FIELDS})
        return row

    parts = []
    if flt(gle.debit):
        parts.append(part("Collection", collected=gle.debit))

    credit = flt(gle.credit)
    if not credit:
        return parts

    split = splits.get((gle.voucher_type, gle.voucher_no))
    if not split or not split.total:
        parts.append(part("Payment", other_out=credit))
        return parts

    banked = flt(credit * split.bank / split.total, 2)
    expense = flt(credit * split.expense / split.total, 2)
    other = flt(credit - banked - expense, 2)

    if banked:
        parts.append(part("Banking", split.bank_account, banked=banked))
    if expense:
        parts.append(part("Expense", expense=expense))
    if other:
        # All of the rest went to other cash accounts, or some of it to a party.
        rest = split.total - split.bank - split.expense
        kind = "Cash Transfer" if split.cash and not flt(rest - split.cash, 2) else "Payment"
        parts.append(part(kind, other_out=other))
    return parts


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
                    "expense": 0,
                    "other_out": 0,
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

        splits = get_debit_splits(gl_entries)
        payments = get_payment_details(gl_entries)

        running = 0.0
        totals = dict.fromkeys(MONEY_FIELDS, 0.0)

        for gle in gl_entries:
            mode_of_payment, party_label = describe(gle, payments)

            for part in split_entry(gle, splits):
                running += part["collected"] - part["banked"] - part["expense"] - part["other_out"]
                for fieldname in MONEY_FIELDS:
                    totals[fieldname] += part[fieldname]

                data.append({
                    "sales_person": route.sales_person,
                    "employee": route.employee,
                    "employee_name": route.employee_name,
                    "user": route.user,
                    "cash_account": route.cash_account,
                    "posting_date": gle.posting_date,
                    "reference": gle.voucher_no,
                    "reference_doctype": gle.voucher_type,
                    "party": party_label,
                    "mode_of_payment": mode_of_payment,
                    **part,
                    "running_balance": running,
                })

        # Route subtotal
        data.append({
            "sales_person": route.sales_person,
            "employee_name": "-- ROUTE TOTAL --",
            "entry_type": "SUBTOTAL",
            **totals,
            "running_balance": totals["collected"] - totals["banked"] - totals["expense"] - totals["other_out"],
        })

    return data


# ============================================================
# SUMMARY & CHART
# ============================================================

def get_report_summary(data):
    if not data:
        return []

    detail = [d for d in data if d.get("entry_type") in DETAIL_TYPES]

    total = {f: sum(flt(d.get(f)) for d in detail) for f in MONEY_FIELDS}
    cash_on_hand = total["collected"] - total["banked"] - total["expense"] - total["other_out"]

    routes_active = len(set(d.get("sales_person") for d in detail))
    # a voucher split into banked and expense parts is still one transaction
    txn_count = len({(d.get("sales_person"), d.get("reference_doctype"), d.get("reference")) for d in detail})

    summary = [
        {"value": total["collected"], "label": _("Total Collected"),
         "datatype": "Currency", "indicator": "green"},
        {"value": total["banked"], "label": _("Total Banked"),
         "datatype": "Currency", "indicator": "blue"},
        {"value": total["expense"], "label": _("Total Expenses"),
         "datatype": "Currency", "indicator": "red"},
    ]
    if total["other_out"]:
        summary.append({"value": total["other_out"], "label": _("Other Paid Out"),
                        "datatype": "Currency", "indicator": "grey"})
    summary += [
        {"value": cash_on_hand, "label": _("Cash On Hand"),
         "datatype": "Currency",
         "indicator": "orange" if cash_on_hand > 0 else "green"},
        {"value": routes_active, "label": _("Active Routes"),
         "datatype": "Int", "indicator": "purple"},
        {"value": txn_count, "label": _("Transactions"),
         "datatype": "Int", "indicator": "grey"},
    ]
    return summary


def get_chart_data(data):
    if not data:
        return None

    detail = [d for d in data if d.get("entry_type") in DETAIL_TYPES]
    if not detail:
        return None

    totals = {}
    for row in detail:
        sp = row.get("sales_person") or "Unassigned"
        totals.setdefault(sp, {"collected": 0, "banked": 0, "expense": 0})
        totals[sp]["collected"] += flt(row.get("collected"))
        totals[sp]["banked"] += flt(row.get("banked"))
        totals[sp]["expense"] += flt(row.get("expense"))

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
                {"name": "Expenses", "values": [r[1]["expense"] for r in sorted_routes]},
            ]
        },
        "type": "bar",
        "colors": ["#7cc99a", "#7ab8e8", "#e57373"],
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
                    "totals": dict.fromkeys(MONEY_FIELDS, 0.0)
                }
            continue

        if et not in DETAIL_TYPES:
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
                "totals": dict.fromkeys(MONEY_FIELDS, 0.0)
            }
        by_route[sp]["rows"].append(row)
        for fieldname in MONEY_FIELDS:
            by_route[sp]["totals"][fieldname] += flt(row.get(fieldname))

    def cash_left(totals):
        return totals["collected"] - totals["banked"] - totals["expense"] - totals["other_out"]

    grand = {f: sum(r["totals"][f] for r in by_route.values()) for f in MONEY_FIELDS}
    total_collected = grand["collected"]
    total_banked = grand["banked"]
    total_expense = grand["expense"]
    total_balance = cash_left(grand)

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

    type_colors = {
        "Collection": "#5cb37e",
        "Banking": "#e8884a",
        "Expense": "#d9534f",
        "Cash Transfer": "#9374c8",
        "Payment": "#7a8aa3",
    }

    def money_cell(value, css_class=""):
        shown = fmt_money(value, currency=currency) if flt(value) else "-"
        return '<td class="text-right ' + css_class + '">' + shown + '</td>'

    def total_cells(totals):
        return "".join(
            '<td class="text-right"><strong>' + fmt_money(totals[f], currency=currency) + '</strong></td>'
            for f in MONEY_FIELDS
        ) + '<td class="text-right"><strong>' + fmt_money(cash_left(totals), currency=currency) + '</strong></td>'

    # Build table rows
    rows_html = ""
    for sp, info in by_route.items():
        if not info["rows"]:
            continue

        rows_html += (
            '<tr class="route-header">'
            '<td colspan="11">'
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
            running += cash_left({f: flt(row.get(f)) for f in MONEY_FIELDS})
            row_class = "even" if idx % 2 == 0 else "odd"
            entry_type = row.get("entry_type") or ""
            type_color = type_colors.get(entry_type, "#7a8aa3")
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
                + money_cell(row.get('collected'), 'collected-col')
                + money_cell(row.get('banked'), 'banked-col')
                + money_cell(row.get('expense'), 'expense-col')
                + money_cell(row.get('other_out'))
                + '<td class="text-right"><strong>' + fmt_money(running, currency=currency) + '</strong></td>'
                '</tr>'
            )

        rows_html += (
            '<tr class="route-subtotal">'
            '<td colspan="6" class="text-right"><strong>ROUTE TOTAL: ' + str(sp) + '</strong></td>'
            + total_cells(info["totals"])
            + '</tr>'
        )

    totals_html = (
        '<tr class="totals-row">'
        '<td colspan="6" class="text-right"><strong>GRAND TOTAL</strong></td>'
        + total_cells(grand)
        + '</tr>'
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
        .summary-cards { display: grid; grid-template-columns: repeat(6, 1fr);
                        gap: 8px; margin-bottom: 10px; }
        .summary-card { padding: 10px; border-radius: 6px; color: white; text-align: center;
                        box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
        .card-blue { background: linear-gradient(135deg, #7ab8e8, #5a92d4); }
        .card-green { background: linear-gradient(135deg, #7cc99a, #5cb37e); }
        .card-orange { background: linear-gradient(135deg, #f5a96b, #e8884a); }
        .card-purple { background: linear-gradient(135deg, #b08ee0, #9374c8); }
        .card-grey { background: linear-gradient(135deg, #95a3b8, #7a8aa3); }
        .card-red { background: linear-gradient(135deg, #e57373, #d9534f); }
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
        .expense-col { color: #c53030; font-weight: 600; }
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
            '<div class="summary-card card-red">'
                '<div class="label">Total Expenses</div>'
                '<div class="value">' + fmt_money(total_expense, currency=currency) + '</div>'
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
            '<th class="text-right">Expenses</th>'
            '<th class="text-right">Other Out</th>'
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