# =====================================================================
# Neotec OrderFlow — KSA VAT pre-flight
# KSA has one national VAT (single TRN per legal entity in the common
# case), so multi-warehouse dispatch is NOT a tax split like India.
# The structural risks are different:
#   ERROR : K1 company has no Tax ID (TRN) — tax invoice cannot be
#           ZATCA-compliant without it
#   WARN  : K2 dispatch spans multiple regions/branches in ONE order —
#           per-branch Delivery Notes recommended so the delivery
#           address on the tax invoice chain is truthful
#   WARN  : K3 B2B customer without Tax ID (VAT number) — ZATCA
#           standard (B2B) invoices require buyer VAT number
#   WARN  : K4 items missing an Item Tax Template — VAT category
#           (standard/zero/exempt) resolution may fall to defaults
# E-invoice XML generation and VAT return correctness remain with the
# ZATCA integration app (e.g. ERPGulf) at invoice time.
# =====================================================================

import frappe
from frappe import _
from frappe.utils import flt

from neotec_orderflow.compliance import used_states


def validate(qtn, allocations, group_by, data, settings):
    errors, warnings = [], []

    # K1: company TRN
    trn = frappe.db.get_value("Company", qtn.company, "tax_id")
    if not (trn or "").strip():
        errors.append(_("Company '{0}' has no Tax ID (TRN/VAT number). Set it on the "
                        "Company master — ZATCA tax invoices cannot be issued without it.")
                      .format(qtn.company))

    # K2: multi-region dispatch in a single order
    if data["has_state_field"] and group_by == "single":
        named = {s: w for s, w in used_states(allocations, data).items() if s}
        if len(named) > 1:
            detail = "; ".join(f"{s.title()}: {', '.join(w)}" for s, w in named.items())
            warnings.append(_("Allocations dispatch from multiple regions/branches in "
                              "ONE order ({0}). Consider 'One Order per State' (region) "
                              "grouping, or ensure per-branch Delivery Notes so the "
                              "dispatch address on the invoice chain is accurate.")
                            .format(detail))

    # K3: B2B buyer VAT number
    cust_tax_id = frappe.db.get_value("Customer", qtn.get("party_name") or qtn.get("customer"),
                                      "tax_id") if (qtn.get("party_name") or qtn.get("customer")) else None
    cust_type = frappe.db.get_value("Customer", qtn.get("party_name") or qtn.get("customer"),
                                    "customer_type") if (qtn.get("party_name") or qtn.get("customer")) else None
    if cust_type == "Company" and not (cust_tax_id or "").strip():
        warnings.append(_("Customer '{0}' is a Company but has no VAT number (Tax ID). "
                          "ZATCA standard (B2B) tax invoices require the buyer VAT "
                          "number — set it on the Customer master.")
                        .format(qtn.get("customer_name") or qtn.get("party_name")))

    # K4: item tax templates
    alloc_items = sorted({a["item_code"] for a in allocations if flt(a.get("qty")) > 0})
    if alloc_items:
        rows_by_item = {d.item_code: d for d in qtn.items}
        missing = [i for i in alloc_items
                   if rows_by_item.get(i) and not rows_by_item[i].get("item_tax_template")]
        if missing:
            warnings.append(_("No Item Tax Template on quotation rows for: {0}. VAT "
                              "category (standard 15% / zero-rated / exempt) will fall "
                              "back to the document default — verify this is intended.")
                            .format(", ".join(missing)))

    return {"errors": errors, "warnings": warnings}
