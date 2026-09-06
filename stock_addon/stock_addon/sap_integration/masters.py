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
    single_flight,
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


def resolve_item(sap_code):
    """The ERPNext item for a SAP ItemCode, or None.

    Items synced from here are named after their SAP code, so the docname
    usually IS the code — but an item that existed before the
    integration, or a site that names items by series, carries the code
    in item_code under a different docname. Checking only the docname
    reports a present item as missing, which reads as "sync the items"
    when syncing will never help.
    """
    code = (sap_code or "").strip()
    if not code:
        return None
    if frappe.db.exists("Item", code):
        return code
    return frappe.db.get_value("Item", {"item_code": code}, "name")


def _normalise(text):
    """Fold case and whitespace so 'Bakers  Flour ' == 'bakers flour'."""
    return " ".join((text or "").split()).casefold()


def _adoption_index(series_prefixes):
    """Series-named items indexed by normalised name and by barcode.

    Built once per run so a SAP item can find a pre-existing record for
    the same product and rename it, instead of creating a second item
    alongside it. Exact-name matching alone missed twins that differed
    only by case or spacing.
    """
    by_name, by_barcode = {}, {}
    if not series_prefixes:
        return by_name, by_barcode

    conditions = " OR ".join(["item.name LIKE %s"] * len(series_prefixes))
    values = tuple(p + "%" for p in series_prefixes)
    rows = frappe.db.sql(
        f"""
        SELECT item.name, item.item_name
        FROM `tabItem` item
        WHERE ({conditions})
          AND IFNULL(item.variant_of, '') = ''
        """,
        values, as_dict=True,
    )
    for row in rows:
        by_name.setdefault(_normalise(row.item_name), []).append(row.name)

    barcodes = frappe.db.sql(
        f"""
        SELECT barcode.parent AS name, barcode.barcode
        FROM `tabItem Barcode` barcode
        JOIN `tabItem` item ON item.name = barcode.parent
        WHERE ({conditions})
        """,
        values, as_dict=True,
    )
    for row in barcodes:
        code = (row.barcode or "").strip()
        if code:
            by_barcode.setdefault(code, []).append(row.name)
    return by_name, by_barcode


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
    with single_flight("items") as lock:
        if not lock.acquired:
            return _("An item sync is already running — try again in a moment.")
        return _sync_items()


def _sync_items():
    settings = get_settings()
    client = SAPClient(settings)

    filters = ["SalesItem eq 'tYES'"]
    group_codes = [c.strip() for c in (settings.sap_item_group_codes or "").split(",") if c.strip()]
    if group_codes:
        group_filter = " or ".join(f"ItemsGroupCode eq {int(c)}" for c in group_codes)
        filters.append(f"({group_filter})")

    rows = client.get_all("Items", params={
        "$select": "ItemCode,ItemName,InventoryUOM,ManageBatchNumbers,ItemsGroupCode,Frozen,Valid,UoMGroupEntry,InventoryUoMEntry,BarCode",
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
    adopt_by_name, adopt_by_barcode = _adoption_index(series_prefixes)
    created = updated = renamed = conversions = skipped = 0
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
            # Adopt a pre-existing record for this same product instead of
            # creating a twin beside it. Candidates are series-named items
            # (victims of the old autoname behaviour) matched by barcode
            # first, then by normalised name. A match is CLAIMED — popped
            # from the index — so two SAP items can never adopt the same
            # ERPNext record.
            barcode = (row.get("BarCode") or "").strip()
            candidates = adopt_by_barcode.pop(barcode, None) if barcode else None
            if not candidates:
                candidates = adopt_by_name.pop(_normalise(name), None)

            if candidates and len(candidates) == 1 and candidates[0] != code \
                    and candidates[0] not in sap_codes:
                try:
                    from frappe.model.rename_doc import rename_doc as _rename_doc
                    _rename_doc(doctype="Item", old=candidates[0], new=code,
                                ignore_permissions=True, show_alert=False,
                                rebuild_search=False)
                    renamed += 1
                except Exception:
                    frappe.log_error(frappe.get_traceback(),
                                     f"SAP item adopt-rename failed: {candidates[0]} -> {code}")
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
            # doc.name == doc.item_code == the SAP code, always.
            # A concurrent run that created it a moment ago surfaces as a
            # duplicate here — treat that as "already synced", never as a
            # reason to abort the whole run or to write a second record.
            try:
                doc.insert(ignore_permissions=True, set_name=code)
                created += 1
            except frappe.DuplicateEntryError:
                frappe.db.rollback()
                skipped += 1
                continue
            except Exception:
                frappe.db.rollback()
                frappe.log_error(frappe.get_traceback(), f"SAP item sync failed for {code}")
                skipped += 1
                continue
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
            "{3} UOM conversions mapped, {4} skipped, {5} scanned").format(
        created, updated, renamed, conversions, skipped, len(rows))
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


# ------------------------------------------- uniform customer codes
# SAP B1 OCRD.CardCode is nvarchar(15) and is the business partner's
# identity. ERPNext sites that name customers by Customer Name end up
# with IDs like "NDIBAIRIRA MARIA" — too long, with spaces — which SAP
# rejects outright. This gives every un-coded customer a uniform code
# (prefix + running number) and makes it the ERPNext ID as well, so the
# two systems address the same partner the same way.
SAP_CARDCODE_MAX = 15


def _next_code_number(prefix):
    """Continue the sequence from the highest existing <prefix><digits>."""
    rows = frappe.db.sql(
        """
        SELECT custom_sap_cardcode FROM `tabCustomer`
        WHERE custom_sap_cardcode LIKE %s
        """,
        (prefix + "%",),
    )
    highest = 0
    for (code,) in rows:
        tail = (code or "")[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


@frappe.whitelist()
def assign_customer_codes(limit=500):
    """Assign a uniform SAP CardCode to every customer that lacks one,
    and rename the customer to it. Idempotent — customers that already
    have a code are skipped."""
    frappe.only_for(("System Manager", "Administrator"))

    settings = get_settings()
    prefix = ((settings.get("customer_code_prefix") or "C").strip().upper())
    if not prefix.isalnum():
        frappe.throw(_("Customer Code Prefix must be letters/numbers only."))

    names = frappe.get_all(
        "Customer",
        or_filters=[
            ["custom_sap_cardcode", "is", "not set"],
            ["custom_sap_cardcode", "=", ""],
        ],
        pluck="name",
        limit=int(limit),
        order_by="creation asc",
    )

    number = _next_code_number(prefix)
    assigned = failed = 0
    for name in names:
        # find the next free code (a manual entry may already hold one)
        code = f"{prefix}{number:05d}"
        while frappe.db.exists("Customer", code) or frappe.db.exists(
            "Customer", {"custom_sap_cardcode": code}
        ):
            number += 1
            code = f"{prefix}{number:05d}"
        if len(code) > SAP_CARDCODE_MAX:
            frappe.throw(
                _("Generated code {0} exceeds SAP's {1}-character CardCode limit — use a shorter prefix.")
                .format(code, SAP_CARDCODE_MAX)
            )

        try:
            frappe.db.set_value("Customer", name, "custom_sap_cardcode", code,
                                update_modified=False)
            if name != code:
                from frappe.model.rename_doc import rename_doc as _rename_doc
                _rename_doc(doctype="Customer", old=name, new=code,
                            ignore_permissions=True, show_alert=False,
                            rebuild_search=False)
            assigned += 1
            number += 1
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(),
                             f"SAP customer code assignment failed for {name}")
            failed += 1

    msg = _("Customer codes assigned: {0} coded, {1} failed ({2} scanned)").format(
        assigned, failed, len(names))
    log_sap("Masters", "Failed" if failed else "Success", "assign_customer_codes", message=msg)
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
    """Returns {SalesEmployeeCode: ERPNext Sales Person name}.

    Existing records win. A route is set up here once — WAKISO-ROUTE with
    its van warehouse, serving warehouse, cash account, route names and
    employee link — and creating a second Sales Person from SAP's own
    spelling of the name would leave two records for one rep, only one of
    which the app can use. So a SAP employee is matched to what is already
    here: first on the SAP code stamped on the record, then on the name
    (case and spacing folded), and a new record only when neither finds
    anything.

    A match by name stamps the code onto the record, so the link survives
    somebody renaming the rep on either side.
    """
    client = client or SAPClient()
    rows = client.get_all("SalesPersons", params={
        "$select": "SalesEmployeeCode,SalesEmployeeName,Active",
    })
    root = _root_group("Sales Person", "parent_sales_person")

    # The code field arrives with the fixtures; a sync running before they
    # are applied must fall back to matching on the name rather than
    # blowing up on an unknown column.
    has_code = bool(frappe.get_meta("Sales Person").get_field("custom_sap_sales_employee_code"))

    fields = ["name", "sales_person_name"]
    if has_code:
        fields.append("custom_sap_sales_employee_code")
    known = frappe.get_all("Sales Person", fields=fields)

    by_code = {
        str(r.get("custom_sap_sales_employee_code")).strip(): r.name
        for r in known
        if (r.get("custom_sap_sales_employee_code") or "").strip()
    }
    by_name = {}
    for r in known:
        for candidate in (r.sales_person_name, r.name):
            key = _normalise(candidate)
            if key:
                by_name.setdefault(key, r.name)

    mapping = {}
    for row in rows:
        code = row.get("SalesEmployeeCode")
        name = (row.get("SalesEmployeeName") or "").strip()
        if not name or code in (None, -1):  # -1 = "No Sales Employee"
            continue

        existing = by_code.get(str(code).strip()) or by_name.get(_normalise(name))
        if existing:
            if not (frappe.db.get_value("Sales Person", existing,
                                        "custom_sap_sales_employee_code") or "").strip():
                frappe.db.set_value("Sales Person", existing,
                                    "custom_sap_sales_employee_code", str(code),
                                    update_modified=False)
            mapping[code] = existing
            continue

        doc = frappe.get_doc({
            "doctype": "Sales Person",
            "sales_person_name": name,
            "parent_sales_person": root,
            "is_group": 0,
            "enabled": 1 if row.get("Active") in (None, "tYES") else 0,
            "custom_sap_sales_employee_code": str(code),
        })
        # don't auto-provision a cost center/warehouse per synced rep
        doc.flags.skip_auto_provision = True
        doc.insert(ignore_permissions=True)
        by_code[str(code)] = doc.name
        by_name.setdefault(_normalise(name), doc.name)
        mapping[code] = doc.name

    return mapping


def _set_customer_sales_team(customer, sales_person):
    """Point a customer's Sales Team at one rep, at 100%.

    This is the field that matters. custom_sap_salesperson records SAP's
    answer but nothing reads it: the Sales Pro app filters customers with
    ['sales_team.sales_person', '=', <rep>], the Customers tab on Sales
    Person reads the same table, and a Sales Order picks its sales team up
    from there too. Writing only the custom field left every one of them
    blank.

    Written as child rows rather than through a document save because this
    runs for every customer on an hourly sync — a save would re-validate
    and re-version each one. SAP owns the whole table here (one rep, at
    100%), so there is nothing a save would add. ERPNext requires the
    percentages to total exactly 100, which one row at 100 satisfies.

    Returns 1 when something changed.
    """
    rows = frappe.get_all(
        "Sales Team",
        filters={"parenttype": "Customer", "parent": customer},
        fields=["name", "sales_person", "allocated_percentage"],
    )
    if (len(rows) == 1 and rows[0].sales_person == sales_person
            and flt(rows[0].allocated_percentage) == 100):
        return 0

    frappe.db.delete("Sales Team", {"parenttype": "Customer", "parent": customer})
    frappe.get_doc({
        "doctype": "Sales Team",
        "parenttype": "Customer",
        "parent": customer,
        "parentfield": "sales_team",
        "sales_person": sales_person,
        "allocated_percentage": 100,
        "idx": 1,
    }).insert(ignore_permissions=True)
    return 1


# ----------------------------------------------------------- customers
def sync_customers():
    with single_flight("customers") as lock:
        if not lock.acquired:
            return _("A customer sync is already running — try again in a moment.")
        return _sync_customers()


def _sync_customers():
    settings = get_settings()
    client = SAPClient(settings)
    sales_person_map = {}
    try:
        sales_person_map = sync_sales_persons(client)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP SalesPersons sync failed")

    rows = client.get_all("BusinessPartners", params={
        "$select": "CardCode,CardName,CardType,Currency,SalesPersonCode,Frozen,Valid,GroupCode,PriceListNum",
        "$filter": "CardType eq 'cCustomer'",
    })

    # SAP price list number -> ERPNext Price List name. Without this every
    # customer falls back to Standard Selling regardless of the list they
    # are on in SAP. A pricing failure must not sink the customer sync.
    price_list_by_num = {}
    try:
        from stock_addon.stock_addon.sap_integration.pricing import _price_list_map
        price_list_by_num = _price_list_map(client)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP price list map fetch failed")

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
    renamed = 0
    reassigned = 0

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
        price_list = price_list_by_num.get(row.get("PriceListNum"))

        existing = frappe.db.get_value("Customer", {"custom_sap_cardcode": card_code}, "name")
        if not existing and frappe.db.exists("Customer", card_name):
            # adopt a pre-integration customer ONLY if it isn't already
            # linked to a different SAP card (SAP allows duplicate names)
            if not frappe.db.get_value("Customer", card_name, "custom_sap_cardcode"):
                existing = card_name

        # SAP CardCode is the identity — make it the ERPNext docname too.
        # With Selling Settings "Customer Naming By = Customer Name" the
        # docname would otherwise be the person's name, which is neither
        # uniform nor a legal SAP CardCode (OCRD.CardCode is 15 chars).
        if existing and existing != card_code and not frappe.db.exists("Customer", card_code):
            try:
                from frappe.model.rename_doc import rename_doc as _rename_doc
                _rename_doc(doctype="Customer", old=existing, new=card_code,
                            ignore_permissions=True, show_alert=False,
                            rebuild_search=False)
                existing = card_code
                renamed += 1
            except Exception:
                frappe.log_error(frappe.get_traceback(),
                                 f"SAP customer rename failed: {existing} -> {card_code}")

        if existing:
            values = {"customer_name": card_name, "custom_sap_cardcode": card_code}
            # never clobber a currency that is already set — changing the
            # billing currency under existing ledger entries breaks postings
            if currency and not frappe.db.get_value("Customer", existing, "default_currency"):
                values["default_currency"] = currency
            if sales_person:
                values["custom_sap_salesperson"] = sales_person
            # SAP owns which list a customer is priced on
            if price_list:
                values["default_price_list"] = price_list
            # SAP is the master: whenever its group resolves, it wins —
            # but never re-home on a failed resolution (fallback)
            if resolved_group and \
                    frappe.db.get_value("Customer", existing, "customer_group") != resolved_group:
                values["customer_group"] = resolved_group
            frappe.db.set_value("Customer", existing, values, update_modified=False)
            # SAP is the master where it actually names a rep. Where it
            # names none (SalesPersonCode -1), whatever is on the customer
            # stays — an hourly sync must not quietly undo an assignment
            # made on the Sales Person's Customers tab.
            if sales_person:
                reassigned += _set_customer_sales_team(existing, sales_person)
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
            "default_price_list": price_list,
            # the table the app, the Customers tab and Sales Orders read
            "sales_team": ([{"sales_person": sales_person, "allocated_percentage": 100}]
                           if sales_person else []),
        })
        doc.flags.ignore_mandatory = True
        # set_name pins the docname to the SAP CardCode, bypassing the
        # site's "Customer Naming By" setting entirely
        doc.insert(ignore_permissions=True, set_name=card_code)
        created += 1

    msg = _("Customers synced from SAP: {0} created, {1} updated, {2} renamed to their SAP CardCode, "
            "{3} put on a sales person's round, {4} scanned").format(
                created, updated, renamed, reassigned, len(rows))
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
