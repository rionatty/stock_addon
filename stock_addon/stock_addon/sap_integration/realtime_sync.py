# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""One heartbeat for every SAP stream that has to be near-live.

Frappe's cron cannot fire more often than once a minute, so anything
closer to live has to poll inside its own minute. Doing that in two
separate scheduler jobs would mean two workers each sleeping through most
of every minute, competing for the same connection and the same locks —
so there is exactly ONE such job, and it drives every stream:

    van transfers   SAP StockTransfers    -> Stock Entry
    invoices        SAP Invoices          -> Sales Invoice
    payments        SAP IncomingPayments  -> Payment Entry

Three things keep this from becoming a load problem.

  * One lock. A tick that overruns its minute cannot be joined by the
    next one; the next simply does nothing and returns.

  * A cheap question first. Asking "is there anything new?" costs one
    row — $select=DocEntry, $top=1 — not a page of documents. In the
    steady state, where nothing has happened in SAP, a tick is three
    tiny reads. Only a DocEntry higher than the last one seen turns into
    a fetch, and that fetch asks only for the difference.

  * Its own pace per stream. Transfers are what a rep is standing in
    front of a customer waiting for, so they poll on the tight interval.
    Invoices and payments are accounting records; a minute is already
    far faster than anyone needs, and the interval is theirs to set.

Master data — items, customers, pricing — deliberately stays hourly. It
changes rarely and a full sweep is expensive; polling it on this loop
would be exactly the overload this design exists to avoid.
"""

import time

import frappe

from frappe.utils import cint

from stock_addon.stock_addon.sap_integration.connection import (
    get_settings,
    integration_enabled,
    single_flight,
)

# Leave headroom so a tick finishes before the next minute fires.
BUDGET_SECONDS = 55


def _interval(settings, fieldname, default, floor=5):
    value = cint(settings.get(fieldname)) or default
    return max(floor, min(60, value))


def tick():
    """The per-minute scheduler job. Polls each enabled stream at its own
    interval until the minute is nearly spent."""
    if not integration_enabled():
        return

    # 50s: shorter than the budget, so a tick that dies without releasing
    # cannot lock the next one out for long.
    with single_flight("sap_realtime", seconds=50) as lock:
        if not lock.acquired:
            return

        settings = get_settings()
        transfer_every = _interval(settings, "pull_interval_seconds", 60)
        document_every = _interval(settings, "document_pull_interval_seconds", 60)

        # The shortest interval sets the beat; each stream runs on a
        # multiple of it, so nothing is polled faster than it asked for.
        beat = min(transfer_every, document_every)
        elapsed = 0
        last_transfer = last_document = None
        stopped = set()

        while True:
            if "transfers" not in stopped and (
                    last_transfer is None or elapsed - last_transfer >= transfer_every):
                _run("transfers", _poll_transfers, stopped)
                last_transfer = elapsed
            if "documents" not in stopped and (
                    last_document is None or elapsed - last_document >= document_every):
                _run("documents", _poll_documents, stopped)
                last_document = elapsed

            if stopped >= {"transfers", "documents"}:
                return                      # nothing left worth waiting for
            if elapsed + beat > BUDGET_SECONDS:
                return
            time.sleep(beat)
            elapsed += beat


def _run(label, poller, stopped):
    """Run one stream.

    A stream that throws must not take the tick — and so the other
    streams — down with it. It is also dropped for the rest of this tick:
    an unreachable SAP, or a mapping that is wrong, fails identically on
    every pass, and retrying it eleven more times inside one minute is
    just load. The next minute starts clean.
    """
    try:
        poller()
    except Exception:
        stopped.add(label)
        frappe.log_error(frappe.get_traceback(), f"SAP realtime sync: {label}")


def _poll_transfers():
    if not integration_enabled("pull_van_transfers"):
        return
    from stock_addon.stock_addon.sap_integration.stock_pull import pull_van_transfers
    pull_van_transfers(triggered_by="scheduler")


def _poll_documents():
    from stock_addon.stock_addon.sap_integration import document_pull
    if integration_enabled("pull_sap_invoices"):
        document_pull.poll_invoices()
    if integration_enabled("pull_sap_payments"):
        document_pull.poll_payments()
