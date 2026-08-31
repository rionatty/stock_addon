# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP Sync Monitor.

One screen for the whole ERPNext → SAP pipeline: every pushable
transaction (Sales Invoice / Credit Note, Van Stock Request, Incoming
Payment, Field Expense) with its SAP status, DocNum and date — plus the
'Send Pending to SAP' and 'Pull From SAP Now' actions in the report
toolbar (see sap_sync_monitor.js).
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    data = get_rows(filters)
    return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
    return [
        {"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
        {"fieldname": "kind", "label": _("Transaction"), "fieldtype": "Data", "width": 150},
        {"fieldname": "document_type", "label": _("DocType"), "fieldtype": "Data",
         "width": 90, "hidden": 1},
        {"fieldname": "document", "label": _("Document"), "fieldtype": "Dynamic Link",
         "options": "document_type", "width": 200},
        {"fieldname": "party", "label": _("Party"), "fieldtype": "Data", "width": 180},
        {"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "sap_status", "label": _("SAP Status"), "fieldtype": "Data", "width": 110},
        {"fieldname": "sap_docnum", "label": _("SAP DocNum"), "fieldtype": "Data", "width": 100},
        {"fieldname": "sap_docentry", "label": _("SAP DocEntry"), "fieldtype": "Data", "width": 100},
    ]


def _status(value):
    return value or "Pending"


def get_rows(filters):
    rows = []
    date_between = ["between", [filters.get("from_date"), filters.get("to_date")]]

    if not filters.get("transaction") or filters.transaction in ("Sales Invoice", "Credit Note"):
        for d in frappe.get_all(
            "Sales Invoice",
            filters={"docstatus": 1, "posting_date": date_between},
            fields=["name", "posting_date", "customer", "grand_total", "is_return",
                    "custom_sap_sync_status", "custom_sap_docnum", "custom_sap_docentry"],
        ):
            kind = "Credit Note" if d.is_return else "Sales Invoice"
            if filters.get("transaction") and filters.transaction != kind:
                continue
            rows.append({
                "posting_date": d.posting_date, "kind": kind,
                "document_type": "Sales Invoice", "document": d.name,
                "party": d.customer, "amount": d.grand_total,
                "sap_status": _status(d.custom_sap_sync_status),
                "sap_docnum": d.custom_sap_docnum, "sap_docentry": d.custom_sap_docentry,
            })

    if not filters.get("transaction") or filters.transaction == "Van Stock Request":
        for d in frappe.get_all(
            "Material Request",
            filters={"docstatus": 1, "material_request_type": "Material Transfer",
                     "transaction_date": date_between},
            fields=["name", "transaction_date", "set_warehouse",
                    "custom_sap_sync_status", "custom_sap_docnum", "custom_sap_docentry"],
        ):
            rows.append({
                "posting_date": d.transaction_date, "kind": "Van Stock Request",
                "document_type": "Material Request", "document": d.name,
                "party": d.set_warehouse, "amount": None,
                "sap_status": _status(d.custom_sap_sync_status),
                "sap_docnum": d.custom_sap_docnum, "sap_docentry": d.custom_sap_docentry,
            })

    if not filters.get("transaction") or filters.transaction == "Incoming Payment":
        for d in frappe.get_all(
            "Payment Entry",
            filters={"docstatus": 1, "payment_type": "Receive", "party_type": "Customer",
                     "posting_date": date_between},
            fields=["name", "posting_date", "party", "paid_amount",
                    "custom_sap_sync_status", "custom_sap_docnum", "custom_sap_docentry"],
        ):
            rows.append({
                "posting_date": d.posting_date, "kind": "Incoming Payment",
                "document_type": "Payment Entry", "document": d.name,
                "party": d.party, "amount": d.paid_amount,
                "sap_status": _status(d.custom_sap_sync_status),
                "sap_docnum": d.custom_sap_docnum, "sap_docentry": d.custom_sap_docentry,
            })

    if not filters.get("transaction") or filters.transaction == "Expense Journal":
        for d in frappe.get_all(
            "Field Expense",
            filters={"status": "Posted", "expense_date": date_between},
            fields=["name", "expense_date", "sales_person", "total_amount",
                    "custom_sap_sync_status", "custom_sap_docnum", "custom_sap_docentry"],
        ):
            rows.append({
                "posting_date": d.expense_date, "kind": "Expense Journal",
                "document_type": "Field Expense", "document": d.name,
                "party": d.sales_person, "amount": d.total_amount,
                "sap_status": _status(d.custom_sap_sync_status),
                "sap_docnum": d.custom_sap_docnum, "sap_docentry": d.custom_sap_docentry,
            })

    if filters.get("sap_status"):
        rows = [r for r in rows if r["sap_status"] == filters.sap_status]

    rows.sort(key=lambda r: (str(r["posting_date"] or ""), r["document"]), reverse=True)
    return rows


def get_chart(data):
    counts = {}
    for r in data:
        counts[r["sap_status"]] = counts.get(r["sap_status"], 0) + 1
    if not counts:
        return None
    labels = list(counts.keys())
    return {
        "data": {"labels": labels,
                 "datasets": [{"name": _("Documents"), "values": [counts[l] for l in labels]}]},
        "type": "donut",
        "height": 220,
        "colors": ["#28a745" if l == "Synced" else "#dc3545" if l == "Failed" else "#fd7e14"
                   for l in labels],
    }


def get_summary(data):
    synced = sum(1 for r in data if r["sap_status"] == "Synced")
    failed = sum(1 for r in data if r["sap_status"] == "Failed")
    pending = sum(1 for r in data if r["sap_status"] == "Pending")
    return [
        {"value": synced, "label": _("Synced"), "datatype": "Int", "indicator": "Green"},
        {"value": failed, "label": _("Failed"), "datatype": "Int", "indicator": "Red"},
        {"value": pending, "label": _("Pending"), "datatype": "Int", "indicator": "Orange"},
    ]
