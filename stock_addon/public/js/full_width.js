// Stock Addon — the desk fills the window by default.
//
// ERPNext v16 caps page content at --page-max-width (900px), so on a wide
// screen the workspaces, lists and forms sit squeezed into a column in the
// middle with empty space either side. The wide tables this app adds — the
// route and banking reports, the stock reports, the customers tab — are the
// worst hit.
//
// Frappe already has the switch for it: "Toggle Full Width" in the desk's
// sidebar menu, remembered per browser in localStorage. All this does is
// make ON the default for a browser that has never been told either way.
// The desk applies it a moment later when it starts up
// (frappe.Desk.set_fullwidth_if_enabled), and anyone who prefers the narrow
// page can still switch it off — their choice is left alone from then on.

(function () {
    if (typeof localStorage === "undefined") return;

    try {
        if (localStorage.container_fullwidth == null) {
            localStorage.container_fullwidth = "true";
        }
    } catch (e) {
        return; // storage blocked (a private window): leave the desk as it is
    }

    // If the desk read the setting before this file loaded, apply it now.
    const toolbar = typeof frappe !== "undefined" && frappe.ui && frappe.ui.toolbar;
    if (toolbar && toolbar.set_fullwidth_if_enabled && document.body) {
        toolbar.set_fullwidth_if_enabled();
    }
})();
