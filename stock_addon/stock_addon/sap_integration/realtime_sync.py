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
    log_sap,
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


# Endpoint names for the log, so a failure here reads the same as one
# raised deeper in the pull it was calling.
STREAM_ENDPOINT = {"transfers": "InventoryTransfers", "documents": "Documents"}


def _run(label, poller, stopped):
    """Run one stream.

    A stream that throws must not take the tick — and so the other
    streams — down with it. It is also dropped for the rest of this tick:
    an unreachable SAP, or a mapping that is wrong, fails identically on
    every pass, and retrying it eleven more times inside one minute is
    just load. The next minute starts clean.

    The failure goes to the SAP Integration Log as well as the Error Log.
    A pull that gets far enough logs its own failures there; one that
    cannot even reach SAP throws before it can, and that is exactly the
    failure someone is looking for when they open that log.
    """
    try:
        poller()
    except Exception:
        stopped.add(label)
        trace = frappe.get_traceback()
        frappe.log_error(trace, f"SAP realtime sync: {label}")
        log_sap("Pull", "Failed", STREAM_ENDPOINT.get(label, label),
                message=trace[-2000:])


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


# ----------------------------------------------------------- diagnosis
#
# "Nothing is syncing" has four possible causes and only one of them is
# this app: Frappe's scheduler can be off site-wide, the job can be
# missing or paused, the switches can be off, or SAP can be refusing.
# Chasing that from a console is slow and needs a shell, so it is
# answered here — in order, stopping at the first thing that is actually
# blocking, because everything after it is moot.

TICK_METHOD = "stock_addon.stock_addon.sap_integration.realtime_sync.tick"


@frappe.whitelist()
def sync_health():
    """Why the live sync is or is not running. Read top to bottom."""
    frappe.only_for(("System Manager", "Administrator"))
    from frappe.utils import now_datetime, time_diff_in_seconds

    lines = []
    blockers = []

    # 1. the scheduler itself
    lines.append(_("SCHEDULER"))
    if frappe.conf.get("maintenance_mode"):
        blockers.append(_("The site is in maintenance mode — no scheduled job runs."))
        lines.append(_("  Maintenance mode: ON — nothing scheduled runs"))
    if frappe.conf.get("pause_scheduler"):
        blockers.append(_("frappe.conf.pause_scheduler is set — no scheduled job runs."))
        lines.append(_("  pause_scheduler: SET in site config"))
    if frappe.conf.get("disable_scheduler"):
        blockers.append(_("frappe.conf.disable_scheduler is set — no scheduled job runs."))
        lines.append(_("  disable_scheduler: SET in site config"))
    if not cint(frappe.get_system_settings("enable_scheduler")):
        blockers.append(_("The scheduler is switched off in System Settings. Tick "
                          "'Enable Scheduler' there, or run: bench --site {0} enable-scheduler"
                          ).format(frappe.local.site))
        lines.append(_("  System Settings > Enable Scheduler: OFF"))
    if not blockers:
        lines.append(_("  Running."))

    # 2. this app's jobs
    lines.append("")
    lines.append(_("JOBS"))
    jobs = frappe.get_all(
        "Scheduled Job Type",
        filters={"method": ("like", "%stock_addon%")},
        fields=["name", "method", "stopped", "last_execution", "cron_format"],
        order_by="method",
    )
    if not jobs:
        blockers.append(_("No scheduled jobs are registered for this app — run bench migrate."))
        lines.append(_("  None registered. Run 'bench migrate'."))
    for job in jobs:
        short = job.method.rsplit(".", 2)[-2] + "." + job.method.rsplit(".", 1)[-1]
        age = ""
        if job.last_execution:
            seconds = time_diff_in_seconds(now_datetime(), job.last_execution)
            age = _(" ({0} ago)").format(_humanise(seconds))
        lines.append("  {0}  [{1}]  {2}{3}{4}".format(
            short,
            job.cron_format or _("event"),
            _("STOPPED — ") if job.stopped else "",
            job.last_execution or _("never run"),
            age,
        ))
        if job.method == TICK_METHOD:
            if job.stopped:
                blockers.append(_("The live sync job is stopped. Open Scheduled Job Type "
                                  "'{0}' and untick Stopped.").format(job.name))
            elif not job.last_execution:
                blockers.append(_("The live sync job has never run — the scheduler is not "
                                  "reaching it. Check that the workers are up: bench doctor"))
            elif time_diff_in_seconds(now_datetime(), job.last_execution) > 300:
                blockers.append(_("The live sync job last ran {0} ago; it should run every "
                                  "minute. The scheduler or its workers have stopped — "
                                  "bench restart, then bench doctor.").format(
                                      _humanise(time_diff_in_seconds(now_datetime(), job.last_execution))))
    if not any(j.method == TICK_METHOD for j in jobs):
        blockers.append(_("The live sync job is not registered. It is created from hooks.py "
                          "by bench migrate — pull the latest code and migrate."))

    # 3. the switches
    settings = get_settings()
    lines.append("")
    lines.append(_("SWITCHES"))
    for label, field in (
        (_("SAP integration"), "enabled"),
        (_("Pull van transfers"), "pull_van_transfers"),
        (_("Pull invoices raised in SAP"), "pull_sap_invoices"),
        (_("Pull payments taken in SAP"), "pull_sap_payments"),
    ):
        lines.append("  {0}: {1}".format(label, _("ON") if cint(settings.get(field)) else _("off")))
    if not cint(settings.get("enabled")):
        blockers.append(_("'Enable SAP Integration' is off — nothing is pushed or pulled."))
    elif not any(cint(settings.get(f)) for f in
                 ("pull_van_transfers", "pull_sap_invoices", "pull_sap_payments")):
        blockers.append(_("Every pull switch is off, so the job runs and finds nothing to do."))

    lines.append("")
    lines.append(_("POSITION IN SAP"))
    lines.append(_("  Transfers from DocEntry: {0}").format(cint(settings.get("last_transfer_docentry"))))
    lines.append(_("  Invoices live from DocEntry: {0}").format(cint(settings.get("last_invoice_docentry")) or _("not started")))
    lines.append(_("  Payments live from DocEntry: {0}").format(cint(settings.get("last_payment_docentry")) or _("not started")))

    # 4. what SAP has actually been saying
    lines.append("")
    lines.append(_("LAST 5 PULL LOG ENTRIES"))
    logs = frappe.get_all(
        "SAP Integration Log",
        filters={"direction": "Pull"},
        fields=["creation", "status", "endpoint", "message"],
        order_by="creation desc",
        limit=5,
    )
    if not logs:
        lines.append(_("  Nothing logged yet."))
    for entry in logs:
        lines.append("  {0}  {1:<7} {2}: {3}".format(
            entry.creation, entry.status, entry.endpoint,
            (entry.message or "").splitlines()[0][:150]))

    verdict = ([_("BLOCKED:")] + [f"  - {b}" for b in blockers]) if blockers else \
        [_("Nothing is blocking the live sync. If documents are still not arriving, the "
           "last log entries above say what SAP answered.")]
    return "\n".join(verdict + [""] + lines)


def _humanise(seconds):
    seconds = int(seconds or 0)
    if seconds < 120:
        return _("{0}s").format(seconds)
    if seconds < 7200:
        return _("{0} min").format(seconds // 60)
    return _("{0} h").format(seconds // 3600)
