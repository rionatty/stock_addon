// Stock Addon — collapsible form sidebar.
//
// The right-hand panel on a form (avatar, Assign / Attachments / Tags /
// Share, created + edited timestamps) is useful but permanently eats
// horizontal space. This adds a round handle on the boundary between the
// content and that panel: click to collapse, click again to bring it
// back. The choice is remembered in this browser and applies to every
// form until it is toggled again.
//
// Plain asset (no bench build needed for this file); the styling lives in
// public/css/stock_addon.bundle.css.

(function () {
	if (typeof frappe === "undefined") return;

	const STORAGE_KEY = "stock_addon:form_sidebar_collapsed";
	const COLLAPSED_CLASS = "sa-form-sidebar-collapsed";

	function stored() {
		try {
			return localStorage.getItem(STORAGE_KEY) === "1";
		} catch (e) {
			// private mode / blocked storage — default to expanded
			return false;
		}
	}

	function persist(collapsed) {
		try {
			localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
		} catch (e) {
			// not persistable here; the toggle still works for this session
		}
	}

	function apply(collapsed, button) {
		document.body.classList.toggle(COLLAPSED_CLASS, collapsed);
		if (!button) return;
		// ‹ pulls the panel back in, › pushes it away
		button.textContent = collapsed ? "‹" : "›";
		button.setAttribute("aria-expanded", String(!collapsed));
		button.setAttribute(
			"title",
			collapsed ? __("Show details panel") : __("Hide details panel")
		);
	}

	function inject() {
		// Frappe keeps previously visited pages in the DOM — only ever
		// touch the one actually on screen.
		const container = document.querySelector(".page-container:not(.hide)");
		if (!container) return;

		const side = container.querySelector(".layout-side-section");
		const main = container.querySelector(".layout-main-section-wrapper");
		if (!side || !main) return;
		if (main.querySelector(".sa-sidebar-toggle")) {
			apply(stored(), main.querySelector(".sa-sidebar-toggle"));
			return;
		}

		const button = document.createElement("button");
		button.type = "button";
		button.className = "sa-sidebar-toggle";
		button.setAttribute("aria-label", __("Toggle details panel"));
		button.addEventListener("click", function () {
			const next = !document.body.classList.contains(COLLAPSED_CLASS);
			persist(next);
			apply(next, button);
		});

		main.appendChild(button);
		apply(stored(), button);
	}

	function refresh() {
		apply(stored());
		setTimeout(inject, 120);
	}

	$(document).on("page-change", refresh);
	$(document).ready(refresh);
})();
