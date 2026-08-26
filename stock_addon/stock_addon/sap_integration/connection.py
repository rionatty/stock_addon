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
            timeout=60,
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

    def test(self):
        """Login and return a human-readable success line."""
        self.login()
        return _("Connected to SAP B1 company {0} at {1}").format(
            self.settings.company_db, self.base_url
        )
