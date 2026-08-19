import frappe
from frappe.model.document import Document

DEFAULT_DN_TERMS = (
    "1) Material delivered on your request to a 2nd party will not be our liability.\n"
    "2) Exchange / return of goods in a subsequent invoiced month will not be accepted.\n"
    "3) Material urgently arranged from our sources will not be reinstated, replaced or cancelled.\n"
    "4) Return of material without prior intimation will not be accepted."
)
DEFAULT_SI_TERMS = (
    "1) INR 500 will be applicable for every dishonoured cheque.\n"
    "2) Material delivered on your request to a 2nd party will not be our liability.\n"
    "3) Exchange / return of goods in a subsequent invoiced month will not be accepted.\n"
    "4) Material urgently arranged from our sources will not be reinstated, replaced or cancelled.\n"
    "5) Return of material without prior intimation will not be accepted."
)
DEFAULT_PKG = (
    "Please ensure all components are checked against the enclosed list when opening the "
    "package. If your package contains an inner pouch, do not remove the smaller items before "
    "verifying the contents. Our liability ceases if any component is found missing after the "
    "inner packet (if applicable) is opened."
)
DEFAULT_PAY_NOTE = (
    "If this or any previous invoice amount is not received by the due date, or if the total "
    "outstanding exceeds your assigned credit limit (whichever is earlier), further dispatches "
    "may be suspended without limiting our rights or remedies."
)


class NeotecPrintSettings(Document):
    def validate(self):
        # fill friendly defaults once so the admin sees editable text, not blanks
        if not self.dn_terms:
            self.dn_terms = DEFAULT_DN_TERMS
        if not self.si_terms:
            self.si_terms = DEFAULT_SI_TERMS
        if not self.package_inspection_text:
            self.package_inspection_text = DEFAULT_PKG
        if not self.payment_terms_note:
            self.payment_terms_note = DEFAULT_PAY_NOTE

    def on_update(self):
        from neotec_orderflow.setup.install import apply_default_print_formats
        apply_default_print_formats(bool(self.set_as_default), bool(self.classic_set_as_default))
