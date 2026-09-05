# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP B1 → ERPNext van stock pull.

Watches SAP Inventory Transfers (the warehouse team executing the van
request in SAP). Any new transfer whose target line-warehouse is mapped
as a VAN warehouse is mirrored into ERPNext as a submitted Stock Entry,
so the Sales Pro app immediately sees the van's stock.

Modes (SAP Integration Settings → Receipt Mode):
  - "Material Receipt"  (default): stock appears directly in the van
    warehouse — works regardless of ERPNext stock levels elsewhere.
  - "Material Transfer": moves stock from the mapped source warehouse —
    keeps ERPNext's main-warehouse balance in step, but requires that
    stock to exist in ERPNext.

Finding the transfers, first route that works:
  1. The integration number (e.g. U_IntegrationNumber) — every transfer
     request pushed from here carries its ERPNext name, and SAP copies
     that onto the posted transfer, so this matches exactly the transfers
     that originated here. Needs the user field on BOTH documents in SAP.
  2. The van flag (e.g. U_VanRequest) — only usable if that field exists
     on the posted transfer too, which on many installs it does not.
  3. A bounded scan of recent transfers above a fixed floor, keeping
     anything bound for a van warehouse.

No DocEntry ever has to be typed in, and the floor does not advance, so a
transfer passed over on one run is still reachable on the next.

'Check SAP User Fields' on the settings screen reports which of those
fields SAP actually has, on each document — the silent half-configuration
(field on the request but not the transfer) is otherwise invisible.

De-duplication is the same either way: each pulled transfer stamps its
SAP DocEntry on the Stock Entry (custom_sap_docentry, unique), so a
transfer is mirrored exactly once however it was found.
"""

import time

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    get_settings,
    integration_enabled,
    log_sap,
)


# The posted stock transfer (OWTR) is "StockTransfers" on most B1
# installs and "InventoryTransfers" on some. Resolve it against the
# Service Layer's own metadata rather than guessing — and never pick the
# *Request* entity (OWTQ), which is what we PUSH to, not pull from.
# Only names that really are the posted stock transfer (OWTR). Nothing
# else belongs here: probing binds to the first that responds, so a
# near-miss like InventoryGenExits (a goods issue) would silently mirror
# the wrong documents into van warehouses.
PREFERRED_TRANSFER_ENTITIES = (
    "StockTransfers",
    "InventoryTransfers",
    "StockTransfer",
    "InventoryTransfer",
)


def _transfer_entity(client):
    # Probe the real resource paths first — that is the definitive test,
    # and it does not depend on $metadata being parseable.
    found = client.probe_entity(PREFERRED_TRANSFER_ENTITIES)
    if found:
        return found
    # Fallback: scan the metadata for anything transfer-shaped that is not
    # the Request entity (OWTQ — that is the push side) or a draft.
    try:
        available = client.entity_sets()
    except Exception:
        return None
    for name in sorted(available):
        lowered = name.lower()
        if "transfer" in lowered and "request" not in lowered and "draft" not in lowered:
            return name
    return None


def _ensure_batch(item_code, batch_number):
    """Return the ERPNext Batch name for a SAP batch number, creating it
    if needed."""
    existing = frappe.db.get_value("Batch", {"batch_id": batch_number, "item": item_code}, "name")
    if existing:
        return existing
    batch = frappe.get_doc({
        "doctype": "Batch",
        "item": item_code,
        "batch_id": batch_number,
    })
    batch.insert(ignore_permissions=True)
    return batch.name


def _make_stock_entry(transfer, van_lines, code_to_erp, settings):
    mode = settings.receipt_mode or "Material Receipt"
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = mode
    se.purpose = mode
    se.posting_date = str(transfer.get("DocDate") or nowdate())[:10]
    se.set_posting_time = 1
    se.custom_sap_docentry = str(transfer["DocEntry"])

    # Show where this came from. The integration number is the ERPNext
    # request that started it, so the round trip is legible from here
    # rather than only inside SAP.
    reference_udf = (settings.get("integration_number_udf") or "").strip()
    origin = (transfer.get(reference_udf) or "").strip() if reference_udf else ""
    se.remarks = _("SAP transfer #{0}{1}").format(
        transfer.get("DocNum") or transfer.get("DocEntry"),
        _(" — from {0}").format(origin) if origin else "",
    )

    for line in van_lines:
        item_code = line.get("ItemCode")
        if not frappe.db.exists("Item", item_code):
            raise frappe.ValidationError(
                f"Item {item_code} from SAP transfer {transfer['DocNum']} does not "
                "exist in ERPNext — run 'Sync Items' first."
            )
        target_wh = code_to_erp[line["WarehouseCode"]]
        source_wh = None
        if mode == "Material Transfer":
            source_wh = code_to_erp.get(line.get("FromWarehouseCode")) or settings.default_source_warehouse
            if not source_wh:
                raise frappe.ValidationError(
                    f"SAP transfer {transfer['DocNum']}: no ERPNext source warehouse "
                    f"mapped for SAP warehouse '{line.get('FromWarehouseCode')}' and no "
                    "Default Source Warehouse set in SAP Integration Settings."
                )

        stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
        batches = line.get("BatchNumbers") or []
        # one row per batch (or a single row when the item is unbatched)
        portions = (
            [(b.get("BatchNumber"), flt(b.get("Quantity"))) for b in batches]
            or [(None, flt(line.get("Quantity")))]
        )
        for batch_number, qty in portions:
            if not qty:
                continue
            row = se.append("items", {
                "item_code": item_code,
                "qty": qty,
                "transfer_qty": qty,
                "uom": stock_uom,
                "stock_uom": stock_uom,
                "conversion_factor": 1,
                "t_warehouse": target_wh,
                "s_warehouse": source_wh,
                "allow_zero_valuation_rate": 1,
            })
            if batch_number:
                row.batch_no = _ensure_batch(item_code, batch_number)
                if row.meta.get_field("use_serial_batch_fields"):
                    row.use_serial_batch_fields = 1

    se.flags.ignore_permissions = True
    se.insert(ignore_permissions=True)
    se.submit()
    return se


def pull_van_transfers(triggered_by="manual"):
    """Pull new SAP Inventory Transfers into mapped van warehouses.
    Returns a human-readable summary. Every manual run writes a summary
    row to the SAP Integration Log (scheduled runs only log when they
    actually pulled or failed something, to keep the log readable)."""
    if not integration_enabled("pull_van_transfers"):
        return _("Van transfer pull is disabled in SAP Integration Settings.")

    settings = get_settings()
    mappings = settings.warehouse_mappings or []
    van_codes = {m.sap_warehouse_code for m in mappings if cint(m.is_van_warehouse)}
    code_to_erp = {m.sap_warehouse_code: m.erpnext_warehouse for m in mappings}
    if not van_codes:
        return _("No van warehouses mapped — tick 'Is Van Warehouse' on at least one mapping row.")

    # single-flight lock: the per-minute cron, the manual button and any
    # on-demand call from the app must not process the same window at once
    if frappe.cache().get_value("sap_van_pull_running"):
        return _("A van stock pull is already running — try again in a minute.")
    frappe.cache().set_value("sap_van_pull_running", 1, expires_in_sec=90)

    try:
        client = SAPClient(settings)
        entity = _transfer_entity(client)
        if not entity:
            message = _("No stock-transfer entity found on this SAP install — "
                        "run 'Discover SAP Entities' to see what it exposes.")
            log_sap("Pull", "Failed", "InventoryTransfers", message=message)
            return message
        # a fixed go-live floor, never advanced — see the note below
        floor = cint(settings.last_transfer_docentry)

        # How the transfers to consider are chosen.
        #
        # 1. By the van flag, when SAP actually exposes it on this entity.
        #    A UDF added only to the transfer REQUEST (OWTQ) does not exist
        #    on the posted transfer (OWTR); SAP answers 400/201 and we fall
        #    through. That failure is remembered briefly so a per-15s poll
        #    does not re-ask — and re-log — a question already answered.
        #
        # 2. Otherwise scan the most recent transfers and let the van
        #    warehouse decide. No date filter (its syntax differs between
        #    b1s/v1 and v2) and, crucially, no ADVANCING watermark: the
        #    stored DocEntry is a fixed go-live floor, so a transfer that
        #    is not pulled on one run can still be pulled on the next.
        #    Already-pulled ones are excluded by their unique DocEntry.
        by_flag = False
        matched_on = None
        transfers = None

        # 0. Preferred: the integration number. Every transfer request
        #    pushed from here carries the ERPNext document name, and SAP
        #    copies it onto the Stock Transfer — so this finds precisely
        #    the transfers that originated here, whatever their DocEntry.
        reference_udf = (settings.get("integration_number_udf") or "").strip()
        reference_dead_key = f"sap_ref_udf_unusable:{entity}:{reference_udf}"
        if reference_udf and not frappe.cache().get_value(reference_dead_key):
            try:
                transfers = client.get_all(entity, params={
                    "$filter": f"{reference_udf} ne ''",
                    "$orderby": "DocEntry asc",
                })
                by_flag = True
                matched_on = _("matched by integration number")
            except Exception as e:
                frappe.cache().set_value(reference_dead_key, 1, expires_in_sec=600)
                log_sap("Pull", "Failed", entity, message=(
                    f"Integration number field '{reference_udf}' does not exist on {entity} "
                    f"({str(e)[:200]}). Add the same user field to the Stock Transfer in SAP "
                    "so it copies over from the request, or clear it in SAP Integration "
                    "Settings. Falling back — van stock still syncs."))

        udf = (settings.get("van_request_udf") or "").strip()
        udf_value = (settings.get("van_request_udf_value") or "Yes").strip()
        udf_dead_key = f"sap_van_udf_unusable:{entity}:{udf}"

        if not by_flag and udf and not frappe.cache().get_value(udf_dead_key):
            try:
                transfers = client.get_all(entity, params={
                    "$filter": "{0} eq '{1}'".format(udf, udf_value.replace("'", "''")),
                    "$orderby": "DocEntry asc",
                })
                by_flag = True
                matched_on = _("matched by van flag")
            except Exception as e:
                frappe.cache().set_value(udf_dead_key, 1, expires_in_sec=600)
                log_sap("Pull", "Failed", entity, message=(
                    f"Van flag '{udf}' does not exist on {entity} ({str(e)[:200]}). "
                    "Add the same user field to the Stock Transfer in SAP (it is "
                    "currently only on the transfer request), or clear the field in "
                    "SAP Integration Settings. Scanning recent transfers instead — "
                    "van stock still syncs."))

        if not by_flag:
            if not floor:
                # First run: remember where SAP was, so enabling the
                # integration never replays the whole transfer history.
                newest = client.get(entity, params={
                    "$select": "DocEntry", "$orderby": "DocEntry desc", "$top": 1,
                }).get("value") or []
                floor = cint(newest[0]["DocEntry"]) if newest else 0
                frappe.db.set_single_value(
                    "SAP Integration Settings", "last_transfer_docentry", floor)
                frappe.db.commit()
                message = _(
                    "Starting point set at SAP transfer DocEntry {0} — transfers posted "
                    "from now on are pulled automatically."
                ).format(floor)
                log_sap("Pull", "Success", entity, message=message)
                return message

            scan = cint(settings.get("pull_scan_limit")) or 100
            recent = client.get_all(entity, params={
                "$orderby": "DocEntry desc",
                "$top": max(10, min(500, scan)),
            })
            # oldest first, and never anything from before go-live
            transfers = sorted(
                (t for t in recent if cint(t.get("DocEntry")) > floor),
                key=lambda t: cint(t.get("DocEntry")),
            )

        pulled = skipped = failed = 0
        for transfer in transfers:
            entry = cint(transfer.get("DocEntry"))
            van_lines = [
                l for l in (transfer.get("StockTransferLines") or [])
                if l.get("WarehouseCode") in van_codes
            ]
            if not van_lines or frappe.db.exists(
                "Stock Entry", {"custom_sap_docentry": str(entry)}
            ):
                skipped += 1
                continue
            try:
                se = _make_stock_entry(transfer, van_lines, code_to_erp, settings)
                log_sap("Pull", "Success", entity, "Stock Entry", se.name,
                        entry, f"SAP transfer #{transfer.get('DocNum')} → {se.name}")
                pulled += 1
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                log_sap("Pull", "Failed", entity, "Stock Entry", None,
                        entry, frappe.get_traceback()[-2000:])
                failed += 1
                frappe.db.commit()
                # nothing to advance: the next run re-examines this transfer
                # and retries it once the cause is fixed

        how = matched_on or _("scanning recent transfers")
        summary = _("Van stock pull ({4}): {0} pulled, {1} skipped, {2} failed "
                    "(checked {3} SAP transfers)").format(
            pulled, skipped, failed, len(transfers), how
        )
        # manual runs always leave a visible trace; the scheduled and
        # on-demand runs only log when something actually happened, or the
        # log would gain a row every minute
        if triggered_by == "manual" or pulled or failed:
            log_sap("Pull", "Failed" if failed else "Success",
                    entity, message=summary)
        return summary
    finally:
        frappe.cache().delete_value("sap_van_pull_running")


@frappe.whitelist()
def pull_now():
    """On-demand pull, for the Sales Pro app to call before it shows van
    stock (POST /api/method/....stock_pull.pull_now).

    The scheduler already runs every minute; this removes even that wait
    for the one moment freshness actually matters — the rep opening their
    van. Any signed-in user may call it: it takes no input, the
    single-flight lock stops repeated taps stampeding SAP, and a caller
    who is already up to date simply gets "nothing new".
    """
    if frappe.session.user in ("Guest", None):
        raise frappe.PermissionError
    if not integration_enabled("pull_van_transfers"):
        return {"ok": False, "message": _("Van stock pull is switched off.")}
    return {"ok": True, "message": pull_van_transfers(triggered_by="on-demand")}


@frappe.whitelist()
def preview_transfers(limit=10):
    """Show SAP's most recent stock transfers and whether each would be
    pulled — without changing anything.

    "checked 0 SAP transfers" only says nothing was newer than the
    high-water mark; it cannot say what SAP holds or why a given transfer
    was not matched. This answers both, and gives the DocEntry to feed to
    'Pull From DocEntry'.
    """
    frappe.only_for(("System Manager", "Administrator"))
    settings = get_settings()
    client = SAPClient(settings)
    entity = _transfer_entity(client)
    if not entity:
        return _("No stock-transfer entity found on this SAP install.")

    mappings = settings.warehouse_mappings or []
    van_codes = {m.sap_warehouse_code for m in mappings if cint(m.is_van_warehouse)}
    mark = cint(settings.last_transfer_docentry)

    rows = client.get_all(entity, params={
        "$orderby": "DocEntry desc",
        "$top": cint(limit) or 10,
    })

    reference_udf = (settings.get("integration_number_udf") or "").strip()

    lines = [_("High-water mark is DocEntry {0} — only transfers above it are pulled.").format(mark), ""]
    for row in rows:
        entry = cint(row.get("DocEntry"))
        targets = sorted({
            (l.get("WarehouseCode") or "?")
            for l in (row.get("StockTransferLines") or [])
        })
        into_van = any(t in van_codes for t in targets)
        already = frappe.db.exists("Stock Entry", {"custom_sap_docentry": str(entry)})

        if already:
            verdict = _("already pulled as {0}").format(already)
        elif not into_van:
            verdict = _("target not a van warehouse")
        elif entry <= mark:
            verdict = _("BELOW the mark — use 'Pull From DocEntry' with {0}").format(entry)
        else:
            verdict = _("would be pulled")
        # Show whether SAP really carried the integration number across —
        # the one fact that says if the correlation is working, and the
        # thing you otherwise have to open SAP to see.
        origin = (row.get(reference_udf) or "") if reference_udf else ""
        origin_note = f" [{reference_udf}={origin or '(empty)'}]" if reference_udf else ""

        lines.append(
            f"DocEntry {entry} (DocNum {row.get('DocNum')}) {str(row.get('DocDate'))[:10]} "
            f"-> {', '.join(targets) or 'no lines'}{origin_note} : {verdict}"
        )

    summary = "\n".join(lines)
    log_sap("Pull", "Success", entity, message=summary[:9000])
    return summary


@frappe.whitelist()
def check_integration_fields():
    """Ask SAP whether the configured user fields really exist — on the
    transfer request we push to AND on the stock transfer we pull from.

    The integration number only works if SAP carries it across that
    conversion, and nothing on the ERPNext side can see whether it does.
    A field missing on either half fails silently: the number stays blank
    with nothing on the document to say why. This answers it outright,
    and lists the user fields SAP does have, so a near-miss in the name
    is visible rather than guessed at.
    """
    frappe.only_for(("System Manager", "Administrator"))
    settings = get_settings()
    client = SAPClient(settings)
    request_entity = "InventoryTransferRequests"
    transfer_entity = _transfer_entity(client)

    lines = [
        _("Request (pushed from here): {0}").format(request_entity),
        _("Transfer (pulled from SAP): {0}").format(transfer_entity or _("NOT FOUND on this install")),
        "",
    ]

    for label, field, needs_both in (
        (_("Integration Number"), (settings.get("integration_number_udf") or "").strip(), True),
        (_("Van Flag"), (settings.get("van_request_udf") or "").strip(), False),
    ):
        if not field:
            lines.append(_("{0}: not set in SAP Integration Settings — nothing is stamped or matched.").format(label))
            continue

        # Bypass the cache: this is run precisely after adding the field
        # in SAP, and a ten-minute-old "missing" would be read as failure.
        on_request = client.has_property(request_entity, field, use_cache=False)
        on_transfer = bool(transfer_entity) and client.has_property(transfer_entity, field, use_cache=False)

        lines.append("{0} ('{1}'):".format(label, field))
        lines.append(_("  on {0}: {1}").format(request_entity, _("YES") if on_request else _("NO")))
        lines.append(_("  on {0}: {1}").format(transfer_entity or "?", _("YES") if on_transfer else _("NO")))

        if on_request and on_transfer:
            lines.append(_("  -> Working. Requests carry it and the pull can match on it."))
        elif on_request and needs_both:
            lines.append(_("  -> Half done. SAP accepts it on the request, but it is not on the "
                           "stock transfer, so the pull cannot match it back. Add the same user "
                           "field to the stock transfer in SAP and set it to copy over."))
        elif on_request:
            lines.append(_("  -> Stamped on requests. Not on the transfer, so it cannot be pulled by."))
        elif on_transfer:
            lines.append(_("  -> On the transfer but not the request, so nothing ever writes a value into it."))
        else:
            lines.append(_("  -> Not in SAP under this name. Create it, or correct the name below."))
        lines.append("")

    for entity in [e for e in (request_entity, transfer_entity) if e]:
        found = client.user_fields(entity)
        lines.append(_("User fields seen on {0}: {1}").format(
            entity, ", ".join(found) if found else _("none visible in a sample row")))
    lines.append("")
    lines.append(_("Note: SAP omits empty user fields from a sample row, so that last list "
                   "under-reports. The YES/NO answers above are the reliable ones."))

    summary = "\n".join(lines)
    log_sap("Pull", "Success", transfer_entity or request_entity, message=summary[:9000])
    return summary


@frappe.whitelist()
def pull_from_docentry(docentry):
    """Re-pull starting at a specific SAP transfer.

    The first pull baselines to SAP's newest transfer so history is not
    replayed — which also means a transfer posted BEFORE that moment is
    never seen. This rewinds the mark to just below the given DocEntry
    and pulls, so a specific transfer (or a short run of them) can be
    brought in deliberately.

    Transfers already mirrored are skipped on their unique SAP DocEntry
    stamp, so rewinding cannot double-create stock.
    """
    frappe.only_for(("System Manager", "Administrator"))
    docentry = cint(docentry)
    if docentry < 1:
        frappe.throw(_("Enter the SAP DocEntry of the transfer to pull from."))
    frappe.db.set_single_value("SAP Integration Settings", "last_transfer_docentry", docentry - 1)
    frappe.db.commit()
    frappe.clear_cache(doctype="SAP Integration Settings")
    return pull_van_transfers()


def scheduled_pull():
    """Kept as an entry point; the sub-minute loop now lives in
    realtime_sync.tick, which drives transfers, invoices and payments
    from one job so they share a worker instead of competing for one.

    Migrate clears the old scheduler entry, but a stale one — or a hand
    call — must not start a second polling loop beside the real tick.
    Delegating means either route ends up in the same lock.
    """
    from stock_addon.stock_addon.sap_integration.realtime_sync import tick
    tick()
