# =====================================================================
# Neotec OrderFlow — auto-create Sales Order on Quotation submit
# GUARDRAILS (non-negotiable by design):
#   1. Only runs when enabled in settings (default OFF).
#   2. Only fires when EVERY item's full balance is coverable by
#      available stock AND compliance returns zero errors AND zero
#      warnings. Anything ambiguous -> skip + comment on Quotation.
#   3. Created orders are always drafts, with a traceability comment.
#   4. NEVER blocks quotation submission: all failures are caught,
#      logged, and commented.
# =====================================================================

import frappe
from frappe import _
from frappe.utils import flt

from neotec_orderflow.api.so_generator import (
    create_sales_orders, get_allocation_data, get_settings,
)
from neotec_orderflow.compliance import run_compliance


def on_quotation_submit(doc, method=None):
    try:
        s = get_settings()
        if not (s.enabled and s.enable_auto_so):
            return
        _auto_create(doc, s)
    except Exception:
        frappe.log_error(title="Neotec OrderFlow auto-SO failed",
                         message=frappe.get_traceback())
        try:
            doc.add_comment("Comment",
                _("Neotec OrderFlow: auto-creation failed with an error — "
                  "see Error Log. Use the Create Sales Order button instead."))
        except Exception:
            pass


def _auto_create(doc, s):
    data = get_allocation_data(doc.name)

    # Build greedy allocations; bail out on ANY shortfall
    allocations = []
    for item in data["items"]:
        balance = flt(item["balance_qty"])
        if balance <= 0:
            continue
        stock = data["stock"].get(item["item_code"]) or []
        total_avail = sum(flt(r["available_qty"]) for r in stock)
        if total_avail + 0.0001 < balance:
            doc.add_comment("Comment",
                _("Neotec OrderFlow: auto-creation skipped — {0} needs {1} but only "
                  "{2} available. Use the Create Sales Order button to allocate "
                  "partially.").format(item["item_code"], balance, total_avail))
            return
        need = balance
        for r in stock:
            take = min(need, flt(r["available_qty"]))
            if take > 0:
                allocations.append({"item_code": item["item_code"],
                                    "warehouse": r["warehouse"], "qty": take})
                need -= take
            if need <= 0:
                break

    if not allocations:
        return  # nothing to order (fully ordered already, or no balance)

    group_by = s.auto_grouping or "single"

    # Compliance must be COMPLETELY clean for silent creation
    comp = run_compliance(doc, allocations, group_by, data, s)
    if comp["errors"] or comp["warnings"]:
        issues = comp["errors"] + comp["warnings"]
        doc.add_comment("Comment",
            _("Neotec OrderFlow: auto-creation skipped — compliance review needed:")
            + "<br>" + "<br>".join(issues)
            + "<br>" + _("Use the Create Sales Order button to review and proceed."))
        return

    result = create_sales_orders(doc.name, allocations, group_by)
    links = ", ".join(
        f'<a href="/app/sales-order/{so["name"]}">{so["name"]}</a>'
        f' ({so["group"]})'
        for so in result["created"])
    doc.add_comment("Comment",
        _("Neotec OrderFlow: auto-created {0} draft Sales Order(s): {1}")
        .format(len(result["created"]), links))
