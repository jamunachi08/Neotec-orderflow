# Neotec OrderFlow

Warehouse-wise fulfilment engine for ERPNext v15 (Frappe Cloud ready).

Quotation -> Sales Order -> Delivery Note flow with:
- Stock availability matrix on Quotation (screen + print format)
- Warehouse-wise SO generation from Quotation with balance tracking
- Grouping modes: Single Order / Per Warehouse / Per Region-State
- Optional auto-creation of draft SOs on Quotation submit (guarded)
- Region-pluggable compliance pre-flight: India (GST) / KSA (VAT)
- Manual SO warehouse allocator for directly-entered orders
- One Settings doc (Neotec OrderFlow Settings) controls everything

Install: add to Frappe Cloud bench via GitHub, install on site.
All custom fields and the print format are created idempotently on
install/migrate. Configure via: Neotec OrderFlow Settings.
