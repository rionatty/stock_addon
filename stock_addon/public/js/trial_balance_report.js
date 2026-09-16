// Stock Addon — Trial Balance printout: the account CODE and the LEDGER name
// in columns of their own.
//
// ERPNext's print template (trial_balance.html) prints each account in one
// column, "12030303 - Petty cash Christine". The report already returns the
// two parts separately — acc_number and acc_name — but marks them hidden, and
// that template prints only visible columns. Unhiding them would not work
// either: the template refuses to print more than 8 columns, and the Trial
// Balance already prints exactly 8.
//
// So this supplies the print template instead. Frappe reads it from the
// report settings' html_format (query_report.js, get_custom_format), and a
// property on the settings object stops ERPNext's own template, assigned when
// the report loads, from replacing it. The layout is ERPNext's: ledgers stay
// indented by depth and top-level groups stay bold. Currency is left off the
// printout — a single-currency ledger repeats it on every line — which keeps
// Code + Ledger within the width the page allows.
//
// Loaded desk-wide via app_include_js, like general_ledger_report.js.

(function () {
	if (typeof frappe === "undefined") return;

	const REPORT_NAME = "Trial Balance";

	// Notes on the template, kept out of it. Frappe's template compiler
	// (microtemplate.js) joins every {% %} block onto one line, so a // comment
	// inside one would silence all the code after it.
	//
	// split_account: newer ERPNext sends the two parts (acc_number, acc_name);
	// older builds send only the joined "code - name". That is split only when
	// the front part looks like a code, so a ledger whose NAME contains " - "
	// is never cut in two. The Total row arrives quoted ("'Total'"), hence clean.
	//
	// Columns: get_columns_for_print is what ERPNext's own template reads, and
	// already leaves out hidden columns; older Frappe lacks it, so fall back to
	// report.columns and drop the hidden ones here.
	const TEMPLATE = `
{%
	const clean = (v) => String(v == null ? "" : v).replace(/^['"]|['"]$/g, "");

	const split_account = (row) => {
		if (row.acc_number || row.acc_name) {
			return { code: clean(row.acc_number), ledger: clean(row.acc_name || row.account_name) };
		}
		const joined = clean(row.account_name || row.section);
		const m = joined.match(/^([^\\s]*\\d[^\\s]*) - (.+)$/);
		return m ? { code: m[1], ledger: m[2] } : { code: "", ledger: joined };
	};

	const print_columns = typeof report.get_columns_for_print === "function"
		? report.get_columns_for_print()
		: report.columns || [];
	const amount_columns = print_columns.filter(
		(col) => !col.hidden && !["account", "currency", "acc_name", "acc_number"].includes(col.fieldname)
	);
%}

<style>
	body, html { margin-top: 10px; padding: 0; width: 100%; height: auto;
		font-family: Inter, sans-serif; font-size: 13px; line-height: 20px; color: #171717; }
	.title-letter-spacing { font-size: 15px; font-weight: 600; color: #171717; }
	.financial-statements-important td { font-weight: bold; }
	.financial-statements-blank-row td { height: 20px; }
	.report-meta { margin: 10px 0 14px; padding: 8px 10px; display: flex;
		justify-content: space-between; font-size: 13px; }
	.report-meta .left, .report-meta .right { display: flex; flex-direction: column; }
	.report-meta strong { color: #7c7c7c; font-weight: 500; }
	.report-subtitle { margin: 10px 0 14px; }
	.text-center { text-align: center; }
	.text-right { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;
		overflow: hidden; text-overflow: ellipsis; }
	.text-left { text-align: left; }
	.report-table table { width: 100%; table-layout: fixed; border-collapse: collapse; }
	.report-table th, .report-table td { padding: 5px 6px; font-size: 13px;
		border-top: 1px solid #ededed; border-bottom: 1px solid #ededed; }
	.report-table thead th { background: #f8f8f8; font-weight: 500; color: #7c7c7c; }
	.report-table tbody td.text-left { vertical-align: top; word-wrap: break-word; }
	.report-table td.code { white-space: nowrap; font-variant-numeric: tabular-nums; }
	.report-table thead th:first-child { border-left: 1px solid #ededed; }
	.report-table thead th:last-child { border-right: 1px solid #ededed; }
	@media print {
		@page { size: A4; margin-top: 10mm; }
		thead { display: table-header-group; }
		tr { page-break-inside: avoid; }
	}
</style>

<div>
	<div class="text-center" style="margin-bottom: 12px;">
		<div class="title-letter-spacing">{%= __(report.report_name) %}</div>
	</div>

	{% if (subtitle && subtitle.trim()) { %}
		<div class="report-subtitle">{{ subtitle }}</div>
	{% } else { %}
		<div class="report-meta">
			<div class="left">
				<div><strong>{%= __("Company") %}:</strong> {%= filters.company %}</div>
				<div><strong>{%= __("Currency") %}:</strong>
					{%= filters.presentation_currency || erpnext.get_currency(filters.company) %}</div>
			</div>
			<div class="right text-right">
				<div><strong>{%= __("From Date") %}:</strong> {%= frappe.datetime.str_to_user(filters.from_date) %}</div>
				<div><strong>{%= __("To Date") %}:</strong> {%= frappe.datetime.str_to_user(filters.to_date) %}</div>
			</div>
		</div>
	{% } %}

	<div class="report-table">
		<table>
			<thead>
				<tr>
					<th class="text-left" style="width: 7em">{%= __("Code") %}</th>
					<th class="text-left">{%= __("Ledger") %}</th>
					{% for (let i = 0; i < amount_columns.length; i++) { %}
						<th class="text-right" style="width: 8.5em">{%= amount_columns[i].label %}</th>
					{% } %}
				</tr>
			</thead>
			<tbody>
				{% for (let j = 0; j < data.length; j++) { %}
					{%
						const row = data[j];
						const acc = split_account(row);
						let row_class = "";
						if (!(row.parent_account || row.parent_section)) row_class = "financial-statements-important";
						if (!(row.account_name || row.section)) row_class += " financial-statements-blank-row";
					%}
					<tr class="{%= row_class %}">
						<td class="text-left code">{%= acc.code %}</td>
						<td class="text-left">
							<span style="padding-left: {%= cint(row.indent) * 1.5 %}em">{%= acc.ledger %}</span>
						</td>
						{% for (let i = 0; i < amount_columns.length; i++) { %}
							{% const col = amount_columns[i]; const value = row[col.fieldname]; %}
							<td class="text-right">
								{% if (!is_null(value)) { %}{%= frappe.format(value, col, {}, row) %}{% } %}
							</td>
						{% } %}
					</tr>
				{% } %}
			</tbody>
		</table>
	</div>

	<p class="text-right text-muted">
		{%= __("Printed on {0}", [frappe.datetime.str_to_user(frappe.datetime.get_datetime_as_string())]) %}
	</p>
</div>
`;

	// Pin html_format to this template. ERPNext assigns its own when the report
	// loads (query_report.js), after the settings object is registered; the
	// setter swallows that so the separated layout is the one printed.
	function install(settings) {
		if (!settings || settings._stock_addon_tb_print) return settings;
		Object.defineProperty(settings, "html_format", {
			configurable: true,
			enumerable: true,
			get() {
				return TEMPLATE;
			},
			set() {},
		});
		settings._stock_addon_tb_print = true;
		return settings;
	}

	// The report's own .js loads lazily on first visit and registers its
	// settings then — install on whatever is there now, and on every later
	// registration.
	frappe.query_reports = frappe.query_reports || {};
	let stored = install(frappe.query_reports[REPORT_NAME]);
	Object.defineProperty(frappe.query_reports, REPORT_NAME, {
		configurable: true,
		enumerable: true,
		get() {
			return stored;
		},
		set(value) {
			stored = install(value);
		},
	});
})();
