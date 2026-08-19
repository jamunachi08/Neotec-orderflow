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

## Neotec Delivery Note & Tax Invoice print formats (v1.4.0)

Two landscape-A4 print formats are installed and kept in sync from code:

* **Neotec Delivery Note** — Delivery Note
* **Neotec Tax Invoice** — Sales Invoice

Every block is customised from **Neotec Print Settings** (Single): logos (company / brand),
colours & font, titles, package grid, payment QR, e-Invoice block, item-table columns
(HSN, image, unit price, discount columns, tax column mode, line total), totals
(round-off, words, customer previous/current balance, HSN summary, freight / P&F lines),
bank details, terms source, package-inspection text, signatory and footer lines.
Tick *Set Neotec formats as default* to make them the default print for both doctypes.

When printing, choose **No Letterhead** — the formats carry their own branded header.
Package counts/weights are read from optional custom fields `custom_bag`, `custom_box`,
`custom_carton`, `custom_w_box`, `custom_other`, `custom_length` (+ `_weight` variants)
on the document; otherwise a blank grid can be printed for manual fill.

### Classic variants (v1.5.0)
`Neotec Tax Invoice (Classic)` and `Neotec Delivery Note (Classic)` keep the legacy
charges band under the item table — *Total Before Freight*, then Freight / Insurance /
Packing & Forwarding (each with SAC and its own tax columns), Round Off, and the total —
with per-variant defaults: invoice shows all CGST/SGST/IGST rate+amount columns,
challan shows the three rate columns only. Configure under **Neotec Print Settings →
Classic Variants**. The original `Neotec Tax Invoice` / `Neotec Delivery Note` are unchanged.
