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
from frappe.utils import cint, flt, getdate, today

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


def _ensure_tree_leaf(doctype, name_field, parent_field, leaf_name):
    """Create a leaf node (e.g. an Item Group / Customer Group mirroring a
    SAP group) under the tree root if it doesn't exist. Returns its name,
    or None when it can't be created."""
    leaf_name = (leaf_name or "").strip()
    if not leaf_name:
        return None
    # return the STORED docname (exists() matches case-insensitively on
    # MariaDB — returning the SAP casing would make every re-home
    # comparison a phantom diff that rewrites on each sync)
    stored = frappe.db.get_value(doctype, leaf_name, "name")
    if stored:
        return stored
    from frappe.utils.nestedset import get_root_of
    try:
        root = get_root_of(doctype)
        frappe.get_doc({
            "doctype": doctype,
            name_field: leaf_name,
            parent_field: root,
            "is_group": 0,
        }).insert(ignore_permissions=True)
        return leaf_name
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"SAP sync: could not create {doctype} '{leaf_name}'")
        return None


# ----------------------------------------------------- uoms and groups
def _uom_maps(client):
    """Returns (entry_to_code, group_lines, group_base):
    entry_to_code: SAP UoM AbsEntry -> UoM code
    group_lines:   SAP UoM group AbsEntry -> its definition rows
    group_base:    SAP UoM group AbsEntry -> base UoM AbsEntry."""
    uoms = client.get_all("UnitOfMeasurements", params={"$select": "AbsEntry,Code,Name"})
    entry_to_code = {u.get("AbsEntry"): (u.get("Code") or u.get("Name")) for u in uoms}
    groups = client.get_all("UnitOfMeasurementGroups", params={
        "$select": "AbsEntry,BaseUoM,UoMGroupDefinitionCollection",
    })
    group_lines = {g.get("AbsEntry"): (g.get("UoMGroupDefinitionCollection") or []) for g in groups}
    group_base = {g.get("AbsEntry"): g.get("BaseUoM") for g in groups}
    return entry_to_code, group_lines, group_base


def _next_uoms_idx(item_code):
    return (frappe.db.sql(
        """SELECT COALESCE(MAX(idx), 0) FROM `tabUOM Conversion Detail`
           WHERE parent = %s AND parentfield = 'uoms'""", (item_code,)
    )[0][0] or 0) + 1


def _apply_uom_conversions(item_code, stock_uom, group_entry, inventory_uom_entry,
                           entry_to_code, group_lines, group_base):
    """Mirror a SAP UoM group onto the Item's UOM Conversion table.

    SAP group lines define '1 <alt> = BaseQuantity/AlternateQuantity <base>',
    but ERPNext's conversion_factor is expressed in the ITEM'S stock UoM.
    Since B1 9.0 an item's inventory UoM may be any member of the group —
    when it isn't the base, every factor is rebased by the inventory UoM's
    own factor so quantities convert correctly."""
    lines = group_lines.get(group_entry, [])
    base_entry = group_base.get(group_entry)

    # factor of the item's inventory (stock) UoM relative to the group base
    inv_factor = 1.0
    if inventory_uom_entry and base_entry and inventory_uom_entry != base_entry:
        inv_factor = 0.0
        for line in lines:
            if line.get("AlternateUoM") == inventory_uom_entry:
                alt_qty = flt(line.get("AlternateQuantity")) or 1
                inv_factor = flt(line.get("BaseQuantity")) / alt_qty
                break
        if inv_factor <= 0:
            # inventory UoM not resolvable inside its group — writing factors
            # here would corrupt every conversion, so skip and log
            frappe.log_error(
                f"Item {item_code}: SAP inventory UoM (entry {inventory_uom_entry}) not "
                f"found in UoM group {group_entry}; conversions skipped.",
                "SAP UOM sync")
            return 0

    changed = 0
    # ERPNext expects the stock UOM itself present with factor 1
    if not frappe.db.exists("UOM Conversion Detail",
                            {"parent": item_code, "parenttype": "Item", "uom": stock_uom}):
        frappe.get_doc({
            "doctype": "UOM Conversion Detail", "parenttype": "Item",
            "parentfield": "uoms", "parent": item_code, "idx": _next_uoms_idx(item_code),
            "uom": stock_uom, "conversion_factor": 1,
        }).insert(ignore_permissions=True)

    targets = []
    for line in lines:
        alt_code = entry_to_code.get(line.get("AlternateUoM"))
        alt_qty = flt(line.get("AlternateQuantity")) or 1
        base_factor = flt(line.get("BaseQuantity")) / alt_qty
        if alt_code and base_factor > 0:
            targets.append((alt_code, base_factor / inv_factor))
    # when the stock UoM is an alternate, the group's base UoM is itself a
    # sellable unit — include it, rebased
    if inv_factor != 1.0 and base_entry:
        base_code = entry_to_code.get(base_entry)
        if base_code:
            targets.append((base_code, 1.0 / inv_factor))

    for alt_code, factor in targets:
        uom_name = _ensure_uom(alt_code)
        if uom_name == stock_uom or factor <= 0:
            continue
        existing = frappe.db.get_value(
            "UOM Conversion Detail",
            {"parent": item_code, "parenttype": "Item", "uom": uom_name},
            ["name", "conversion_factor"], as_dict=True,
        )
        if existing:
            if abs(flt(existing.conversion_factor) - factor) > 1e-9:
                frappe.db.set_value("UOM Conversion Detail", existing.name,
                                    "conversion_factor", factor, update_modified=False)
                changed += 1
        else:
            frappe.get_doc({
                "doctype": "UOM Conversion Detail", "parenttype": "Item",
                "parentfield": "uoms", "parent": item_code, "idx": _next_uoms_idx(item_code),
                "uom": uom_name, "conversion_factor": factor,
            }).insert(ignore_permissions=True)
            changed += 1

    if changed:
        frappe.clear_document_cache("Item", item_code)
    return changed


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
        "$select": "ItemCode,ItemName,InventoryUOM,ManageBatchNumbers,ItemsGroupCode,Frozen,Valid,UoMGroupEntry,InventoryUoMEntry",
        "$filter": " and ".join(filters),
    })

    # UoM groups — a failure here must not sink the item sync itself
    entry_to_code, group_lines, group_base = {}, {}, {}
    try:
        entry_to_code, group_lines, group_base = _uom_maps(client)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP UoM groups fetch failed")

    # real SAP item groups (Number -> GroupName), mirrored 1:1 so items
    # keep exactly the grouping they have in SAP. Resolved ONCE per run —
    # a group that fails to create is logged once, not once per item.
    resolved_item_groups = {}
    try:
        for number, group_name in {
            g.get("Number"): g.get("GroupName")
            for g in client.get_all("ItemGroups", params={"$select": "Number,GroupName"})
        }.items():
            resolved_item_groups[number] = _ensure_tree_leaf(
                "Item Group", "item_group_name", "parent_item_group", group_name
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP ItemGroups fetch failed")

    default_group = settings.default_item_group or _first_leaf("Item Group")
    # all codes in this SAP pull — guards the self-heal rename below from
    # ever touching a different SAP item that shares the same name
    sap_codes = {r.get("ItemCode") for r in rows if r.get("ItemCode")}
    # naming-series prefixes (e.g. "STO-ITEM-") — the self-heal only ever
    # renames items whose docname came from the series, i.e. actual
    # victims of the old autoname behaviour, never deliberately-coded items
    series_options = (frappe.get_meta("Item").get_field("naming_series").options or "") \
        if frappe.get_meta("Item").get_field("naming_series") else ""
    series_prefixes = [o.split(".")[0] for o in series_options.split("\n") if o.strip()] or ["STO-ITEM-"]
    created = updated = renamed = conversions = 0
    for row in rows:
        if row.get("Frozen") == "tYES" or row.get("Valid") == "tNO":
            continue
        code = row.get("ItemCode")
        name = (row.get("ItemName") or code or "").strip()
        if not code:
            continue
        stock_uom = _ensure_uom(row.get("InventoryUOM"))
        # the item's REAL SAP group, mirrored into ERPNext (fallback: default)
        resolved_group = resolved_item_groups.get(row.get("ItemsGroupCode"))
        item_group = resolved_group or default_group

        exists = frappe.db.exists("Item", code)
        if not exists:
            # Self-heal items synced before the naming fix: with Stock
            # Settings "Item Naming By = Naming Series", Item.autoname used
            # to replace the SAP code with a series name (STO-ITEM-0001).
            # Adopt such an item back by its exact SAP item name — only an
            # unambiguous, series-named match (never a deliberately-coded
            # or differently-coded SAP item).
            same_name = frappe.get_all(
                "Item",
                filters={"item_name": name, "variant_of": ("is", "not set")},
                or_filters=[["name", "like", f"{p}%"] for p in series_prefixes],
                pluck="name", limit=2,
            )
            if len(same_name) == 1 and same_name[0] != code and same_name[0] not in sap_codes:
                try:
                    from frappe.model.rename_doc import rename_doc as _rename_doc
                    _rename_doc(doctype="Item", old=same_name[0], new=code,
                                ignore_permissions=True, show_alert=False,
                                rebuild_search=False)
                    renamed += 1
                except Exception:
                    frappe.log_error(frappe.get_traceback(),
                                     f"SAP item adopt-rename failed: {same_name[0]} -> {code}")
                exists = frappe.db.exists("Item", code)

        if exists:
            current = frappe.db.get_value("Item", code, ["item_name", "item_group"], as_dict=True)
            changes = {}
            if current.item_name != name:
                changes["item_name"] = name
            # SAP is the master: whenever its group resolves, it wins —
            # but never re-home on a failed resolution (fallback)
            if resolved_group and current.item_group != resolved_group:
                changes["item_group"] = resolved_group
            if changes:
                frappe.db.set_value("Item", code, changes, update_modified=False)
                updated += 1
        else:
            # set_name pins the docname to the SAP code, bypassing
            # Item.autoname — so the site's "Item Naming By" setting can
            # never rename synced items again
            doc = frappe.get_doc({
                "doctype": "Item",
                "item_code": code,
                "item_name": name,
                "item_group": item_group,
                "stock_uom": stock_uom,
                "is_stock_item": 1,
                "has_batch_no": 1 if _bool(row.get("ManageBatchNumbers")) else 0,
                "is_sales_item": 1,
            })
            # set_name pins the docname before autoname can run, so
            # doc.name == doc.item_code == the SAP code, always
            doc.insert(ignore_permissions=True, set_name=code)
            created += 1
        # auto-map the item's SAP UoM group (cartons/pieces etc.)
        if row.get("UoMGroupEntry") in group_lines:
            try:
                conversions += _apply_uom_conversions(
                    code, stock_uom, row["UoMGroupEntry"], row.get("InventoryUoMEntry"),
                    entry_to_code, group_lines, group_base
                )
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"UOM conversion sync failed for {code}")

    msg = _("Items synced from SAP: {0} created, {1} updated, {2} renamed to their SAP code, "
            "{3} UOM conversions mapped, {4} scanned").format(
        created, updated, renamed, conversions, len(rows))
    log_sap("Masters", "Success", "Items", message=msg)
    frappe.db.commit()
    return msg


# --------------------------------------------------------------- taxes
def _current_rate(lines):
    """The rate effective TODAY — not simply the last row, which may be a
    pre-loaded future rate change."""
    if not lines:
        return 0
    current = getdate(today())
    dated = []
    for line in lines:
        try:
            dated.append((getdate(line.get("Effectivefrom")), flt(line.get("Rate"))))
        except Exception:
            dated.append((current, flt(line.get("Rate"))))
    dated.sort(key=lambda pair: pair[0])
    effective = [rate for eff_date, rate in dated if eff_date <= current]
    return effective[-1] if effective else dated[0][1]


def sync_taxes():
    """Pull active SAP VAT groups into Settings → Tax Mapping. The back
    office links each code to an ERPNext Sales Taxes and Charges Template;
    pushed invoice lines then carry the SAP VatGroup."""
    settings = frappe.get_doc("SAP Integration Settings")
    client = SAPClient(settings)
    rows = client.get_all("VatGroups", params={
        "$select": "Code,Name,Category,Inactive,VatGroups_Lines",
        # only output (sales) tax codes — /Invoices rejects input-category
        # VatGroups, so purchase codes must never be mappable here
        "$filter": "Category eq 'bovcOutputTax'",
    })

    existing = {m.sap_tax_code: m for m in (settings.tax_mappings or [])}
    added = refreshed = 0
    for row in rows:
        if row.get("Inactive") == "tYES":
            continue
        code = row.get("Code")
        if not code:
            continue
        rate = _current_rate(row.get("VatGroups_Lines") or [])
        if code in existing:
            m = existing[code]
            if m.tax_name != row.get("Name") or flt(m.rate) != rate:
                m.tax_name, m.rate, m.category = row.get("Name"), rate, row.get("Category")
                refreshed += 1
        else:
            settings.append("tax_mappings", {
                "sap_tax_code": code,
                "tax_name": row.get("Name"),
                "rate": rate,
                "category": row.get("Category"),
            })
            added += 1

    settings.flags.ignore_permissions = True
    settings.save()
    msg = _("Tax codes synced from SAP: {0} added, {1} refreshed, {2} scanned").format(
        added, refreshed, len(rows))
    log_sap("Masters", "Success", "VatGroups", message=msg)
    frappe.db.commit()
    return msg


# ---------------------------------------------------------- currencies
def sync_currencies():
    """Pull SAP currencies and make sure each exists and is enabled in
    ERPNext, so customer currencies from SAP always resolve."""
    client = SAPClient()
    rows = client.get_all("Currencies", params={"$select": "Code,Name"})
    enabled = created = 0
    for row in rows:
        code = (row.get("Code") or "").strip()
        if not code or len(code) > 5 or code == "##":
            continue
        if frappe.db.exists("Currency", code):
            if not frappe.db.get_value("Currency", code, "enabled"):
                frappe.db.set_value("Currency", code, "enabled", 1, update_modified=False)
                enabled += 1
        else:
            frappe.get_doc({
                "doctype": "Currency",
                "currency_name": code,
                "enabled": 1,
            }).insert(ignore_permissions=True)
            created += 1
    msg = _("Currencies synced from SAP: {0} created, {1} enabled, {2} scanned").format(
        created, enabled, len(rows))
    log_sap("Masters", "Success", "Currencies", message=msg)
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
        "$select": "CardCode,CardName,CardType,Currency,SalesPersonCode,Frozen,Valid,GroupCode",
        "$filter": "CardType eq 'cCustomer'",
    })

    # real SAP customer groups (Code -> Name), mirrored 1:1 and resolved
    # once per run (failed creations log once, not once per customer)
    resolved_bp_groups = {}
    try:
        for group_code, group_name in {
            g.get("Code"): g.get("Name")
            for g in client.get_all("BusinessPartnerGroups", params={
                "$select": "Code,Name,Type",
                "$filter": "Type eq 'bbpgt_CustomerGroup'",
            })
        }.items():
            resolved_bp_groups[group_code] = _ensure_tree_leaf(
                "Customer Group", "customer_group_name", "parent_customer_group", group_name
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP BusinessPartnerGroups fetch failed")

    default_customer_group = settings.default_customer_group or _first_leaf("Customer Group")
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
        resolved_group = resolved_bp_groups.get(row.get("GroupCode"))
        customer_group = resolved_group or default_customer_group

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
            # SAP is the master: whenever its group resolves, it wins —
            # but never re-home on a failed resolution (fallback)
            if resolved_group and \
                    frappe.db.get_value("Customer", existing, "customer_group") != resolved_group:
                values["customer_group"] = resolved_group
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
        if cint(get_settings().get("sync_taxes")):
            sync_taxes()
        if cint(get_settings().get("sync_currencies")):
            sync_currencies()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP scheduled masters sync failed")
        log_sap("Masters", "Failed", "scheduled", message=frappe.get_traceback()[-2000:])
