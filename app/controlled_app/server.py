"""AcmeBilling Flask server — deterministic arena for CU eval."""
from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import state


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.route("/")
    def index():
        return redirect(url_for("billing"))

    @app.route("/billing")
    def billing():
        snap = state.snapshot()
        flash = state.get_flash()
        state.clear_flash()
        return render_template(
            "billing.html",
            invoices=snap["invoices"],
            variant=snap["variant"],
            flash=flash,
            export_label=_export_label(snap["variant"]),
            nav_label=_nav_label(snap["variant"]),
            column_order=_column_order(snap["variant"]),
            kpis=_kpis(snap["invoices"]),
            active_nav="invoices",
        )

    @app.route("/state")
    def get_state():
        snap = state.snapshot()
        return jsonify({"invoices": snap["invoices"], "settings": snap["settings"],
                        "variant": snap["variant"]})

    @app.route("/reset", methods=["POST"])
    def reset():
        variant = request.args.get("variant", "baseline")
        snap = state.reset(variant)
        return jsonify(snap)

    @app.route("/mutate/<name>")
    def mutate(name: str):
        if name in state.VARIANTS:
            state.reset(name)
        return redirect(url_for("billing"))

    @app.route("/invoice/<invoice_id>/dispute", methods=["POST"])
    def invoice_dispute(invoice_id: str):
        if state.dispute(invoice_id):
            state.set_flash(f"Invoice {invoice_id} marked as disputed.")
        else:
            state.set_flash(f"Could not dispute invoice {invoice_id}.")
        return redirect(url_for("billing"))

    @app.route("/invoice/<invoice_id>/note", methods=["GET", "POST"])
    def invoice_note(invoice_id: str):
        inv = state.find_invoice(invoice_id)
        if not inv:
            return redirect(url_for("billing"))
        if request.method == "POST":
            note = request.form.get("note", "")
            state.set_note(invoice_id, note)
            state.set_flash(f"Note saved for invoice {invoice_id}.")
            return redirect(url_for("billing"))
        return render_template("note.html", invoice=inv, variant=state.get_variant(),
                               nav_label=_nav_label(state.get_variant()))

    @app.route("/invoice/<invoice_id>/export", methods=["POST"])
    def invoice_export(invoice_id: str):
        if state.export_receipt(invoice_id):
            state.set_flash(f"Receipt exported for invoice {invoice_id}.")
        else:
            state.set_flash(f"Could not export invoice {invoice_id}.")
        return redirect(url_for("billing"))

    @app.route("/invoice/<invoice_id>/refund", methods=["POST"])
    def invoice_refund(invoice_id: str):
        if state.refund(invoice_id):
            state.set_flash(f"Invoice {invoice_id} refunded.")
        else:
            state.set_flash(f"Could not refund invoice {invoice_id}.")
        return redirect(url_for("billing"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        snap = state.snapshot()
        if request.method == "POST":
            notifications = request.form.get("notifications") == "on"
            billing_email = request.form.get("billing_email", "")
            plan = request.form.get("plan", "pro")
            state.update_settings(notifications=notifications,
                                  billing_email=billing_email, plan=plan)
            state.set_flash("Settings updated.")
            return redirect(url_for("settings_page"))
        flash = state.get_flash()
        state.clear_flash()
        return render_template("settings.html", settings=snap["settings"],
                               variant=snap["variant"], flash=flash,
                               nav_label=_nav_label(snap["variant"]),
                               active_nav="settings")

    return app


def _export_label(variant: str) -> str:
    return "Download PDF" if variant == "rename_export" else "Export receipt"


def _nav_label(variant: str) -> str:
    return "Billing items" if variant == "relabel_nav" else "Invoices"


def _column_order(variant: str) -> list[str]:
    if variant == "reorder_table":
        return ["amount", "customer", "status", "id", "note", "exported", "actions"]
    return ["id", "customer", "status", "amount", "note", "exported", "actions"]


def _kpis(invoices: list[dict]) -> dict:
    """Display-only summary derived from /state. Does NOT change the state contract."""
    outstanding = sum(i["amount"] for i in invoices if i["status"] in ("unpaid", "disputed"))
    return {
        "outstanding": outstanding,
        "unpaid_count": sum(1 for i in invoices if i["status"] == "unpaid"),
        "disputed_count": sum(1 for i in invoices if i["status"] == "disputed"),
        "total_count": len(invoices),
    }


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8800, debug=False)
