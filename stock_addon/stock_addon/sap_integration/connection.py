# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP Business One Service Layer client.

Session handling: Login once, cache the B1SESSION cookie in Redis for
20 minutes, transparently re-login on 401. All requests use
``verify=False`` because B1 Service Layer installs almost always run on
self-signed certificates.

Every module in this package goes through :class:`SAPClient`; every
success/failure is recorded in the "SAP Integration Log" doctype via
:func:`log_sap`.
"""

import json

import requests
import urllib3

import frappe
from frappe import _
from frappe.utils import cint

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SESSION_CACHE_KEY = "sap_b1_session_cookies"


class SAPError(frappe.ValidationError):
    """Subclassing ValidationError means whitelisted buttons surface the
    actual message as a clean red dialog instead of 'Server Error'."""
    pass


def get_settings():
    return frappe.get_cached_doc("SAP Integration Settings")


def integration_enabled(flag=None):
    """True when the integration master switch (and the given feature
    toggle, if any) is on. Never throws — a broken settings doc simply
    means 'disabled'."""
    try:
        settings = get_settings()
    except Exception:
        return False
    if not cint(settings.get("enabled")):
        return False
    if flag and not cint(settings.get(flag)):
        return False
    return True


class single_flight:
    """Stop two runs of the same sync overlapping.

    The hourly scheduler and the manual button live in different worker
    processes: without this they both pass the "does it exist?" check and
    both try to create the same master. Used as a context manager —
    ``acquired`` is False when another run already holds the lock.
    """

    def __init__(self, key, seconds=600):
        self.key = f"sap_sync_lock:{key}"
        self.seconds = seconds
        self.acquired = False

    def __enter__(self):
        if frappe.cache().get_value(self.key):
            return self
        frappe.cache().set_value(self.key, 1, expires_in_sec=self.seconds)
        self.acquired = True
        return self

    def __exit__(self, *exc):
        if self.acquired:
            frappe.cache().delete_value(self.key)
        return False


def log_sap(direction, status, endpoint, reference_doctype=None,
            reference_name=None, sap_docentry=None, message=""):
    """Write one SAP Integration Log row. Never raises."""
    try:
        frappe.get_doc({
            "doctype": "SAP Integration Log",
            "direction": direction,
            "status": status,
            "endpoint": endpoint,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "sap_docentry": str(sap_docentry) if sap_docentry is not None else None,
            "message": (message or "")[:10000],
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP Integration Log write failed")


class SAPClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        if not self.settings.service_layer_url:
            frappe.throw(_("SAP Integration Settings: Service Layer URL is not set."))
        self.base_url = (self.settings.service_layer_url or "").rstrip("/")

    # ------------------------------------------------------------ session
    def login(self):
        password = self.settings.get_password("password", raise_exception=False) or ""
        resp = requests.post(
            f"{self.base_url}/Login",
            json={
                "CompanyDB": self.settings.company_db,
                "UserName": self.settings.username,
                "Password": password,
            },
            verify=False,
            timeout=30,
        )
        if resp.status_code != 200:
            raise SAPError(f"SAP Login failed ({resp.status_code}): {resp.text[:500]}")
        cookies = resp.cookies.get_dict()
        frappe.cache().set_value(SESSION_CACHE_KEY, json.dumps(cookies), expires_in_sec=20 * 60)
        return cookies

    def _cookies(self):
        raw = frappe.cache().get_value(SESSION_CACHE_KEY)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        return self.login()

    # ------------------------------------------------------------ requests
    def request(self, method, endpoint, payload=None, params=None, _retry=True):
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}/{endpoint.lstrip('/')}"
        resp = requests.request(
            method, url,
            json=payload,
            params=params,
            cookies=self._cookies(),
            headers={"Prefer": "odata.maxpagesize=500"},
            verify=False,
            # pushes run inside the user's submit — fail fast rather than
            # hold the form open on an unreachable SAP. Failures are
            # retryable, so a short timeout costs nothing.
            timeout=25,
        )
        if resp.status_code == 401 and _retry:
            self.login()
            return self.request(method, endpoint, payload=payload, params=params, _retry=False)
        if resp.status_code >= 400:
            raise SAPError(f"{method} {endpoint} failed ({resp.status_code}): {resp.text[:1000]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get(self, endpoint, params=None):
        return self.request("GET", endpoint, params=params)

    def post(self, endpoint, payload):
        return self.request("POST", endpoint, payload=payload)

    def get_all(self, endpoint, params=None, max_pages=100):
        """GET with odata paging — follows odata.nextLink and returns the
        combined ``value`` list."""
        result = self.get(endpoint, params=params)
        rows = list(result.get("value", []))
        pages = 0

        def _next(res):
            # b1s/v1 (OData v3) uses "odata.nextLink"; b1s/v2 (OData v4)
            # uses "@odata.nextLink"
            return res.get("odata.nextLink") or res.get("@odata.nextLink")

        while _next(result) and pages < max_pages:
            result = self.get(_next(result))
            rows.extend(result.get("value", []))
            pages += 1
        return rows

    def entity_sets(self):
        """EntitySet names this Service Layer install actually exposes.

        Entity naming varies between B1 versions, so rather than guessing
        (and getting a 400 "invalid resource"), read $metadata and match
        against what is really there. Cached for the session — the
        document is large and never changes at runtime.
        """
        cached = frappe.cache().get_value("sap_b1_entity_sets")
        if cached:
            return json.loads(cached)
        resp = requests.get(
            f"{self.base_url}/$metadata",
            cookies=self._cookies(), verify=False, timeout=60,
        )
        if resp.status_code != 200:
            raise SAPError(f"Could not read SAP $metadata ({resp.status_code})")
        import re as _re
        names = sorted(set(_re.findall(r'EntitySet\s+Name="([^"]+)"', resp.text)))
        # never cache an empty result — a parse that matched nothing would
        # otherwise look like "this install exposes no entities" for an hour
        if names:
            frappe.cache().set_value("sap_b1_entity_sets", json.dumps(names), expires_in_sec=3600)
        return names

    def probe_entity(self, candidates):
        """First candidate whose resource path actually responds.

        More reliable than reading $metadata: it tests the exact thing
        that matters — whether SAP serves that path — and does not depend
        on the metadata document being parseable. The winner is cached so
        this costs one request per sync, not per call.
        """
        cache_key = "sap_b1_entity:" + "|".join(candidates)
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached
        for name in candidates:
            try:
                self.get(name, params={"$top": 1})
            except Exception:
                continue
            frappe.cache().set_value(cache_key, name, expires_in_sec=3600)
            return name
        return None

    def has_property(self, entity, prop, use_cache=True):
        """Whether ``entity`` really exposes ``prop`` — typically a U_ user
        field.

        Asks SAP to ``$select`` it: 200 means the property is there, 400
        means it is not. That is decisive in a way reading a sample row is
        not, because Service Layer omits null properties from its JSON —
        a user field that exists but has never been filled is invisible in
        a row, and we would wrongly conclude it is missing.

        Both answers are cached for ten minutes, so a fifteen-second pull
        does not re-ask a settled question, and a field added in SAP is
        picked up without a restart.
        """
        if not entity or not prop:
            return False
        cache_key = f"sap_b1_prop:{entity}:{prop}"
        if use_cache:
            cached = frappe.cache().get_value(cache_key)
            if cached is not None:
                return cached == "1"
        try:
            self.get(entity, params={"$select": prop, "$top": 1})
            answer = True
        except Exception:
            answer = False
        frappe.cache().set_value(cache_key, "1" if answer else "0", expires_in_sec=600)
        return answer

    def user_fields(self, entity):
        """The U_ user fields visible on a sample row of ``entity``.

        A hint for diagnostics, not proof: null user fields are omitted
        from the JSON, so this under-reports. Use has_property() to settle
        whether a specific field exists.
        """
        try:
            rows = self.get(entity, params={"$top": 1}).get("value") or []
        except Exception:
            return []
        if not rows:
            return []
        return sorted(k for k in rows[0] if k.startswith("U_"))

    def find_entity(self, *keywords):
        """First EntitySet whose name contains every keyword (case
        insensitive), or None. Lets a tier bind to whatever this install
        calls the thing instead of a hard-coded guess."""
        wanted = [k.lower() for k in keywords]
        for name in self.entity_sets():
            lowered = name.lower()
            if all(k in lowered for k in wanted):
                return name
        return None

    def test(self):
        """Login and return a human-readable success line."""
        self.login()
        return _("Connected to SAP B1 company {0} at {1}").format(
            self.settings.company_db, self.base_url
        )
