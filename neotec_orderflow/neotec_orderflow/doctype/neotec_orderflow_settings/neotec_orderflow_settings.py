import frappe
from frappe.model.document import Document


class NeotecOrderFlowSettings(Document):
    def validate(self):
        if self.enable_auto_so and not self.enable_compliance_checks \
                and (self.region or "None") != "None":
            frappe.msgprint(
                frappe._("Auto-creation is enabled but compliance checks are OFF. "
                         "Auto-created orders will skip the pre-flight — recommended "
                         "to keep compliance checks enabled."),
                indicator="orange", alert=True,
            )

    def on_update(self):
        frappe.clear_cache(doctype="Neotec OrderFlow Settings")
