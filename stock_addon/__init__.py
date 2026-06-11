__version__ = "0.0.1"


# ─── Standard ERPNext report monkey-patches ─────────────────────────────────
# Applied at module import time so any Frappe worker that touches stock_addon
# (which happens at app boot when Frappe loads our hooks.py) installs the
# patches before the first report request can run.

def _apply_standard_report_patches():
	"""Wrap standard ERPNext report `execute()` functions with our patched
	versions. Safe to import at app boot — fails silently if ERPNext isn't
	installed, and is idempotent (re-imports don't double-wrap)."""
	# General Ledger ─────────────────────────────────────────────────────────
	try:
		from erpnext.accounts.report.general_ledger import general_ledger as _gl
		if not getattr(_gl.execute, "_stock_addon_patched", False):
			from stock_addon.stock_addon.report_patches import (
				wrap_general_ledger_execute,
			)
			_gl.execute = wrap_general_ledger_execute(_gl.execute)
	except Exception:
		# Don't break boot if ERPNext isn't available or the patch errors.
		# Failures are logged via the patch wrapper at call time.
		pass


_apply_standard_report_patches()
