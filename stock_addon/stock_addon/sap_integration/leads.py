# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""ERPNext Leads → SAP B1, and what happens when one becomes a customer.

SAP has no separate lead object: a lead is a BusinessPartner whose
CardType is 'cLid'. Converting it to a customer is therefore not a new
record but a change of type on the same one, and the CardCode it was
given as a lead follows it. That shapes everything here:

  Lead created in ERPNext  -> POST /BusinessPartners, CardType cLid
  Lead converted           -> PATCH that partner to cCustomer, and the
                              ERPNext Customer inherits the same CardCode

Because the code carries over, lead codes are drawn from the SAME
sequence as customer codes. Issuing them separately would eventually
hand a customer a code a lead already holds, and SAP would reject the
conversion for a duplicate it could not explain.

The sales person follows too. A lead belongs to whoever brought it in,
and losing that at the moment of conversion — the one moment it starts
to matter — would leave the new customer on nobody's round and invisible
in the app.
"""

import frappe
from frappe import _

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    SAPError,
    get_settings,
    integration_enabled,
    log_sap,
    note_pushed,
    note_sending,
)

LEAD_CARD_TYPE = "cLid"
CUSTOMER_CARD_TYPE = "cCustomer"


# ----------------------------------------------------------- lead push
def on_lead_insert(doc, method=None):
    if not integration_enabled("push_leads"):
        return
    try:
        push_lead_doc(doc)
    except Exception as e:
        # A lead is captured in the field, often on a bad connection. SAP
        # being unreachable must never be the reason it was not recorded.
        _stamp(doc, "Failed")
        log_sap("Push", "Failed", "BusinessPartners", doc.doctype, doc.name, message=str(e))


def push_lead_doc(doc):
    """Create the lead in SAP as a business partner of type cLid."""
    if (doc.get("custom_sap_cardcode") or "").strip():
        return                                  # already there

    name = (doc.get("company_name") or doc.get("lead_name") or "").strip()
    if not name:
        raise SAPError(f"Lead {doc.name} has neither a company name nor a lead name to send.")

    code = _next_card_code()
    payload = {
        "CardCode": code,
        "CardName": name[:100],                 # OCRD.CardName is nvarchar(100)
        "CardType": LEAD_CARD_TYPE,
    }

    phone = (doc.get("mobile_no") or doc.get("phone") or "").strip()
    if phone:
        payload["Phone1"] = phone[:20]
    if (doc.get("email_id") or "").strip():
        payload["EmailAddress"] = doc.email_id.strip()[:100]

    employee = _sales_employee_code(doc)
    if employee is not None:
        payload["SalesPersonCode"] = employee

    note_sending(doc.name)
    result = SAPClient().post("BusinessPartners", payload)
    # A business partner is keyed by its CardCode, not a DocEntry — SAP
    # echoes back what we sent, so trust the code we generated.
    returned = (result.get("CardCode") or code).strip()
    note_pushed("BusinessPartners", returned, doc.name)

    doc.db_set({"custom_sap_cardcode": returned, "custom_sap_sync_status": "Synced"},
               update_modified=False, notify=True)
    log_sap("Push", "Success", "BusinessPartners", doc.doctype, doc.name, returned,
            f"Lead created in SAP as {returned} (CardType {LEAD_CARD_TYPE})")
    return returned


# ------------------------------------------------------- lead -> customer
def on_customer_insert(doc, method=None):
    """A customer made from a lead keeps the lead's rep and its SAP code.

    ERPNext's own conversion copies neither: it fills lead_name and stops.
    Without this the new customer reaches SAP as a SECOND business
    partner, and sits on nobody's round here.
    """
    lead = (doc.get("lead_name") or "").strip()
    if not lead:
        return

    values = frappe.db.get_value(
        "Lead", lead, ["custom_sales_person", "custom_sap_cardcode"], as_dict=True) or {}

    _inherit_sales_person(doc, values.get("custom_sales_person"))

    card_code = (values.get("custom_sap_cardcode") or "").strip()
    if not card_code:
        return                                  # the lead never reached SAP
    if not (doc.get("custom_sap_cardcode") or "").strip():
        doc.db_set("custom_sap_cardcode", card_code, update_modified=False)

    if integration_enabled("push_leads"):
        _convert_in_sap(doc, card_code)


def _inherit_sales_person(doc, sales_person):
    """Put the lead's rep on the new customer's sales team.

    Written through the same helper the customer sync uses, so the
    percentage rule ERPNext enforces is satisfied the same way and the
    app sees the customer on that rep's round immediately.
    """
    if not sales_person or not frappe.db.exists("Sales Person", sales_person):
        return
    try:
        from stock_addon.stock_addon.sap_integration.masters import _set_customer_sales_team
        _set_customer_sales_team(doc.name, sales_person)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Lead conversion: sales person not carried over")


def _convert_in_sap(doc, card_code):
    """Turn the SAP lead into a customer, in place.

    PATCH rather than POST: it is the same partner. Creating a new one
    would leave the lead behind as a duplicate and split its history
    across two cards.
    """
    payload = {"CardType": CUSTOMER_CARD_TYPE}
    name = (doc.get("customer_name") or "").strip()
    if name:
        payload["CardName"] = name[:100]

    try:
        SAPClient().request("PATCH", f"BusinessPartners('{card_code}')", payload=payload)
    except Exception as e:
        log_sap("Push", "Failed", "BusinessPartners", doc.doctype, doc.name, card_code, message=(
            f"Customer {doc.name} was converted from a lead, but SAP business partner "
            f"{card_code} could not be changed from a lead to a customer: {str(e)[:500]}. "
            "It is still a lead in SAP, so invoices for this customer will be rejected "
            "until someone changes it there."))
        return

    log_sap("Push", "Success", "BusinessPartners", doc.doctype, doc.name, card_code,
            f"SAP business partner {card_code} converted from lead to customer")


# ------------------------------------------------------------- helpers
def _next_card_code():
    """The next code in the sequence shared with customers.

    Both tables are scanned. A lead keeps its code when it converts, so a
    code issued to a lead is a code no customer may ever be given.
    """
    from stock_addon.stock_addon.sap_integration.masters import _next_code_number
    prefix = (get_settings().get("customer_code_prefix") or "C").strip()
    return f"{prefix}{_next_code_number(prefix):05d}"


def _sales_employee_code(doc):
    """SAP SalesEmployeeCode for the rep who owns this lead, or None."""
    sales_person = (doc.get("custom_sales_person") or "").strip()
    if not sales_person:
        return None
    if not frappe.get_meta("Sales Person").get_field("custom_sap_sales_employee_code"):
        return None
    code = (frappe.db.get_value(
        "Sales Person", sales_person, "custom_sap_sales_employee_code") or "").strip()
    try:
        return int(code) if code else None
    except ValueError:
        return None


def _stamp(doc, status):
    try:
        doc.db_set("custom_sap_sync_status", status, update_modified=False, notify=True)
    except Exception:
        pass
