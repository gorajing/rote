"""AcmeBilling Flask server — deterministic arena for CU eval."""
from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import state


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.route("/")
    def index():
        return redirect(url_for("billing"))

    @app.route("/billing", methods=["GET"])
    @app.route("/billing/filter/<status>", methods=["GET", "POST"])
    def billing(status: str | None = None):
        if status and request.method in ("GET", "POST"):
            state.set_filter(status)
        snap = state.snapshot()
        flash = state.get_flash()
        state.clear_flash()
        visible = state.visible_invoices()
        variant = snap["variant"]
        return render_template(
            "billing.html",
            invoices=visible,
            all_count=len(snap["invoices"]),
            variant=variant,
            ui_filter=snap["ui_filter"],
            flash=flash,
            dispute_reasons=state.DISPUTE_REASONS,
            kpis=_kpis(snap["invoices"]),
            active_nav="invoices",
            structural=(variant == "move_dispute_to_cases"),
        )

    @app.route("/state")
    def get_state():
        snap = state.snapshot()
        return jsonify({
            "invoices": snap["invoices"],
            "settings": snap["settings"],
            "variant": snap["variant"],
            "ui_filter": snap["ui_filter"],
        })

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

    @app.route("/invoice/<invoice_id>")
    def invoice_detail(invoice_id: str):
        inv = state.find_invoice(invoice_id)
        if not inv:
            return redirect(url_for("billing"))
        return render_template(
            "invoice_detail.html",
            invoice=inv,
            variant=state.get_variant(),
            structural=(state.get_variant() == "move_dispute_to_cases"),
            active_nav="invoices",
        )

    @app.route("/invoice/<invoice_id>/dispute", methods=["GET", "POST"])
    def invoice_dispute(invoice_id: str):
        inv = state.find_invoice(invoice_id)
        if not inv:
            return redirect(url_for("billing"))
        if request.method == "GET":
            return render_template(
                "dispute_modal.html",
                invoice=inv,
                reasons=state.DISPUTE_REASONS,
                variant=state.get_variant(),
                active_nav="invoices",
            )
        reason = request.form.get("reason", "")
        if state.dispute(invoice_id, reason):
            state.set_flash(f"Invoice {invoice_id} marked as disputed ({reason}).")
        else:
            state.set_flash(f"Could not dispute invoice {invoice_id} — reason required.")
        return redirect(url_for("billing"))

    @app.route("/cases/dispute/<invoice_id>", methods=["GET", "POST"])
    def case_dispute(invoice_id: str):
        inv = state.find_invoice(invoice_id)
        if not inv:
            return redirect(url_for("billing"))
        if request.method == "GET":
            return render_template(
                "case_dispute.html",
                invoice=inv,
                reasons=state.DISPUTE_REASONS,
                variant=state.get_variant(),
                active_nav="invoices",
            )
        reason = request.form.get("reason", "")
        if state.dispute(invoice_id, reason):
            state.set_flash(f"Dispute case opened for {invoice_id} ({reason}).")
        else:
            state.set_flash(f"Could not open dispute case for {invoice_id}.")
        return redirect(url_for("billing"))

    @app.route("/invoice/<invoice_id>/flag", methods=["POST"])
    def invoice_flag(invoice_id: str):
        if state.flag_invoice(invoice_id):
            state.set_flash(f"Invoice {invoice_id} flagged for review.")
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
        return render_template(
            "note.html", invoice=inv, variant=state.get_variant(), active_nav="invoices",
        )

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
            state.update_settings(
                notifications=notifications, billing_email=billing_email, plan=plan,
            )
            state.set_flash("Settings updated.")
            return redirect(url_for("settings_page"))
        flash = state.get_flash()
        state.clear_flash()
        return render_template(
            "settings.html",
            settings=snap["settings"],
            variant=snap["variant"],
            flash=flash,
            active_nav="settings",
        )

    return app


def _kpis(invoices: list[dict]) -> dict:
    outstanding = sum(i["amount"] for i in invoices if i["status"] in ("unpaid", "disputed"))
    return {
        "outstanding": outstanding,
        "unpaid_count": sum(1 for i in invoices if i["status"] == "unpaid"),
        "disputed_count": sum(1 for i in invoices if i["status"] == "disputed"),
        "total_count": len(invoices),
    }


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8800, debug=False)
