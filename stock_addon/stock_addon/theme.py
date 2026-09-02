# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Desk theme palette.

The SAP B1 navy theme in public/css/stock_addon.bundle.css drives every
colour through CSS custom properties. This module lets those properties
be overridden at runtime from the "Stock Addon Theme Settings" screen —
no CSS edit, no bench build.

Flow:
  settings doc -> get_palette() -> extend_bootinfo -> frappe.boot
  -> public/js/stock_addon_theme.js sets the properties on :root

FIELD_TO_VAR is the whole contract: add a Color field to the doctype,
add its CSS variable here, and it becomes adjustable. DEFAULTS must
mirror the :root block in the stylesheet — they are what "Reset to
Defaults" restores and what the form shows as placeholders.
"""

import frappe

# doctype fieldname -> CSS custom property
FIELD_TO_VAR = {
    "primary_navy":          "--agri-primary",
    "section_header_colour": "--agri-primary-mid",
    "sidebar_background":    "--agri-shell",
    "navbar_background":     "--agri-shell-dark",
    "canvas_top":            "--agri-canvas-top",
    "canvas_bottom":         "--agri-canvas-bottom",
    "accent_colour":         "--agri-accent",
    "selected_highlight":    "--agri-shell-marker",
    "zebra_tint":            "--agri-pale",
    "border_colour":         "--agri-border",
}

# must match the :root block in public/css/stock_addon.bundle.css
DEFAULTS = {
    "primary_navy":          "#14395E",
    "section_header_colour": "#2A5A8C",
    "sidebar_background":    "#3A5F86",
    "navbar_background":     "#33547A",
    "canvas_top":            "#2C5480",
    "canvas_bottom":         "#42729B",
    "accent_colour":         "#0A6ED1",
    "selected_highlight":    "#F0AB00",
    "zebra_tint":            "#EEF3F9",
    "border_colour":         "#C3D0E0",
}


def get_palette():
    """{css_variable: colour} for the desk to apply. Empty when the
    override is off.

    Deliberately swallows everything: this runs on every session boot,
    and a half-migrated site or a malformed colour must never be able to
    stop people logging in.
    """
    try:
        if not frappe.db.exists("DocType", "Stock Addon Theme Settings"):
            return {}
        settings = frappe.get_cached_doc("Stock Addon Theme Settings")
        if not settings.get("enabled"):
            return {}
        palette = {}
        for fieldname, css_var in FIELD_TO_VAR.items():
            value = (settings.get(fieldname) or "").strip()
            if value:
                palette[css_var] = value
        return palette
    except Exception:
        return {}


def boot_session(bootinfo):
    """extend_bootinfo hook — ship the palette with the desk boot so the
    colours are applied before first paint (no extra round trip)."""
    bootinfo.stock_addon_theme = get_palette()
