# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Submit Sales Orders and Material Requests as soon as they are created.

after_insert hook (wired in hooks.py). The Sales Pro app posts each of
these as a single draft — one request carrying the whole document — and
never comes back to submit it, so without this they queue up in the desk
waiting for someone to submit them by hand.

Driven by SAP Integration Settings -> Auto Submit, off by default and set
per doctype. Submitting is not reversible: a submitted document can only
be cancelled and amended, never edited.

It sits on that screen because submitting is what sends these documents
to SAP, so it belongs next to "Send Sales Orders" where anyone
configuring the flow will look for it. It is NOT gated on the SAP master
switch, though: turning a draft into a submitted document is worth doing
whether or not SAP is connected.

Two things make this less simple than it looks.

  * Frappe is still inside insert() when after_insert runs. __islocal is
    set and only cleared at the very end, and Document._save() returns
    straight into insert() while it is set — so doc.submit() here would
    re-insert a row that already exists and raise DuplicateEntryError.
    A freshly loaded copy is submitted instead.

  * on_submit for both doctypes pushes to SAP over HTTPS with a
    25-second timeout. Doing that inside the creating request puts a SAP
    round trip in front of every order a rep places on a phone. So the
    submit is enqueued to run once the request has committed, and only
    runs in-request when someone deliberately asks for it.

A failed submit never takes the document with it. The failure is rolled
back to a savepoint, the draft survives, and the reason is written onto
the document as a comment — where the person looking at the draft will
actually see it.
"""

import frappe
from frappe import _
from frappe.utils import cint, escape_html

# Fixed identifier: frappe interpolates savepoint names straight into SQL.
SAVEPOINT = "stock_addon_auto_submit"

# Settings field per doctype. A doctype absent from here is never touched.
MODE_FIELD = {
    "Sales Order": "auto_submit_sales_orders",
    "Material Request": "auto_submit_material_requests",
}

OFF = "Off"
APP_ONLY = "From the App Only"
APP_AND_DESK = "App and Desk"

# What the desk sends when someone saves a form.
DESK_SAVE_PREFIX = "frappe.desk."


def after_insert(doc, method=None):
    if not _wanted(doc):
        return

    if cint(_settings().auto_submit_immediately):
        _submit_in_request(doc)
        return

    frappe.enqueue(
        "stock_addon.stock_addon.doc_events.auto_submit.submit_document",
        queue="short",
        # Never chase a document the request went on to roll back.
        enqueue_after_commit=True,
        doctype=doc.doctype,
        docname=doc.name,
    )


def submit_document(doctype, docname):
    """Background half: submit a document in its own transaction, after the
    request that created it has committed."""
    doc = frappe.get_doc(doctype, docname)
    if cint(doc.docstatus) != 0:
        # A retried job, or a person got there first.
        return
    try:
        doc.submit()
    except Exception:
        frappe.db.rollback()
        _record_failure(doctype, docname)
        frappe.db.commit()


# --------------------------------------------------------------- decision
def _wanted(doc):
    """Every reason to leave a document alone, cheapest check first."""
    if doc.doctype not in MODE_FIELD:
        return False
    if cint(doc.docstatus) != 0:
        return False                        # the creator submitted it themselves
    if doc.flags.get("skip_auto_submit"):
        return False                        # our own code opting out
    if not doc.get("items"):
        return False                        # a header with no lines is not a document yet

    settings = _settings()
    if settings is None:
        return False

    mode = (settings.get(MODE_FIELD[doc.doctype]) or OFF).strip()
    if mode == OFF:
        return False

    origin = _origin()
    if origin == "server":
        # Scheduler, patches, our own SAP sync — that code submits what it
        # means to submit, and should not be second-guessed here.
        return False
    if origin == "desk" and mode != APP_AND_DESK:
        return False

    if (
        doc.doctype == "Material Request"
        and _is_van_return(doc)
        and not cint(settings.auto_submit_van_returns)
    ):
        # The app creates returns as drafts deliberately, and the
        # permanent-return flow expects a manager to look at them.
        return False

    if _has_active_workflow(doc.doctype):
        # A workflow owns the docstatus. Submitting behind its back is
        # either refused outright or leaves the workflow state describing
        # a document it no longer matches. The settings screen warns about
        # this on save, so the silence here is not the first anyone hears
        # of it.
        return False

    return True


def _settings():
    """Read the settings document directly, not through
    integration_enabled() — auto-submit must keep working when the SAP
    master switch is off."""
    try:
        return frappe.get_cached_doc("SAP Integration Settings")
    except Exception:
        return None


def _is_van_return(doc):
    return (doc.get("custom_van_return") or "").strip().lower() == "yes"


def _has_active_workflow(doctype):
    return bool(frappe.db.exists("Workflow", {"document_type": doctype, "is_active": 1}))


def _origin():
    """Where this document came from: "app", "desk" or "server".

    The desk saves through frappe.desk.form.save.savedocs, so its own
    drafts stay drafts unless the setting says otherwise. Only the v1 RPC
    route fills form_dict.cmd, hence the fallback to the request path.
    """
    request = getattr(frappe.local, "request", None)
    if request is None:
        return "server"             # background job, scheduler, patch, bench

    cmd = (frappe.local.form_dict or {}).get("cmd") or ""
    if not cmd:
        path = getattr(request, "path", "") or ""
        for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
            if path.startswith(prefix):
                cmd = path[len(prefix):].split("/")[0]
                break

    return "desk" if cmd.startswith(DESK_SAVE_PREFIX) else "app"


# ---------------------------------------------------------------- submit
def _submit_in_request(doc):
    """Submit before the response goes out.

    The savepoint is what keeps a failed submit from taking the new
    document with it: an exception escaping this hook would roll back the
    whole request, and the document the app just created would be gone.
    """
    frappe.db.savepoint(SAVEPOINT)
    try:
        # A fresh copy: the doc handed to after_insert is mid-insert, and
        # submitting it re-enters insert().
        frappe.get_doc(doc.doctype, doc.name).submit()
    except Exception:
        frappe.db.rollback(save_point=SAVEPOINT)
        # Rolling back undoes SQL, not the message queue — without this
        # the caller is shown the validation error and reads it as the
        # creation having failed, which it did not.
        frappe.clear_last_message()
        frappe.clear_document_cache(doc.doctype, doc.name)
        _record_failure(doc.doctype, doc.name)
    else:
        frappe.db.release_savepoint(SAVEPOINT)
        # The response is built from this object. Leaving it saying "draft"
        # with a pre-submit timestamp makes the desk show the wrong state
        # and fail its next save on a timestamp mismatch.
        doc.docstatus = 1
        doc.modified = frappe.db.get_value(doc.doctype, doc.name, "modified")


def _record_failure(doctype, docname):
    """Say why, on the document itself. An Error Log nobody opens does not
    tell the person looking at the stuck draft anything."""
    trace = frappe.get_traceback()
    frappe.log_error(trace, "Stock Addon auto-submit")
    try:
        frappe.get_doc(doctype, docname).add_comment(
            "Comment",
            _("Auto-submit failed, so this is still a draft:<br>{0}").format(
                escape_html(_last_line(trace))
            ),
        )
    except Exception:
        # Best effort. The Error Log already has the full traceback, and a
        # comment that will not write must not mask the original failure.
        pass


def _last_line(trace):
    lines = [line for line in (trace or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else _("no details recorded")
