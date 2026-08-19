# =====================================================================
# Neotec OrderFlow — compliance dispatcher
# Region-pluggable pre-flight. Each region module exposes
# validate(qtn, allocations, group_by, data, settings)
#     -> {"errors": [...], "warnings": [...]}
# Errors block Sales Order creation; warnings require user confirmation.
# =====================================================================

import frappe


def run_compliance(qtn, allocations, group_by, data, settings):
    empty = {"errors": [], "warnings": []}
    if not settings.enable_compliance_checks:
        return empty
    region = (settings.region or "None").strip()
    if region == "India":
        from neotec_orderflow.compliance import gst
        return gst.validate(qtn, allocations, group_by, data, settings)
    if region == "KSA":
        from neotec_orderflow.compliance import ksa_vat
        return ksa_vat.validate(qtn, allocations, group_by, data, settings)
    return empty


# ---- shared helpers used by region modules ----

def used_warehouses(allocations):
    from frappe.utils import flt
    return sorted({a["warehouse"] for a in allocations if flt(a.get("qty")) > 0})


def used_states(allocations, data):
    """{normalized_state_or_empty: [warehouses]}"""
    from frappe.utils import flt
    wh_state = {r["warehouse"]: r["state"]
                for rows in data["stock"].values() for r in rows}
    out = {}
    for a in allocations:
        if flt(a.get("qty")) <= 0:
            continue
        out.setdefault(norm_state(wh_state.get(a["warehouse"])), []) \
           .append(a["warehouse"])
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def norm_state(s):
    import re
    s = (s or "").strip()
    s = re.sub(r"^\d{1,2}\s*-\s*", "", s)
    return re.sub(r"\s+", " ", s).lower()


def company_address_map(company):
    """Company-linked Addresses -> list of dicts (state + GST fields
    when present on the Address doctype)."""
    links = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Company", "link_name": company,
                 "parenttype": "Address"},
        fields=["parent"],
    )
    meta = frappe.get_meta("Address")
    fields = ["name", "state", "country"]
    for f in ("gstin", "gst_state"):
        if meta.has_field(f):
            fields.append(f)
    out = []
    for l in links:
        a = frappe.db.get_value("Address", l.parent, fields, as_dict=True)
        if a:
            out.append(a)
    return out
