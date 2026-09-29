"""Public pages: landing page, lead form, privacy page."""

from __future__ import annotations

import json
import logging
import urllib.request

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Lead, User
from app.services.audit import audit
from app.web.deps import client_ip, form_decimal, redirect, render
from app.web.security import current_user, lead_limiter, verify_csrf

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/")
def landing(request: Request, user: User | None = Depends(current_user)):
    return render(request, "public/landing.html", user=user,
                  utm={k: request.query_params.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign",
                                                                    "ref")})


@router.post("/aanvraag", dependencies=[Depends(verify_csrf)])
def submit_lead(
    request: Request,
    company_name: str = Form(...),
    contact_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    kvk_number: str = Form(""),
    approx_annual_energy_spend: str = Form(""),
    number_of_locations: str = Form(""),
    message: str = Form(""),
    privacy_consent: str = Form(""),
    website: str = Form(""),  # honeypot: humans leave this empty
    utm_source: str = Form(""),
    utm_medium: str = Form(""),
    utm_campaign: str = Form(""),
    ref: str = Form(""),
    db: Session = Depends(get_db),
):
    if website:
        return redirect("/bedankt")
    if not lead_limiter.allow(client_ip(request) or "?"):
        return render(request, "public/landing.html", status_code=429, form_error="Te veel aanvragen. Probeer het "
                      "later opnieuw of mail ons.", utm={})
    errors = []
    if not privacy_consent:
        errors.append("Geef toestemming voor het verwerken van uw gegevens om contact op te nemen.")
    if "@" not in email or len(email) > 254:
        errors.append("Vul een geldig e-mailadres in.")
    kvk = "".join(ch for ch in kvk_number if ch.isdigit())
    if kvk and len(kvk) != 8:
        errors.append("Een KvK-nummer bestaat uit 8 cijfers.")
    spend = None
    try:
        spend = form_decimal(approx_annual_energy_spend)
    except ValueError:
        errors.append("Vul de energiekosten in als bedrag, bijv. 25000.")
    if errors:
        return render(request, "public/landing.html", status_code=400, form_error=" ".join(errors), utm={})
    lead = Lead(
        company_name=company_name.strip()[:200], contact_name=contact_name.strip()[:200], email=email.strip()[:254],
        phone=phone.strip()[:50] or None, kvk_number=kvk or None, approx_annual_energy_spend=spend,
        number_of_locations=int(number_of_locations) if number_of_locations.isdigit() else None,
        message=message.strip()[:2000] or None, privacy_consent=True,
        source="referral" if ref else "website", referral_partner=ref[:200] or None,
        utm_source=utm_source[:100] or None, utm_medium=utm_medium[:100] or None,
        utm_campaign=utm_campaign[:100] or None,
    )
    db.add(lead)
    db.flush()
    audit(db, "lead.created", object_type="lead", object_id=lead.id, ip=client_ip(request))
    db.commit()
    _notify_webhook(lead)
    return redirect("/bedankt")


def _notify_webhook(lead: Lead) -> None:
    """Optional CRM hook. Sends lead metadata only (never invoice data)."""
    url = get_settings().lead_webhook_url
    if not url or not url.startswith("https://"):
        return
    payload = json.dumps({"event": "lead.created", "lead_id": lead.id, "company_name": lead.company_name,
                          "contact_name": lead.contact_name, "email": lead.email, "source": lead.source,
                          "utm_campaign": lead.utm_campaign}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")  # noqa: S310
    try:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310  (https:// enforced above)
    except Exception:
        log.warning("lead webhook failed")


@router.get("/bedankt")
def thanks(request: Request):
    return render(request, "public/thanks.html")


@router.get("/privacy")
def privacy(request: Request):
    return render(request, "public/privacy.html")


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
