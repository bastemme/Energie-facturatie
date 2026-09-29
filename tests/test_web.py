import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as db_module
from app.devtools.synthetic import render_invoice_pdf
from app.domain.enums import CaseStatus, ReviewStatus, Role
from app.models import Anomaly, AuditLog, Client, Contract, ContractPrice, Document, Invoice, Lead, RecoveryCase, User
from app.web.security import hash_password, login_throttle
from tests.factories import electricity_invoice

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app_client():
    from app.web.app import create_app
    from tests.conftest import TEST_DB_URL

    db_module.init_engine(TEST_DB_URL)
    db_module.Base.metadata.drop_all(db_module.get_engine())
    app = create_app(TEST_DB_URL)
    with TestClient(app) as c:
        yield c
    login_throttle._buckets.clear()
    db_module.Base.metadata.drop_all(db_module.get_engine())


def session():
    return db_module.session_factory()()


def make_user(email, role, client_id=None):
    with session() as s:
        u = User(email=email, password_hash=hash_password(PASSWORD), role=role, client_id=client_id)
        s.add(u)
        s.commit()
        return u.id


def make_client(name="Klant A B.V.", fee="15"):
    with session() as s:
        c = Client(company_name=name, success_fee_percentage=Decimal(fee), consent_given=True)
        s.add(c)
        s.commit()
        return c.id


def csrf(client, url="/login"):
    html = client.get(url).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, email):
    token = csrf(client)
    r = client.post("/login", data={"email": email, "password": PASSWORD, "csrf_token": token})
    assert r.status_code == 200, r.text[:500]
    return r


# ---------------------------------------------------------------- public


def test_landing_page(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    assert "Vind fouten in uw energiefacturen" in r.text and "No cure, no pay" in r.text
    assert "Laat mijn energiefacturen controleren" in r.text and "Bekijk hoe het werkt" in r.text
    assert "fictieve gegevens" in r.text and "Fictief voorbeeld" in r.text
    assert "fonts.googleapis" not in r.text  # fonts are self-hosted (no visitor data to third parties)
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_lead_submission(app_client):
    token = csrf(app_client, "/")
    data = {"csrf_token": token, "company_name": "Bakkerij X", "contact_name": "Jan", "email": "jan@x.nl",
            "kvk_number": "12345678", "approx_annual_energy_spend": "45.000", "privacy_consent": "on",
            "utm_source": "linkedin", "utm_campaign": "q4"}
    r = app_client.post("/aanvraag", data=data)
    assert r.status_code == 200 and "Bedankt" in r.text
    with session() as s:
        lead = s.scalar(select(Lead))
        assert lead.approx_annual_energy_spend == Decimal("45000") and lead.utm_source == "linkedin"


def test_lead_requires_csrf_and_consent(app_client):
    base = {"company_name": "X", "contact_name": "Y", "email": "a@b.nl"}
    assert app_client.post("/aanvraag", data=base).status_code == 403
    r = app_client.post("/aanvraag", data={**base, "csrf_token": csrf(app_client, "/")})
    assert r.status_code == 400


def test_lead_honeypot_silently_dropped(app_client):
    data = {"csrf_token": csrf(app_client, "/"), "company_name": "Spam", "contact_name": "S", "email": "s@s.nl",
            "privacy_consent": "on", "website": "http://spam"}
    app_client.post("/aanvraag", data=data)
    with session() as s:
        assert s.scalar(select(Lead)) is None


# ---------------------------------------------------------------- auth


def test_login_failure_and_lockout(app_client):
    make_user("staff@op.nl", Role.ADMIN)
    for _ in range(5):
        r = app_client.post("/login", data={"email": "staff@op.nl", "password": "wrong-password!",
                                            "csrf_token": csrf(app_client)})
        assert r.status_code == 401
    r = app_client.post("/login", data={"email": "staff@op.nl", "password": PASSWORD, "csrf_token": csrf(app_client)})
    assert r.status_code == 429


def test_app_requires_login(app_client):
    r = app_client.get("/app/clients", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_password_policy():
    with pytest.raises(ValueError):
        hash_password("short")


# ---------------------------------------------------------------- full staff workflow


def test_end_to_end_recovery_workflow(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    login(app_client, "admin@op.nl")

    # intake: fee and consent are mandatory
    token = csrf(app_client, "/app/clients/new")
    r = app_client.post("/app/clients/new", data={"csrf_token": token, "company_name": "Metaal B.V."})
    assert r.status_code == 400 and "Succesvergoeding" in r.text and "machtiging" in r.text
    r = app_client.post("/app/clients/new", data={
        "csrf_token": token, "company_name": "Metaal B.V.", "kvk_number": "12345678", "success_fee_percentage": "20",
        "consent_given": "on", "consent_given_by": "J. Jansen, directeur", "vat_deductible": "on"})
    assert r.status_code == 200
    with session() as s:
        client = s.scalar(select(Client).where(Client.company_name == "Metaal B.V."))
        client_id = client.id
        assert client.success_fee_percentage == Decimal("20") and client.consent_given_by.startswith("J. Jansen")
        c = Contract(client_id=client_id, supplier="Voorbeeld Energie", start_date=date(2025, 1, 1),
                     verified_at=client.created_at)
        c.prices.append(ContractPrice(category="ELECTRICITY_NORMAL", unit="kWh", price=Decimal("0.20"),
                                      valid_from=date(2025, 1, 1)))
        s.add(c)
        s.commit()

    # upload a synthetic invoice with a tariff error
    syn = electricity_invoice("2026-0012", normal_price=Decimal("0.25"), supplier="Voorbeeld Energie")
    token = csrf(app_client, f"/app/clients/{client_id}")
    r = app_client.post(f"/app/clients/{client_id}/upload", data={"csrf_token": token},
                        files=[("files", ("factuur.pdf", render_invoice_pdf(syn), "application/pdf"))])
    assert r.status_code == 200 and "1 document(en) verwerkt" in r.text

    r = app_client.post(f"/app/clients/{client_id}/analyse", data={"csrf_token": token})
    assert "Analyse afgerond" in r.text

    with session() as s:
        a = s.scalar(select(Anomaly).where(Anomaly.rule_id == "contract_price"))
        assert a.potential_recovery == Decimal("50.00")
        anomaly_id = a.id
        doc_id = s.scalar(select(Document.id))

    # invoice analysis view shows the same document with the flagged line highlighted
    with session() as s:
        inv_id = s.scalar(select(Invoice.id))
    r = app_client.get(f"/app/invoices/{inv_id}")
    assert r.status_code == 200 and 'class="hl"' in r.text and "Uitgelezen regels" in r.text
    assert app_client.get(f"/app/invoices/{inv_id}/edit").status_code == 200

    # finding detail: split view with the source page and a highlight overlay linked to the evidence
    r = app_client.get(f"/app/anomalies/{anomaly_id}")
    assert "Mogelijke discrepantie" in r.text and "€ 50,00" in r.text
    assert f'/app/documents/{doc_id}/page/1.png' in r.text
    assert re.search(r'<rect id="hl0" class="hl"', r.text) and 'data-hl="hl0"' in r.text
    for step in ("Bron", "Analyse", "Berekening", "Conclusie"):
        assert f'class="step-label">{step}<' in r.text
    png = app_client.get(f"/app/documents/{doc_id}/page/1.png?hl=50,240,540,260")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")

    # reject requires a note; confirm works
    token = csrf(app_client, f"/app/anomalies/{anomaly_id}")
    r = app_client.post(f"/app/anomalies/{anomaly_id}/review", data={"csrf_token": token, "action": "reject"})
    assert "verplicht" in r.text
    app_client.post(f"/app/anomalies/{anomaly_id}/review", data={"csrf_token": token, "action": "confirm",
                                                                 "note": "Tarief gecontroleerd tegen contract p.2"})

    # case: create, move through lifecycle, amounts, fee
    r = app_client.post(f"/app/clients/{client_id}/cases", data={"csrf_token": token, "anomaly_ids": anomaly_id})
    assert r.status_code == 200 and "Dossier ER-" in r.text
    with session() as s:
        case = s.scalar(select(RecoveryCase))
        case_id = case.id
        assert case.disputed_amount == Decimal("50.00") and case.success_fee_percentage == Decimal("20")
    assert "Verzoek tot correctie factuur 2026-0012" in r.text

    def move(to, note="ok"):
        return app_client.post(f"/app/cases/{case_id}/transition", data={"csrf_token": token, "to_status": to,
                                                                        "note": note})

    for to in ["VERIFIED", "CLIENT_APPROVAL", "SUBMITTED", "SUPPLIER_REVIEW"]:
        move(to)
    r = move("APPROVED")
    assert "toegekende bedrag" in r.text  # needs confirmed amount first
    app_client.post(f"/app/cases/{case_id}/amounts", data={"csrf_token": token, "confirmed_amount": "50,00"})
    move("APPROVED")
    r = app_client.post(f"/app/cases/{case_id}/amounts", data={"csrf_token": token, "recovered_amount": "50,00"})
    assert "referentie" in r.text.lower()
    app_client.post(f"/app/cases/{case_id}/amounts", data={"csrf_token": token, "recovered_amount": "50,00",
                                                           "recovered_reference": "CN-778812"})
    move("RECOVERED")
    with session() as s:
        case = s.get(RecoveryCase, case_id)
        assert case.status == CaseStatus.RECOVERED
        assert case.recovered_amount == Decimal("50.00") and case.success_fee == Decimal("10.00")
        assert len(case.events) >= 7

    # claim package
    r = app_client.get(f"/app/cases/{case_id}/package.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any(n.endswith(".pdf") and "verzoek" in n for n in names)
    assert any(n.startswith("bijlagen/") and n.endswith("factuur.pdf") for n in names)
    data = json.loads(zf.read(next(n for n in names if n.endswith(".json"))))
    assert data["items"][0]["potential_recovery"] == "50.00"
    assert data["case"]["recovered_amount"] == "50.00"

    # client report + dashboard show separated figures
    r = app_client.get(f"/app/clients/{client_id}/report.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = app_client.get("/app/admin")
    assert "Teruggevorderd per 1.000 facturen" in r.text and "€ 50,00" in r.text
    assert '<svg class="chart"' in r.text  # monthly chart rendered server-side
    r = app_client.get(f"/app/clients/{client_id}?tab=overzicht")
    assert "Terugvordering" in r.text and "Factuurwaarde per maand" in r.text

    # audit trail contains the decisions, without amounts
    with session() as s:
        actions = {a.action for a in s.scalars(select(AuditLog)).all()}
        assert {"auth.login", "document.uploaded", "anomaly.review", "case.created", "case.package_exported"} <= actions
        assert not any("50" in json.dumps(a.details or {}) for a in s.scalars(select(AuditLog)).all())


def test_invoice_manual_correction_and_verification(app_client):
    make_user("rev@op.nl", Role.REVIEWER)
    client_id = make_client()
    login(app_client, "rev@op.nl")
    token = csrf(app_client, f"/app/clients/{client_id}")
    app_client.post(f"/app/clients/{client_id}/upload", data={"csrf_token": token},
                    files=[("files", ("f.pdf", render_invoice_pdf(electricity_invoice()), "application/pdf"))])
    with session() as s:
        inv = s.scalar(select(Invoice))
        inv_id, line_id = inv.id, inv.lines[0].id
    page = app_client.get(f"/app/invoices/{inv_id}/edit").text
    assert "uit PDF" in page
    form = {"csrf_token": token, "supplier": "Eneco", "invoice_number": "2026-0001", "invoice_type": "INVOICE",
            "invoice_date": "31-01-2026", "billing_period_start": "01-01-2026", "billing_period_end": "31-01-2026",
            "commodity": "ELECTRICITY", f"line-{line_id}-description": "Levering normaal",
            f"line-{line_id}-category": "ELECTRICITY_NORMAL", f"line-{line_id}-quantity": "1.100",
            f"line-{line_id}-unit": "kWh", f"line-{line_id}-unit_price": "0,20", f"line-{line_id}-amount": "220,00"}
    app_client.post(f"/app/invoices/{inv_id}", data=form)
    with session() as s:
        inv = s.get(Invoice, inv_id)
        line = next(li for li in inv.lines if li.id == line_id)
        assert line.quantity == Decimal("1100") and line.extraction_method.value == "MANUAL"
        assert inv.supplier == "Eneco" and not inv.is_verified
    app_client.post(f"/app/invoices/{inv_id}/verify", data={"csrf_token": token})
    with session() as s:
        assert s.get(Invoice, inv_id).is_verified


# ---------------------------------------------------------------- tenant isolation & roles


def test_client_user_isolation(app_client):
    a_id, b_id = make_client("A B.V."), make_client("B B.V.")
    make_user("a@klant.nl", Role.CLIENT, a_id)
    with session() as s:
        b = s.get(Client, b_id)
        from app.ingestion.pipeline import ingest_upload
        from app.services.analysis import run_analysis

        doc = ingest_upload(s, b, "b.pdf", render_invoice_pdf(electricity_invoice(normal_amount=Decimal("300"))),
                            None)
        run_analysis(s, b)
        s.commit()
        b_doc = doc.id
        b_anomaly = s.scalar(select(Anomaly).where(Anomaly.client_id == b_id)).id
        b_invoice = s.scalar(select(Invoice).where(Invoice.client_id == b_id)).id
    login(app_client, "a@klant.nl")
    assert app_client.get(f"/app/clients/{a_id}").status_code == 200
    for url in [f"/app/clients/{b_id}", f"/app/documents/{b_doc}", f"/app/documents/{b_doc}/download",
                f"/app/documents/{b_doc}/page/1.png", f"/app/anomalies/{b_anomaly}", f"/app/invoices/{b_invoice}",
                f"/app/clients/{b_id}/report.pdf"]:
        assert app_client.get(url).status_code == 404, url
    for url in ["/app/review", "/app/admin", "/app/clients", "/app/cases", "/app/reference-rates"]:
        assert app_client.get(url).status_code == 403, url
    token = csrf(app_client, f"/app/clients/{a_id}")
    assert app_client.post(f"/app/clients/{a_id}/analyse", data={"csrf_token": token}).status_code == 403
    # upload to another tenant is refused
    r = app_client.post(f"/app/clients/{b_id}/upload", data={"csrf_token": token},
                        files=[("files", ("x.pdf", render_invoice_pdf(electricity_invoice("Q")), "application/pdf"))])
    assert r.status_code == 404


def test_client_sees_only_confirmed_findings(app_client):
    a_id = make_client()
    make_user("a@klant.nl", Role.CLIENT, a_id)
    with session() as s:
        from app.ingestion.pipeline import ingest_upload
        from app.services.analysis import run_analysis

        ingest_upload(s, s.get(Client, a_id), "a.pdf",
                      render_invoice_pdf(electricity_invoice(normal_amount=Decimal("300"))), None)
        run_analysis(s, s.get(Client, a_id))
        s.commit()
        anomaly = s.scalar(select(Anomaly).where(Anomaly.rule_id == "line_arithmetic"))
        anomaly_id, title = anomaly.id, anomaly.title
    login(app_client, "a@klant.nl")
    assert title not in app_client.get(f"/app/clients/{a_id}").text
    assert app_client.get(f"/app/anomalies/{anomaly_id}").status_code == 404
    with session() as s:
        s.get(Anomaly, anomaly_id).review_status = ReviewStatus.CONFIRMED
        s.commit()
    assert title in app_client.get(f"/app/clients/{a_id}").text


def test_reviewer_cannot_use_admin_functions(app_client):
    make_user("rev@op.nl", Role.REVIEWER)
    client_id = make_client()
    login(app_client, "rev@op.nl")
    token = csrf(app_client, f"/app/clients/{client_id}")
    assert app_client.get(f"/app/clients/{client_id}/export.json").status_code == 403
    assert app_client.post(f"/app/clients/{client_id}/delete",
                           data={"csrf_token": token, "confirm_name": "Klant A B.V.", "reason": "x"}).status_code == 403
    assert app_client.post("/app/reference-rates", data={"csrf_token": token, "kind": "VAT", "rate": "21",
                                                         "unit": "percent", "valid_from": "2020-01-01",
                                                         "source_reference": "x"}).status_code == 403


# ---------------------------------------------------------------- GDPR


def test_export_and_erasure(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    client_id = make_client("Weg B.V.")
    login(app_client, "admin@op.nl")
    token = csrf(app_client, f"/app/clients/{client_id}")
    app_client.post(f"/app/clients/{client_id}/upload", data={"csrf_token": token},
                    files=[("files", ("f.pdf", render_invoice_pdf(electricity_invoice()), "application/pdf"))])
    export = app_client.get(f"/app/clients/{client_id}/export.json").json()
    assert export["client"]["company_name"] == "Weg B.V." and len(export["invoices"]) == 1
    assert "storage_key" not in json.dumps(export) and "password_hash" not in json.dumps(export)
    with session() as s:
        storage_key = s.scalar(select(Document.storage_key))
    from app.ingestion.storage import get_store

    path = get_store()._path(storage_key)
    assert path.exists()
    app_client.post(f"/app/clients/{client_id}/delete", data={"csrf_token": token, "confirm_name": "Verkeerd",
                                                                  "reason": "verzoek"})
    with session() as s:
        assert s.get(Client, client_id) is not None
    app_client.post(f"/app/clients/{client_id}/delete", data={"csrf_token": token, "confirm_name": "Weg B.V.",
                                                              "reason": "AVG-verzoek"})
    with session() as s:
        assert s.get(Client, client_id) is None
        assert s.scalar(select(Invoice)) is None and s.scalar(select(Document)) is None
        assert s.scalar(select(AuditLog).where(AuditLog.action == "client.erased")) is not None
    assert not path.exists()


def test_reference_rate_admin_flow(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    login(app_client, "admin@op.nl")
    token = csrf(app_client, "/app/reference-rates")
    r = app_client.post("/app/reference-rates", data={"csrf_token": token, "kind": "VAT", "rate": "21",
                                                      "unit": "percent", "valid_from": "2020-01-01",
                                                      "source_reference": ""})
    assert "bronvermelding" in r.text
    app_client.post("/app/reference-rates", data={"csrf_token": token, "kind": "VAT", "rate": "21", "unit": "percent",
                                                  "valid_from": "2020-01-01", "source_reference": "test-bron"})
    with session() as s:
        from app.models import ReferenceRate

        rate = s.scalar(select(ReferenceRate))
        assert rate.verified_at is None
    app_client.post(f"/app/reference-rates/{rate.id}/verify", data={"csrf_token": token})
    with session() as s:
        assert s.get(ReferenceRate, rate.id).verified_at is not None


def test_unicode_filename_download_and_no_state_change_on_get(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    client_id = make_client()
    login(app_client, "admin@op.nl")
    token = csrf(app_client, f"/app/clients/{client_id}")
    app_client.post(f"/app/clients/{client_id}/upload", data={"csrf_token": token},
                    files=[("files", ("фактура ë.pdf", render_invoice_pdf(electricity_invoice()), "application/pdf"))])
    with session() as s:
        doc_id = s.scalar(select(Document.id))
    r = app_client.get(f"/app/documents/{doc_id}/download")
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert app_client.get(f"/app/invoices/new?client_id={client_id}").status_code in (404, 405)
    assert app_client.post("/app/invoices/new", data={"client_id": client_id}).status_code == 403  # no CSRF


def test_upload_json_processing_flow(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    client_id = make_client()
    login(app_client, "admin@op.nl")
    token = csrf(app_client, f"/app/clients/{client_id}?tab=documenten")
    files = [("files", ("a.pdf", render_invoice_pdf(electricity_invoice(normal_amount=Decimal("300"))), "application/pdf")),
             ("files", ("b.exe", b"MZ", "application/octet-stream"))]
    r = app_client.post(f"/app/clients/{client_id}/upload", data={"csrf_token": token}, files=files,
                        headers={"Accept": "application/json"})
    data = r.json()
    assert data["can_analyse"] is True
    ok, bad = data["files"]
    assert ok["ok"] and ok["invoices"] == 1 and ok["lines"] == 3
    assert not bad["ok"] and "niet toegestaan" in bad["message"]
    r = app_client.post(f"/app/clients/{client_id}/analyse", data={"csrf_token": token},
                        headers={"Accept": "application/json"})
    res = r.json()
    assert res["findings_new"] >= 1 and res["rules"] >= 10


def test_human_readable_errors_and_empty_states(app_client):
    make_user("admin@op.nl", Role.ADMIN)
    client_id = make_client()
    login(app_client, "admin@op.nl")
    r = app_client.get("/app/clients/does-not-exist")
    assert r.status_code == 404 and "Deze pagina bestaat niet" in r.text and "Naar het overzicht" in r.text
    r = app_client.get(f"/app/clients/{client_id}")
    assert "Nog geen facturen" in r.text and "Upload de eerste energiefactuur" in r.text
    assert "Niets te beoordelen" in app_client.get("/app/review").text
    assert app_client.get("/app/designsysteem").status_code == 200


def test_static_assets_served(app_client):
    assert app_client.get("/static/app.js").status_code == 200
    r = app_client.get("/static/fonts/instrument-sans-latin-wght-normal.woff2")
    assert r.status_code == 200 and len(r.content) > 10000
