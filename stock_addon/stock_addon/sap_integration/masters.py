# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP B1 → ERPNext master data sync.

- Items:      /Items (sales items, optionally limited to given SAP item
              group codes). item_code == SAP ItemCode, so transaction
              push needs no item mapping at all.
- Customers:  /BusinessPartners (CardType cCustomer). SAP CardCode is
              stored on Customer.custom_sap_cardcode; currency and the
              SAP sales person are carried over.
- Sales Persons: /SalesPersons — created in ERPNext if missing so the
              customer's route/rep is visible.
"""

import frappe
from frappe import _
from frappe.utils import cint

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    get_settings,
    integration_enabled,
    log_sap,
)


def _bool(sap_value):
    return sap_value == "tYES"


def _first_leaf(doctype):
    return frappe.db.get_value(doctype, {"is_group": 0}, "name")


def _root_group(doctype, parent_field):
    # ("is", "not set") compiles to IS NULL OR '' — a plain IN ('', NULL)
    # never matches NULL parents.
    from frappe.utils.nestedset import get_root_of
    try:
        return get_root_of(doctype)
    except Exception:
        return frappe.db.get_value(
            doctype, {"is_group": 1, parent_field: ("is", "not set")}, "name"
        )


def _ensure_uom(uom_name):
    uom_name = (uom_name or "").strip() or "Nos"
    if not frappe.db.exists("UOM", uom_name):
        frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(ignore_permissions=True)
    return uom_name


# --------------------------------------------------------------- items
def sync_items():
    settings = get_settings()
    client = SAPClient(settings)

    filters = ["SalesItem eq 'tYES'"]
    group_codes = [c.strip() for c in (settings.sap_item_group_codes or "").split(",") if c.strip()]
    if group_codes:
        group_filter = " or ".join(f"ItemsGroupCode eq {int(c)}" for c in group_codes)
        filters.append(f"({group_filter})")

    rows = client.get_all("Items", params={
        "$select": "ItemCode,ItemName,InventoryUOM,ManageBatchNumbers,ItemsGroupCode,Frozen,Valid",
        "$filter": " and ".join(filters),
    })

    item_group = settings.default_item_group or _first_leaf("Item Group")
    created = updated = 0
    for row in rows:
        if row.get("Frozen") == "tYES" or row.get("Valid") == "tNO":
            continue
        code = row.get("ItemCode")
        name = (row.get("ItemName") or code or "").strip()
        if not code:
            continue
        if frappe.db.exists("Item", code):
            if frappe.db.get_value("Item", code, "item_name") != name:
                frappe.db.set_value("Item", code, "item_name", name, update_modified=False)
                updated += 1
            continue
        frappe.get_doc({
            "doctype": "Item",
            "item_code": code,
            "item_name": name,
            "item_group": item_group,
            "stock_uom": _ensure_uom(row.get("InventoryUOM")),
            "is_stock_item": 1,
            "has_batch_no": 1 if _bool(row.get("ManageBatchNumbers")) else 0,
            "is_sales_item": 1,
        }).insert(ignore_permissions=True)
        created += 1

    msg = _("Items synced from SAP: {0} created, {1} updated, {2} scanned").format(created, updated, len(rows))
    log_sap("Masters", "Success", "Items", message=msg)
    frappe.db.commit()
    return msg


# ------------------------------------------------------- sales persons
def sync_sales_persons(client=None):
    """Returns {SalesEmployeeCode: ERPNext Sales Person name}."""
    client = client or SAPClient()
    rows = client.get_all("SalesPersons", params={
        "$select": "SalesEmployeeCode,SalesEmployeeName,Active",
    })
    root = _root_group("Sales Person", "parent_sales_person")
    mapping = {}
    for row in rows:
        code = row.get("SalesEmployeeCode")
        name = (row.get("SalesEmployeeName") or "").strip()
        if not name or code in (None, -1):  # -1 = "No Sales Employee"
            continue
        if not frappe.db.exists("Sales Person", name):
            doc = frappe.get_doc({
                "doctype": "Sales Person",
                "sales_person_name": name,
                "parent_sales_person": root,
                "is_group": 0,
                "enabled": 1 if row.get("Active") in (None, "tYES") else 0,
            })
            # don't auto-provision a cost center/warehouse per synced rep
            doc.flags.skip_auto_provision = True
            doc.insert(ignore_permissions=True)
        mapping[code] = name
    return mapping


# ----------------------------------------------------------- customers
def sync_customers():
    settings = get_settings()
    client = SAPClient(settings)
    sales_person_map = {}
    try:
        sales_person_map = sync_sales_persons(client)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP SalesPersons sync failed")

    rows = client.get_all("BusinessPartners", params={
        "$select": "CardCode,CardName,CardType,Currency,SalesPersonCode,Frozen,Valid",
        "$filter": "CardType eq 'cCustomer'",
    })

    customer_group = settings.default_customer_group or _first_leaf("Customer Group")
    territory = settings.default_territory or _first_leaf("Territory")

    created = updated = 0
    for row in rows:
        if row.get("Frozen") == "tYES" or row.get("Valid") == "tNO":
            continue
        card_code = row.get("CardCode")
        card_name = (row.get("CardName") or card_code or "").strip()
        if not card_code or not card_name:
            continue

        currency = (row.get("Currency") or "").strip()
        if len(currency) != 3:  # '##' = multi-currency BP — leave default
            currency = None
        sales_person = sales_person_map.get(row.get("SalesPersonCode"))

        existing = frappe.db.get_value("Customer", {"custom_sap_cardcode": card_code}, "name")
        if not existing and frappe.db.exists("Customer", card_name):
            # adopt a pre-integration customer ONLY if it isn't already
            # linked to a different SAP card (SAP allows duplicate names)
            if not frappe.db.get_value("Customer", card_name, "custom_sap_cardcode"):
                existing = card_name

        if existing:
            values = {"customer_name": card_name, "custom_sap_cardcode": card_code}
            # never clobber a currency that is already set — changing the
            # billing currency under existing ledger entries breaks postings
            if currency and not frappe.db.get_value("Customer", existing, "default_currency"):
                values["default_currency"] = currency
            if sales_person:
                values["custom_sap_salesperson"] = sales_person
            frappe.db.set_value("Customer", existing, values, update_modified=False)
            updated += 1
            continue

        doc = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": card_name,
            "customer_type": "Company",
            "customer_group": customer_group,
            "territory": territory,
            "default_currency": currency,
            "custom_sap_cardcode": card_code,
            "custom_sap_salesperson": sales_person,
        })
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
        created += 1

    msg = _("Customers synced from SAP: {0} created, {1} updated, {2} scanned").format(created, updated, len(rows))
    log_sap("Masters", "Success", "BusinessPartners", message=msg)
    frappe.db.commit()
    return msg


# ----------------------------------------------------------- scheduler
def scheduled_masters_sync():
    """Hourly job — runs only when auto-sync is switched on."""
    if not integration_enabled("auto_sync_masters"):
        return
    try:
        if cint(get_settings().sync_items):
            sync_items()
        if cint(get_settings().sync_customers):
            sync_customers()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP scheduled masters sync failed")
        log_sap("Masters", "Failed", "scheduled", message=frappe.get_traceback()[-2000:])
