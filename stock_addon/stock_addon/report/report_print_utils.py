# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Shared 'Print PDF' HTML renderer for Stock Addon script reports.

Produces the same styled, print-ready HTML used by the van-sales reports so the
Print button looks uniform across all of them. A report's whitelisted
get_pdf_html() just forwards its title, filters, columns and data here.
"""

import json

import frappe
from frappe.utils import cint, flt, fmt_money, format_datetime, formatdate, get_datetime

_RIGHT_TYPES = {"Currency", "Float", "Int", "Percent"}


def _load(value):
	if isinstance(value, str):
		return json.loads(value or "[]")
	return value or []


def _col_field(col):
	if isinstance(col, dict):
		return col.get("fieldname")
	if isinstance(col, str) and ":" in col:
		return frappe.scrub(col.split(":", 1)[0])
	return col


def _col_label(col):
	if isinstance(col, dict):
		return col.get("label") or col.get("fieldname") or ""
	if isinstance(col, str):
		return col.split(":", 1)[0]
	return str(col)


def _col_type(col):
	if isinstance(col, dict):
		return col.get("fieldtype") or "Data"
	if isinstance(col, str) and ":" in col:
		parts = col.split(":")
		return parts[1] if len(parts) > 1 else "Data"
	return "Data"


def _fmt(value, fieldtype, currency):
	if value is None or value == "":
		return ""
	if fieldtype == "Currency":
		return fmt_money(flt(value), currency=currency)
	if fieldtype in ("Float", "Percent"):
		return f"{flt(value):,.2f}"
	if fieldtype == "Int":
		return f"{cint(value):,}"
	if fieldtype == "Date":
		return formatdate(value)
	return str(value)


def render_report_pdf(title, filters, columns, data):
	"""Return a full HTML document for printing a generic script report."""
	filters = frappe._dict(_load(filters) if isinstance(filters, str) else (filters or {}))
	columns = _load(columns) if isinstance(columns, str) else (columns or [])
	data = _load(data) if isinstance(data, str) else (data or [])

	company = filters.get("company")
	company_doc = frappe.get_doc("Company", company) if company else None
	currency = company_doc.default_currency if company_doc else "USD"

	letter_head = ""
	if company_doc and company_doc.default_letter_head:
		try:
			lh = frappe.get_doc("Letter Head", company_doc.default_letter_head)
			letter_head = lh.content or ""
		except Exception:
			letter_head = ""

	# only show columns that are not hidden / not the helper currency column
	view_cols = [
		c
		for c in columns
		if not (isinstance(c, dict) and (c.get("hidden") or _col_field(c) == "currency"))
	]

	# header row
	head_html = ""
	for c in view_cols:
		cls = ' class="text-right"' if _col_type(c) in _RIGHT_TYPES else ""
		head_html += f"<th{cls}>{frappe.utils.escape_html(_col_label(c))}</th>"

	# body rows
	rows_html = ""
	for idx, row in enumerate(data, 1):
		if not isinstance(row, dict):
			continue
		is_total = row.get("is_total") or row.get("entry_type") in ("SUBTOTAL", "TOTAL")
		row_cls = "totals-row" if is_total else ("even" if idx % 2 == 0 else "odd")
		row_cur = row.get("currency") or currency
		tds = ""
		for c in view_cols:
			ftype = _col_type(c)
			val = _fmt(row.get(_col_field(c)), ftype, row_cur)
			align = " text-right" if ftype in _RIGHT_TYPES else ""
			tds += f'<td class="{align.strip()}">{val}</td>'
		rows_html += f'<tr class="{row_cls}">{tds}</tr>'

	# applied filters
	filter_html = ""
	for key, value in filters.items():
		if value in (None, "", []):
			continue
		label = frappe.unscrub(key)
		shown = ", ".join(value) if isinstance(value, list) else str(value)
		filter_html += (
			'<div class="filter-item">'
			f'<span class="filter-label">{frappe.utils.escape_html(label)}:</span> '
			f'<span class="filter-value">{frappe.utils.escape_html(shown)}</span>'
			"</div>"
		)

	now = format_datetime(get_datetime(), "dd MMM yyyy HH:mm")
	period = ""
	if filters.get("from_date") or filters.get("to_date"):
		period = (
			f"Period: {formatdate(filters.get('from_date'))} to {formatdate(filters.get('to_date'))}"
		)

	letter_head_html = (
		f'<div class="letter-head">{letter_head}</div>' if letter_head else ""
	)

	html = (
		'<!DOCTYPE html><html><head><meta charset="UTF-8">'
		f"<title>{frappe.utils.escape_html(title)}</title>"
		"<style>" + _CSS + "</style></head><body>"
		+ letter_head_html
		+ '<div class="report-header">'
		'<div class="report-title">'
		f"<h1>{frappe.utils.escape_html(title)}</h1>"
		f'<div class="subtitle">{period}</div>'
		"</div>"
		'<div class="report-meta">'
		f'<div><span class="label">Company:</span> <strong>{str(company or "")}</strong></div>'
		f'<div><span class="label">Generated:</span> {now}</div>'
		f'<div><span class="label">By:</span> {frappe.session.user}</div>'
		"</div></div>"
		+ (
			'<div class="filters-section"><div class="filters-title">Applied Filters</div>'
			f'<div class="filters-grid">{filter_html}</div></div>'
			if filter_html
			else ""
		)
		+ '<table class="report-table"><thead><tr>'
		+ head_html
		+ "</tr></thead><tbody>"
		+ rows_html
		+ "</tbody></table>"
		+ '<div class="signature-section">'
		'<div class="signature-box"><div class="signature-line"></div><div><strong>Prepared By</strong></div></div>'
		'<div class="signature-box"><div class="signature-line"></div><div><strong>Verified By</strong></div></div>'
		'<div class="signature-box"><div class="signature-line"></div><div><strong>Approved By</strong></div></div>'
		"</div>"
		'<div class="footer">'
		f'<div>{frappe.utils.escape_html(title)} - {str(company or "")}</div>'
		f"<div>Generated by {frappe.session.user} on {now}</div>"
		"</div></body></html>"
	)
	return html


_CSS = """
	@page { size: A4 landscape; margin: 8mm; }
	* { box-sizing: border-box; margin: 0; padding: 0; }
	body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 8pt;
		   color: #2d3748; background: #fff; line-height: 1.3; }
	.letter-head { margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #5e72e4; }
	.report-header { display: flex; justify-content: space-between; align-items: flex-start;
					 margin-bottom: 10px; padding: 12px 16px;
					 background: linear-gradient(135deg, #4299e1 0%, #667eea 50%, #764ba2 100%);
					 color: white; border-radius: 8px; }
	.report-title h1 { font-size: 16pt; font-weight: 700; margin-bottom: 3px; letter-spacing: 0.3px; }
	.report-title .subtitle { font-size: 8.5pt; opacity: 0.92; font-weight: 400; }
	.report-meta { text-align: right; font-size: 7.5pt; }
	.report-meta .label { opacity: 0.85; }
	.filters-section { background: #f0f4ff; border-left: 4px solid #667eea;
					  padding: 8px 12px; margin-bottom: 10px; border-radius: 4px; }
	.filters-title { font-size: 8pt; font-weight: 700; color: #5e72e4;
					margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
	.filters-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px 12px; }
	.filter-item { font-size: 7.5pt; }
	.filter-label { font-weight: 600; color: #4a5568; }
	.filter-value { color: #2d3748; }
	table.report-table { width: 100%; border-collapse: collapse; font-size: 7.5pt; margin-bottom: 10px; }
	table.report-table thead { background: linear-gradient(135deg, #4299e1 0%, #667eea 100%); color: white; }
	table.report-table th { padding: 6px 4px; text-align: left; font-weight: 600;
							font-size: 7.5pt; text-transform: uppercase; letter-spacing: 0.2px;
							border: 1px solid #5a92d4; }
	table.report-table th.text-right { text-align: right; }
	table.report-table td { padding: 4px; border: 1px solid #e2e8f0; }
	table.report-table tr.odd { background: #ffffff; }
	table.report-table tr.even { background: #f7fafc; }
	table.report-table .text-right { text-align: right; }
	tr.totals-row { background: linear-gradient(135deg, #4299e1, #667eea) !important; color: white; font-weight: 700; }
	tr.totals-row td { padding: 8px 4px; border-color: #5a92d4; font-size: 8pt; }
	.footer { margin-top: 14px; padding-top: 6px; border-top: 1px solid #e2e8f0;
			 display: flex; justify-content: space-between; font-size: 6.5pt; color: #718096; }
	.signature-section { display: grid; grid-template-columns: repeat(3, 1fr);
						gap: 25px; margin-top: 30px; padding-top: 6px; }
	.signature-box { text-align: center; font-size: 7.5pt; color: #4a5568; }
	.signature-line { border-top: 1px solid #2d3748; margin: 22px 15px 4px 15px; }
	@media print {
		body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
		tr { page-break-inside: avoid; }
		thead { display: table-header-group; }
	}
"""
