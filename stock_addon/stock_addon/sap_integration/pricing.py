# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP B1 → ERPNext price determination.

SAP resolves a line price in this order, first match wins:

    1. Special Prices for the business partner
    2. Discount Groups
    3. Period and Volume Discounts
    4. the Price List assigned to the partner

ERPNext reproduces that with Pricing Rule ``priority`` (higher wins) and
"apply multiple pricing rules" left OFF, so exactly one tier applies —
the same first-match-wins behaviour:

    tier                        ERPNext                       priority
    Special Price               Item Price (customer-scoped)      20
                                + Pricing Rule (absolute Rate)
    Discount Group              Pricing Rule (Item Group, %)      15
    Period and Volume Discount  Pricing Rule (dates + qty)        10
    Price List                  Item Price                      base

Special prices are written twice on purpose: as a customer-scoped Item
Price so the Sales Pro app (which reads Item Price directly) shows the
right number, and as an absolute-rate Pricing Rule so it outranks the
lower tiers instead of being discounted again on top.

Every record is keyed deterministically on its SAP identity, so
re-syncing updates in place and never duplicates.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    single_flight,
    get_settings,
    integration_enabled,
    log_sap,
)
from stock_addon.stock_addon.sap_integration.masters import resolve_item

PRIORITY_SPECIAL_PRICE = "20"
PRIORITY_DISCOUNT_GROUP = "15"
PRIORITY_PERIOD_VOLUME = "10"


# ------------------------------------------------------------ helpers
def _docname(*parts):
    """Deterministic, filesystem-safe Pricing Rule name from SAP keys."""
    raw = "SAP-" + "-".join(str(p) for p in parts if p not in (None, ""))
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in raw)
    return safe[:140]


def _company():
    return (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {}, "name")
    )


def _price_list_map(client=None):
    """SAP PriceListNo -> ERPNext Price List name (only lists that exist)."""
    client = client or SAPClient()
    mapping = {}
    # no $select: field names vary by B1 version and a bad one is a 400
    for row in client.get_all("PriceLists"):
        number, name = row.get("PriceListNo"), (row.get("PriceListName") or "").strip()
        if number is None or not name:
            continue
        existing = frappe.db.get_value("Price List", name, "name")
        if existing:
            mapping[number] = existing
    return mapping


def _ensure_price_list(name, currency):
    name = (name or "").strip()
    if not name:
        return None
    existing = frappe.db.get_value("Price List", name, "name")
    if existing:
        return existing
    frappe.get_doc({
        "doctype": "Price List",
        "price_list_name": name,
        "selling": 1,
        "buying": 0,
        "enabled": 1,
        "currency": currency or frappe.db.get_default("currency") or "UGX",
    }).insert(ignore_permissions=True)
    return name


def _customer_for(card_code):
    return frappe.db.get_value("Customer", {"custom_sap_cardcode": card_code}, "name")


def _item_price_has(field):
    """Item Price lost min_qty in newer ERPNext — never reference a
    column this site does not have."""
    return bool(frappe.get_meta("Item Price").get_field(field))


# The fields ERPNext itself uses to decide two Item Prices are the same
# document (item_price.py check_duplicates). Ours has to agree with it,
# or we look for a row it will later object to.
ITEM_PRICE_KEY = ("uom", "valid_from", "valid_upto", "customer", "supplier",
                  "batch_no", "packing_unit")

# Fixed identifier: frappe interpolates savepoint names straight into SQL.
ITEM_PRICE_SAVEPOINT = "stock_addon_item_price"


def _same_key(row, wanted, skip=()):
    """Compare on ERPNext's terms: an unset field matches NULL *or* empty.

    Predicting what ERPNext will store has been wrong three times now —
    customer arrived as NULL where we looked for "", uom is fetched from
    the item's stock UOM before the check runs, and valid_from defaults
    to today. Fields in `skip` are ones we did not ask for and therefore
    cannot predict; _find_clash below is what catches anything still
    mis-modelled, by asking after the fact instead of guessing before.
    """
    for field in ITEM_PRICE_KEY:
        if field in skip:
            continue
        ours, theirs = wanted.get(field), row.get(field)
        if field == "packing_unit":
            if flt(ours) != flt(theirs):
                return False
            continue
        if (ours or "") != (theirs or ""):
            return False
    return True


def _candidates(item_code, price_list, supports_min_qty):
    return frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "price_list": price_list},
        fields=["name", "price_list_rate"] + list(ITEM_PRICE_KEY)
        + (["min_qty"] if supports_min_qty else []),
    )


def _find_clash(doc):
    """The stored row ERPNext just called a duplicate of `doc`.

    Read from the DOCUMENT's own values, not from what we meant to write:
    by the time check_duplicates throws, frappe has already fetched uom
    from the item and stamped valid_from with today's default, and those
    resolved values are what it compared. Asking the document is the one
    version of the key that cannot drift from ERPNext's.
    """
    wanted = {field: doc.get(field) for field in ITEM_PRICE_KEY}
    for row in _candidates(doc.item_code, doc.price_list, False):
        if row.name != doc.name and _same_key(row, wanted):
            return row
    return None


def _upsert_item_price(item_code, price_list, rate, customer=None,
                       min_qty=0, valid_from=None, valid_upto=None, currency=None):
    """Item Price keyed on its natural identity, so a re-sync updates the
    same row instead of stacking duplicates.

    Quantity tiers are only expressible here when the site's Item Price
    carries min_qty; otherwise the tier lives solely in its Pricing Rule,
    which always supports quantity ranges.
    """
    item_code = resolve_item(item_code)
    if not item_code or not price_list:
        return None
    # No price on that list is not a price of zero. SAP returns an entry
    # for every list an item touches, so writing the empty ones would put
    # items on lists they were never priced on — and a 0.00 Item Price is
    # worse than none, because it wins over the list the item really
    # belongs to. Enforced here rather than at each caller: this is the
    # only door into the table.
    if flt(rate) <= 0:
        return None
    supports_min_qty = _item_price_has("min_qty")
    if flt(min_qty) and not supports_min_qty:
        return None

    # Narrow in SQL on the two fields that are always set, then decide on
    # the full key in Python — SQL cannot express "NULL or empty" without
    # a clause per field, and getting that subtly wrong is what broke it.
    # uom is NOT ours to leave blank. Item Price declares
    # fetch_from = item_code.stock_uom, and frappe applies that in
    # _validate_links() — which insert() calls BEFORE the validate that
    # runs check_duplicates. So the row ERPNext compares always carries
    # the item's stock UOM, and looking one up as blank never matches it:
    # we insert, and ERPNext throws on the duplicate we just failed to
    # find. Ask for the same value it will fetch.
    stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")

    # Every field in ERPNext's key, including the ones we leave empty.
    wanted = {
        "uom": stock_uom,
        "valid_from": valid_from,
        "valid_upto": valid_upto,
        "customer": customer,
        "supplier": None,
        "batch_no": None,
        "packing_unit": 0,
    }
    # valid_from defaults to Today, so a row written on any earlier run
    # carries that run's date. Matching on it would miss the row and add
    # a fresh one every day the sync runs; when we did not ask for a date
    # we ignore whichever one is stored.
    skip = ("valid_from",) if not valid_from else ()

    # Compare rates at the field's own precision. Plain float equality
    # calls 1000.0 and 1000.0000001 different, which rewrites rows on
    # every run and reports price changes that never happened.
    precision = frappe.get_precision("Item Price", "price_list_rate") or 2

    existing = None
    for row in _candidates(item_code, price_list, supports_min_qty):
        if supports_min_qty and flt(row.get("min_qty")) != flt(min_qty):
            continue
        if _same_key(row, wanted, skip=skip):
            existing = row
            break

    if existing:
        if flt(existing.price_list_rate, precision) == flt(rate, precision):
            return None                       # already right — leave it alone
        frappe.db.set_value("Item Price", existing.name, "price_list_rate", flt(rate),
                            update_modified=False)
        return "updated"
    payload = {
        "doctype": "Item Price",
        "item_code": item_code,
        "price_list": price_list,
        "price_list_rate": flt(rate),
        "customer": customer,
        "valid_from": valid_from,
        "valid_upto": valid_upto,
        "selling": 1,
        "currency": currency,
        # Set it rather than letting the fetch decide silently, so the row
        # written is the row that was looked for.
        "uom": stock_uom,
    }
    if supports_min_qty:
        payload["min_qty"] = flt(min_qty)
    doc = frappe.get_doc(payload)
    doc.flags.ignore_permissions = True

    # If ERPNext still considers this a duplicate, believe it rather than
    # our model of its rules, and update the row it points at. One item
    # raising this used to kill the whole item_prices stage — thousands
    # of good prices lost to one row we could not predict.
    frappe.db.savepoint(ITEM_PRICE_SAVEPOINT)
    try:
        doc.insert(ignore_permissions=True)
    except frappe.ValidationError:
        frappe.db.rollback(save_point=ITEM_PRICE_SAVEPOINT)
        clash = _find_clash(doc)
        if not clash:
            raise                    # a different validation problem — say so
        frappe.clear_last_message()
        if flt(clash.price_list_rate, precision) == flt(rate, precision):
            return None
        frappe.db.set_value("Item Price", clash.name, "price_list_rate", flt(rate),
                            update_modified=False)
        return "updated"
    frappe.db.release_savepoint(ITEM_PRICE_SAVEPOINT)
    return "added"


def _upsert_pricing_rule(name, values, item_codes=None, item_groups=None):
    """Create or refresh a Pricing Rule under a deterministic name."""
    payload = dict(values)
    payload["doctype"] = "Pricing Rule"
    payload.setdefault("selling", 1)
    payload.setdefault("price_or_product_discount", "Price")
    payload.setdefault("apply_multiple_pricing_rules", 0)
    payload.setdefault("company", _company())

    if frappe.db.exists("Pricing Rule", name):
        doc = frappe.get_doc("Pricing Rule", name)
        for key, value in payload.items():
            if key != "doctype":
                doc.set(key, value)
    else:
        doc = frappe.get_doc(payload)

    doc.set("items", [])
    for code in (item_codes or []):
        item = resolve_item(code)
        if item:
            doc.append("items", {"item_code": item})
    doc.set("item_groups", [])
    for group in (item_groups or []):
        if frappe.db.exists("Item Group", group):
            doc.append("item_groups", {"item_group": group})

    # nothing to price against — skip rather than save an inert rule
    if not doc.get("items") and not doc.get("item_groups"):
        return 0

    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    if doc.get("__islocal") is None and frappe.db.exists("Pricing Rule", name):
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True, set_name=name)
    return 1


def _date(value):
    if not value:
        return None
    try:
        return getdate(str(value)[:10])
    except Exception:
        return None


# ------------------------------------------------------- 4. price list
def sync_price_lists(client):
    """SAP price lists -> ERPNext Price Lists (the base tier)."""
    rows = client.get_all("PriceLists")
    created = 0
    for row in rows:
        if row.get("Active") == "tNO":
            continue
        name = (row.get("PriceListName") or "").strip()
        if not name:
            continue
        if not frappe.db.exists("Price List", name):
            _ensure_price_list(name, row.get("PrimeCurrency"))
            created += 1
    return f"price lists: {created} created, {len(rows)} scanned"


def sync_item_prices(client):
    """Each item's price on each SAP list -> Item Price."""
    price_lists = _price_list_map(client)
    if not price_lists:
        return "item prices: skipped (no SAP price lists matched ERPNext)"

    rows = client.get_all("Items", params={
        "$select": "ItemCode,ItemPrices,Valid,Frozen",
        "$filter": "SalesItem eq 'tYES'",
    })
    added = updated = unchanged = unpriced = 0
    for row in rows:
        if row.get("Frozen") == "tYES" or row.get("Valid") == "tNO":
            continue
        item_code = row.get("ItemCode")
        for entry in (row.get("ItemPrices") or []):
            price_list = price_lists.get(entry.get("PriceList"))
            if not price_list:
                continue                      # a SAP list ERPNext does not have
            price = flt(entry.get("Price"))
            if price <= 0:
                # SAP lists every price list against every item; the ones
                # it holds no price for are skipped, not written as zero.
                unpriced += 1
                continue
            outcome = _upsert_item_price(
                item_code, price_list, price, currency=entry.get("Currency")
            )
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1
    frappe.db.commit()
    return (f"item prices: {added} added, {updated} price changes, "
            f"{unchanged} already correct, {unpriced} skipped (no price on that list)")


# --------------------------------------------------- 1. special prices
def sync_special_prices(client):
    """Special Prices for a business partner — the top tier.

    Written as a customer-scoped Item Price (so the mobile app shows it)
    AND as an absolute-rate Pricing Rule at priority 20, which is what
    stops a Discount Group from discounting the special price again.
    """
    rows = client.get_all("SpecialPrices", params={
        "$select": "ItemCode,CardCode,Price,Currency,DiscountPercent,PriceListNum,SpecialPriceDataAreas",
    })
    prices = rules = 0
    no_customer = no_item = 0
    for row in rows:
        item_code, card_code = row.get("ItemCode"), row.get("CardCode")
        customer = _customer_for(card_code)
        # a silent skip here is why a run can report "0 written, N scanned"
        if not customer:
            no_customer += 1
            continue
        if not resolve_item(item_code):
            no_item += 1
            continue

        price_list = frappe.db.get_value("Customer", customer, "default_price_list") \
            or frappe.db.get_single_value("Selling Settings", "selling_price_list")

        # header price, plus any dated/quantity sub-rows SAP hangs off it
        variants = [(flt(row.get("Price")), 0, None, None)]
        for area in (row.get("SpecialPriceDataAreas") or []):
            area_price = flt(area.get("Price") or row.get("Price"))
            date_from, date_to = _date(area.get("DateFrom")), _date(area.get("DateTo"))
            quantity_areas = area.get("SpecialPriceQuantityAreas") or []
            if quantity_areas:
                for qty_area in quantity_areas:
                    variants.append((
                        flt(qty_area.get("Price") or area_price),
                        flt(qty_area.get("Amount")),   # qty break
                        date_from, date_to,
                    ))
            else:
                variants.append((area_price, 0, date_from, date_to))

        for price, min_qty, date_from, date_to in variants:
            if price <= 0:
                continue
            if _upsert_item_price(
                item_code, price_list, price, customer=customer,
                min_qty=min_qty, valid_from=date_from, valid_upto=date_to,
                currency=row.get("Currency"),
            ):
                prices += 1
            rules += _upsert_pricing_rule(
                _docname("SP", card_code, item_code, int(min_qty), date_from or ""),
                {
                    "title": f"SAP Special Price {card_code} {item_code}"[:140],
                    "apply_on": "Item Code",
                    "applicable_for": "Customer",
                    "customer": customer,
                    "rate_or_discount": "Rate",
                    "rate": price,
                    "min_qty": min_qty,
                    "valid_from": date_from,
                    "valid_upto": date_to,
                    "priority": PRIORITY_SPECIAL_PRICE,
                },
                item_codes=[item_code],
            )
    frappe.db.commit()
    detail = ""
    if no_customer or no_item:
        detail = f" (skipped: {no_customer} unknown CardCode, {no_item} unknown ItemCode)"
    return f"special prices: {prices} item prices, {rules} rules, {len(rows)} scanned{detail}"


# -------------------------------------------------- 2. discount groups
def sync_discount_groups(client):
    """Discount Groups — a percentage for a partner over an item group."""
    entity = (client.probe_entity(("DiscountGroups", "BPDiscountGroups", "ItemDiscountGroups"))
              or client.find_entity("discount", "group"))
    if not entity:
        return "discount groups: skipped (no matching entity on this SAP install)"
    rows = client.get_all(entity)
    made = 0
    for row in rows:
        customer = _customer_for(row.get("BPCode"))
        discount = flt(row.get("Discount"))
        if not customer or discount <= 0:
            continue
        # ObjectKey carries the item group code for group-type discounts
        group_name = frappe.db.get_value(
            "Item Group", {"name": str(row.get("ObjectKey") or "").strip()}, "name"
        )
        if not group_name:
            continue
        made += _upsert_pricing_rule(
            _docname("DG", row.get("BPCode"), row.get("ObjectKey")),
            {
                "title": f"SAP Discount Group {row.get('BPCode')} {group_name}"[:140],
                "apply_on": "Item Group",
                "applicable_for": "Customer",
                "customer": customer,
                "rate_or_discount": "Discount Percentage",
                "discount_percentage": discount,
                "valid_from": _date(row.get("ValidFrom")),
                "valid_upto": _date(row.get("ValidTo")),
                "priority": PRIORITY_DISCOUNT_GROUP,
            },
            item_groups=[group_name],
        )
    frappe.db.commit()
    return f"discount groups: {made} rules, {len(rows)} scanned"


# ---------------------------------------------- 3. period and volume
def sync_period_volume(client):
    """Period and Volume Discounts — date ranges and quantity breaks on a
    price list, below discount groups in SAP's order."""
    price_lists = _price_list_map(client)
    entity = (client.probe_entity((
                  "PeriodAndVolumeDiscount", "PeriodAndVolumeDiscounts",
                  "VolumeDiscounts", "PeriodDiscounts"))
              or client.find_entity("period", "volume"))
    if not entity:
        return "period/volume: skipped (no matching entity on this SAP install)"
    rows = client.get_all(entity)
    made = 0
    for row in rows:
        item_code = row.get("ItemCode")
        if not resolve_item(item_code):
            continue
        price_list = price_lists.get(row.get("PriceList"))

        for period in (row.get("PeriodDiscount") or []):
            discount = flt(period.get("Discount"))
            if discount <= 0:
                continue
            made += _upsert_pricing_rule(
                _docname("PV", price_list, item_code, "P", period.get("StartDate") or ""),
                {
                    "title": f"SAP Period Discount {item_code}"[:140],
                    "apply_on": "Item Code",
                    "rate_or_discount": "Discount Percentage",
                    "discount_percentage": discount,
                    "valid_from": _date(period.get("StartDate")),
                    "valid_upto": _date(period.get("EndDate")),
                    "for_price_list": price_list,
                    "priority": PRIORITY_PERIOD_VOLUME,
                },
                item_codes=[item_code],
            )

        for volume in (row.get("VolumeDiscount") or []):
            discount = flt(volume.get("Discount"))
            if discount <= 0:
                continue
            made += _upsert_pricing_rule(
                _docname("PV", price_list, item_code, "V", int(flt(volume.get("Quantity")))),
                {
                    "title": f"SAP Volume Discount {item_code}"[:140],
                    "apply_on": "Item Code",
                    "rate_or_discount": "Discount Percentage",
                    "discount_percentage": discount,
                    "min_qty": flt(volume.get("Quantity")),
                    "for_price_list": price_list,
                    "priority": PRIORITY_PERIOD_VOLUME,
                },
                item_codes=[item_code],
            )
    frappe.db.commit()
    return f"period/volume: {made} rules, {len(rows)} scanned"


# -------------------------------------------------------- orchestrator
TIERS = [
    ("price_lists", sync_price_lists),
    ("item_prices", sync_item_prices),
    ("special_prices", sync_special_prices),
    ("discount_groups", sync_discount_groups),
    ("period_volume", sync_period_volume),
]


def sync_pricing():
    """Run every pricing tier. A tier whose SAP entity is not exposed on
    this install is reported and skipped — it must not take the rest of
    the pricing sync down with it."""
    if not integration_enabled():
        return _("SAP integration is disabled — enable it in SAP Integration Settings first.")

    with single_flight("pricing") as lock:
        if not lock.acquired:
            return _("A pricing sync is already running — try again in a moment.")
        return _sync_pricing()


def _sync_pricing():
    client = SAPClient()
    results, failures = [], 0
    for label, fn in TIERS:
        try:
            results.append(fn(client))
        except Exception as e:
            failures += 1
            results.append(f"{label}: FAILED ({str(e)[:120]})")
            log_sap("Masters", "Failed", label, message=frappe.get_traceback()[-2000:])

    summary = " | ".join(results)
    log_sap("Masters", "Failed" if failures else "Success", "sync_pricing", message=summary)
    frappe.db.commit()
    return summary


def scheduled_pricing_sync():
    """Kept as an entry point. Pricing is now one of the masters driven by
    masters.scheduled_masters_sync, on its own interval, so a stale
    scheduler row pointing here lands in the same place instead of
    running a second sweep beside it."""
    from stock_addon.stock_addon.sap_integration.masters import scheduled_masters_sync
    scheduled_masters_sync()


@frappe.whitelist()
def discover_entities(keyword=None):
    """List the EntitySets this SAP install exposes.

    Entity naming differs between B1 versions — this is the quickest way
    to find what a tier should actually bind to when a sync reports
    "no matching entity".
    """
    frappe.only_for(("System Manager", "Administrator"))
    names = SAPClient().entity_sets()
    if keyword:
        needle = keyword.lower()
        names = [n for n in names if needle in n.lower()]
    log_sap("Masters", "Success", "$metadata",
            message=f"{len(names)} entity sets: {', '.join(names)}"[:9000])
    return f"{len(names)} entity sets found — full list written to the SAP Integration Log:\n" + \
           ", ".join(names[:60]) + (" …" if len(names) > 60 else "")
