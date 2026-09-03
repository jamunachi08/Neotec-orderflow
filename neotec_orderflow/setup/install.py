# =====================================================================
# Neotec OrderFlow — install / migrate (idempotent, code-driven)
# Runs on after_install AND after_migrate: safe to run repeatedly.
# =====================================================================

import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "Warehouse": [
        {
            "fieldname": "custom_whprefix",
            "label": "WH Print Prefix (Neotec)",
            "fieldtype": "Data",
            "insert_after": "warehouse_type",
            "description": "Short label (e.g. VZ, K1). Warehouses with a prefix "
                           "appear as columns on the quotation stock print.",
        },
        {
            "fieldname": "custom_state",
            "label": "Region / State (Neotec)",
            "fieldtype": "Data",
            "insert_after": "custom_whprefix",
            "description": "India: exact GST state name (e.g. Andhra Pradesh). "
                           "KSA: branch/region name. Drives per-state grouping "
                           "and compliance checks.",
        },
    ],
    "Sales Order Item": [
        {
            "fieldname": "custom_available_qty",
            "label": "Available Qty (at allocation)",
            "fieldtype": "Float",
            "insert_after": "warehouse",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
        },
    ],
}

PRINT_FORMAT_NAME = "Quotation Stock Availability (Neotec)"

# name -> (doctype, html file, prefix injected at top of the template)
PRINT_FORMATS = {
    PRINT_FORMAT_NAME: ("Quotation", "quotation_stock_availability.html", ""),
    "Neotec Delivery Note": ("Delivery Note", "neotec_document.html",
                             '{% set nof_kind = "dn" %}{% set nof_variant = "modern" %}\n'),
    "Neotec Tax Invoice": ("Sales Invoice", "neotec_document.html",
                           '{% set nof_kind = "si" %}{% set nof_variant = "modern" %}\n'),
    "Neotec Delivery Note (Classic)": ("Delivery Note", "neotec_document.html",
                             '{% set nof_kind = "dn" %}{% set nof_variant = "classic" %}\n'),
    "Neotec Tax Invoice (Classic)": ("Sales Invoice", "neotec_document.html",
                           '{% set nof_kind = "si" %}{% set nof_variant = "classic" %}\n'),
}
DEFAULTABLE = {"Delivery Note": "Neotec Delivery Note", "Sales Invoice": "Neotec Tax Invoice"}
DEFAULTABLE_CLASSIC = {"Delivery Note": "Neotec Delivery Note (Classic)",
                       "Sales Invoice": "Neotec Tax Invoice (Classic)"}


def ensure_all():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    ensure_print_formats()
    try:
        ps = frappe.get_doc("Neotec Print Settings")
        ps.validate()          # seed friendly default texts
        ps.db_update()
        apply_default_print_formats(bool(ps.set_as_default), bool(ps.get("classic_set_as_default")))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Neotec Print Settings seed")
    frappe.db.commit()


def ensure_print_format():
    """Backward-compatible single-format entry point."""
    ensure_print_formats()


def ensure_print_formats():
    for name, (doctype, filename, prefix) in PRINT_FORMATS.items():
        html_path = os.path.join(frappe.get_app_path("neotec_orderflow"), "print", filename)
        with open(html_path, encoding="utf-8") as f:
            html = prefix + f.read()

        if frappe.db.exists("Print Format", name):
            pf = frappe.get_doc("Print Format", name)
        else:
            pf = frappe.new_doc("Print Format")
            pf.name = name

        pf.update({
            "doc_type": doctype,
            "module": "Neotec OrderFlow",
            "print_format_type": "Jinja",
            "standard": "No",
            "disabled": 0,
            "html": html,
            "margin_top": 5, "margin_bottom": 5, "margin_left": 5, "margin_right": 5,
            "page_size": "A4",
            "default_print_language": "en",
        })
        if pf.meta.has_field("orientation"):
            pf.orientation = "Landscape"
        pf.save(ignore_permissions=True)


def apply_default_print_formats(enable_modern: bool, enable_classic: bool = False):
    """Set / clear the Neotec formats as the default for DN & SI.
    Classic wins if both are ticked."""
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter
    for doctype in DEFAULTABLE:
        want = DEFAULTABLE_CLASSIC[doctype] if enable_classic else (
            DEFAULTABLE[doctype] if enable_modern else None)
        ours = {DEFAULTABLE[doctype], DEFAULTABLE_CLASSIC[doctype]}
        if want:
            make_property_setter(doctype, None, "default_print_format", want, "Data",
                                 for_doctype=True, validate_fields_for_doctype=False)
        else:
            for pf in ours:
                frappe.db.delete("Property Setter", {"doc_type": doctype,
                                                      "property": "default_print_format",
                                                      "value": pf})
