# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Customers assigned to a Sales Person.

Backs the "Customers" tab on the Sales Person form: who this rep sells
to, what they owe, and the two settings that decide how they are sold to
— customer group and payment terms.

"Assigned" means here what it means everywhere else: a row for this rep
in the customer's Sales Team table. That is exactly what the Sales Pro
app filters on when deciding which customers a salesman sees
(['Sales Team', 'sales_person', '=', <rep>]), so this tab and the phone
in the rep's hand read one fact rather than two — a customer added here
turns up on the rep's round.

Balance is computed on demand from submitted Sales Invoices, the same
definition the rest of this app uses, because ERPNext keeps no balance
on the Customer itself.

Assigning REPLACES the sales team rather than adding to it. Two reasons:
ERPNext throws unless a customer's team percentages total exactly 100
(customer.py — "Total contribution percentage should be equal to 100"),
and in route selling a customer belongs to one rep. Taking a customer
off somebody else is never silent — the caller is told who holds it and
has to say yes.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from stock_addon.stock_addon.doc_events.customer_balance import get_customer_balances


@frappe.whitelist()
def get_customers(sales_person):
    """Customers on this rep's round, with balance, group and payment terms."""
    frappe.has_permission("Customer", throw=True)

    names = frappe.get_all(
        "Sales Team",
        filters={"sales_person": sales_person, "parenttype": "Customer"},
        pluck="parent",
    )
    if not names:
        return []

    rows = frappe.get_all(
        "Customer",
        filters={"name": ("in", names)},
        fields=["name", "customer_name", "customer_group", "territory",
                "payment_terms", "disabled"],
        order_by="customer_name asc",
    )

    balances = get_customer_balances([r["name"] for r in rows])
    for row in rows:
        row["balance"] = flt(balances.get(row["name"]))
    return rows


@frappe.whitelist()
def assign_customer(sales_person, customer, replace=0):
    """Put this rep on a customer's Sales Team.

    Returns {"status": "held_by", "held_by": [...]} instead of acting when
    the customer already belongs to someone else and ``replace`` was not
    passed — moving a customer between reps is a decision, not a detail.
    """
    doc = frappe.get_doc("Customer", customer)
    frappe.has_permission("Customer", "write", doc=doc, throw=True)

    existing = [row.sales_person for row in (doc.get("sales_team") or [])]
    if existing == [sales_person]:
        return {"status": "unchanged", "customer": doc.name}

    others = [person for person in existing if person != sales_person]
    if others and not cint(replace):
        return {"status": "held_by", "held_by": others, "customer": doc.name}

    _set_sole_owner(doc, sales_person)
    doc.save()
    return {"status": "assigned", "customer": doc.name, "replaced": others}


@frappe.whitelist()
def unassign_customer(sales_person, customer):
    """Take a customer off this rep's round.

    The sales team is emptied rather than left with a partial percentage,
    which ERPNext would refuse to save.
    """
    doc = frappe.get_doc("Customer", customer)
    frappe.has_permission("Customer", "write", doc=doc, throw=True)

    remaining = [row for row in (doc.get("sales_team") or [])
                 if row.sales_person != sales_person]
    if len(remaining) == len(doc.get("sales_team") or []):
        return {"status": "unchanged", "customer": doc.name}

    doc.set("sales_team", [])
    # Anything left has to add up to 100 again; an even split is the only
    # division we can make without inventing someone's commission.
    if remaining:
        share = flt(100.0 / len(remaining), 6)
        for row in remaining:
            doc.append("sales_team", {
                "sales_person": row.sales_person,
                "allocated_percentage": share,
                "commission_rate": row.get("commission_rate"),
            })
        _fix_rounding(doc)
    doc.save()
    return {"status": "removed", "customer": doc.name}


@frappe.whitelist()
def create_customer(sales_person, customer_name, customer_group=None,
                    territory=None, payment_terms=None, customer_type=None):
    """Create a customer already belonging to this rep."""
    frappe.has_permission("Customer", "create", throw=True)

    name = (customer_name or "").strip()
    if not name:
        frappe.throw(_("Customer name is required."))

    doc = frappe.new_doc("Customer")
    doc.customer_name = name
    doc.customer_type = customer_type or "Company"
    if customer_group:
        doc.customer_group = customer_group
    if territory:
        doc.territory = territory
    if payment_terms:
        doc.payment_terms = payment_terms
    _set_sole_owner(doc, sales_person)
    doc.insert()
    return {"status": "created", "customer": doc.name}


def _set_sole_owner(doc, sales_person):
    doc.set("sales_team", [])
    doc.append("sales_team", {
        "sales_person": sales_person,
        "allocated_percentage": 100,
    })


def _fix_rounding(doc):
    """Put any rounding remainder on the last row, so the total is exactly
    100 and the save is not rejected over a thousandth of a percent."""
    rows = doc.get("sales_team") or []
    if not rows:
        return
    precision = doc.precision("allocated_percentage", "sales_team") or 2
    total = sum(flt(row.allocated_percentage) for row in rows)
    rows[-1].allocated_percentage = flt(
        flt(rows[-1].allocated_percentage) + (100 - total), precision
    )
