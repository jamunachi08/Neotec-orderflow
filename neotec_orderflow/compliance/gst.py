# =====================================================================
# Neotec OrderFlow — India GST pre-flight
#   ERRORS: E1 multi-state dispatch in Single Order
#           E2 dispatch state without company GSTIN registration
#           E3 dispatch warehouse missing state (when states matter)
#   WARNINGS: W1 tax type flips vs quotation (IGST vs CGST+SGST)
#             (escalated to ERROR when settings.tax_flip_is_error)
#             W2 items with missing HSN code
# Rate/return correctness stays with the India Compliance app.
# =====================================================================

import frappe
from frappe import _
from frappe.utils import flt

from neotec_orderflow.compliance import (
    company_address_map, norm_state, used_states,
)


def _customer_state(qtn):
    pos = qtn.get("place_of_supply")
    if pos:
        return norm_state(pos)
    if qtn.get("customer_address"):
        st = frappe.db.get_value("Address", qtn.customer_address, "state")
        if st:
            return norm_state(st)
    return None


def _quotation_tax_type(qtn):
    heads = " ".join((t.account_head or "") + " " + (t.description or "")
                     for t in (qtn.get("taxes") or [])).upper()
    if "IGST" in heads:
        return "igst"
    if "CGST" in heads or "SGST" in heads:
        return "cgst_sgst"
    return None


def validate(qtn, allocations, group_by, data, settings):
    errors, warnings = [], []

    states = used_states(allocations, data)
    states_in_play = data["has_state_field"] and (
        group_by == "state" or len([s for s in states if s]) > 1
    )

    # E3
    if states_in_play and "" in states:
        errors.append(_("These warehouses have no State set (Warehouse > custom_state): {0}")
                      .format(", ".join(states[""])))

    named = {s: w for s, w in states.items() if s}

    # E1
    if group_by == "single" and len(named) > 1:
        detail = "; ".join(f"{s.title()}: {', '.join(w)}" for s, w in named.items())
        errors.append(_("Allocations dispatch from multiple states in ONE order ({0}). "
                        "Under GST, each state's supplies must be invoiced from that "
                        "state's GSTIN — switch grouping to 'One Order per State'.")
                      .format(detail))

    # E2
    gstin_states = {}
    for a in company_address_map(qtn.company):
        gstin = (a.get("gstin") or "").strip()
        state = a.get("gst_state") or a.get("state")
        if gstin and state:
            gstin_states[norm_state(state)] = gstin

    if data["has_state_field"] and named:
        if gstin_states:
            for s, whs in named.items():
                if s not in gstin_states:
                    errors.append(_("No company GSTIN registration found for state '{0}' "
                                    "(dispatching warehouses: {1}). Register a company "
                                    "Address with GSTIN for this state, or do not "
                                    "dispatch from it.")
                                  .format(s.title(), ", ".join(whs)))
        else:
            warnings.append(_("Could not read company GSTIN registrations from company "
                              "Addresses — GSTIN-per-state check skipped. Verify manually."))

    # W1 (or error if escalated)
    cust_state = _customer_state(qtn)
    qtn_tax = _quotation_tax_type(qtn)
    if cust_state and qtn_tax and named:
        for s in named:
            expected = "cgst_sgst" if s == cust_state else "igst"
            if expected != qtn_tax:
                msg = _("Dispatch from '{0}' to customer in '{1}' is {2} supply ({3} "
                        "applies), but the quotation was taxed with {4}. Taxes on the "
                        "Sales Order/Invoice for this group will differ from the "
                        "quotation — review before submitting.").format(
                    s.title(), cust_state.title(),
                    _("intra-state") if expected == "cgst_sgst" else _("inter-state"),
                    "CGST+SGST" if expected == "cgst_sgst" else "IGST",
                    "IGST" if qtn_tax == "igst" else "CGST+SGST")
                (errors if settings.tax_flip_is_error else warnings).append(msg)

    # W2
    alloc_items = sorted({a["item_code"] for a in allocations if flt(a.get("qty")) > 0})
    if alloc_items and frappe.get_meta("Item").has_field("gst_hsn_code"):
        missing = [i.name for i in frappe.get_all(
            "Item", filters={"name": ["in", alloc_items],
                             "gst_hsn_code": ["in", ["", None]]}, fields=["name"])]
        if missing:
            warnings.append(_("HSN code missing on: {0}. The e-invoice/GSTR-1 will "
                              "fail later — set HSN on the Item master.")
                            .format(", ".join(missing)))

    return {"errors": errors, "warnings": warnings}
