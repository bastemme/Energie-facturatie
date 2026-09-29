"""Operational commands.

    python -m app.cli gen-key                      # new Fernet key for ER_STORAGE_ENCRYPTION_KEY
    python -m app.cli create-admin EMAIL           # prompts for password
    python -m app.cli retention                    # erase clients whose retention period expired
    python -m app.cli seed-demo                    # SYNTHETIC demo client (clearly marked) for local testing
"""

from __future__ import annotations

import argparse
import calendar
import getpass
import sys
from datetime import date
from decimal import Decimal

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.db import create_all, init_engine, session_scope
from app.domain.enums import Role


def cmd_gen_key(_args) -> None:
    print(Fernet.generate_key().decode())


def cmd_create_admin(args) -> None:
    from app.models import User
    from app.web.security import hash_password

    password = args.password or getpass.getpass("Wachtwoord (min. 12 tekens): ")
    init_engine()
    create_all()
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == args.email.lower())):
            sys.exit("Gebruiker bestaat al.")
        db.add(User(email=args.email.lower(), full_name=args.name, password_hash=hash_password(password),
                    role=Role.ADMIN))
    print(f"Beheerder {args.email} aangemaakt.")


def cmd_retention(_args) -> None:
    from app.services.privacy import run_retention

    init_engine()
    create_all()
    with session_scope() as db:
        erased = run_retention(db)
    print(f"{len(erased)} klant(en) verwijderd na afloop bewaartermijn.")


def cmd_seed_demo(_args) -> None:
    """Creates a clearly-labelled SYNTHETIC client with invoices containing known errors."""
    from app.devtools.synthetic import SynInvoice, SynLine, invoices_to_csv, make_ean, render_invoice_pdf
    from app.domain.enums import Commodity, LineCategory
    from app.ingestion.pipeline import DuplicateDocument, ingest_upload
    from app.models import Client, Contract, ContractPrice
    from app.services.analysis import run_analysis

    init_engine()
    create_all()
    D = Decimal
    ean = make_ean(42)
    with session_scope() as db:
        name = "[DEMO — synthetische data] Voorbeeld Logistiek B.V."
        client = db.scalar(select(Client).where(Client.company_name == name))
        if client is None:
            client = Client(company_name=name, success_fee_percentage=D("15"), consent_given=True,
                            consent_text_version="demo", contact_name="Demo", retention_days=30)
            db.add(client)
            db.flush()
            contract = Contract(client_id=client.id, supplier="Voorbeeld Energie B.V.", start_date=date(2025, 1, 1),
                                commodity=Commodity.ELECTRICITY, contract_reference="DEMO-CONTRACT")
            contract.prices += [
                ContractPrice(category=LineCategory.ELECTRICITY_NORMAL, unit="kWh", price=D("0.20"),
                              valid_from=date(2025, 1, 1), source_text="DEMO"),
                ContractPrice(category=LineCategory.ELECTRICITY_LOW, unit="kWh", price=D("0.15"),
                              valid_from=date(2025, 1, 1), source_text="DEMO"),
                ContractPrice(category=LineCategory.FIXED_SUPPLY_FEE, unit="month", price=D("7.50"),
                              valid_from=date(2025, 1, 1), source_text="DEMO"),
            ]
            db.add(contract)

        def inv(n, month, normal_price="0.20", fixed_qty="1", readings=None):
            start = date(2026, month, 1)
            end = date(2026, month, calendar.monthrange(2026, month)[1])
            return SynInvoice(f"DEMO-{n}", end, start, end, ean, [
                SynLine("Levering elektriciteit normaaltarief", D("1000"), "kWh", D(normal_price)),
                SynLine("Levering elektriciteit daltarief", D("600"), "kWh", D("0.15")),
                SynLine("Vaste leveringskosten", D(fixed_qty), "maand", D("7.50")),
            ], supplier="Voorbeeld Energie B.V.", readings=readings or [])

        pdfs = [inv(1, 1), inv(2, 2, normal_price="0.25"), inv(3, 3, fixed_qty="2"),
                inv(4, 4, readings=[("begin", "normaal", date(2026, 4, 1), D("5000"), "werkelijk"),
                                    ("eind", "normaal", date(2026, 4, 30), D("5800"), "geschat")])]
        for syn in pdfs:
            try:
                ingest_upload(db, client, f"{syn.invoice_number}.pdf", render_invoice_pdf(syn), None)
            except DuplicateDocument:
                pass
        try:
            ingest_upload(db, client, "demo-export.csv", invoices_to_csv([inv(5, 5), inv(6, 6)]), None)
        except DuplicateDocument:
            pass
        run = run_analysis(db, client)
        print(f"Demo-klant: {client.id} — {run.findings_total} bevindingen (SYNTHETISCHE data).")


DEMO_EMAIL = "demo@factuurspoor.nl"
DEMO_PASSWORD = "demo-wachtwoord-2026"  # noqa: S105 - local demo only; refused in production


def cmd_demo(args) -> None:
    """One command for beginners: demo data + demo login + start the app + open the browser."""
    import threading
    import webbrowser

    import uvicorn

    from app.config import get_settings
    from app.models import User
    from app.web.security import hash_password

    if get_settings().is_production:
        sys.exit("De demo-modus is niet beschikbaar in productie.")
    init_engine()
    create_all()
    with session_scope() as db:
        if not db.scalar(select(User).where(User.email == DEMO_EMAIL)):
            db.add(User(email=DEMO_EMAIL, full_name="Demo gebruiker", password_hash=hash_password(DEMO_PASSWORD),
                        role=Role.ADMIN))
    cmd_seed_demo(args)
    url = f"http://127.0.0.1:{args.port}"
    print("\n" + "=" * 60)
    print(f"  Factuurspoor draait op:  {url}")
    print(f"  Inloggen op:             {url}/login")
    print(f"  E-mailadres:             {DEMO_EMAIL}")
    print(f"  Wachtwoord:              {DEMO_PASSWORD}")
    print("  Stoppen: sluit dit venster (of druk op Ctrl+C).")
    print("=" * 60 + "\n")
    if not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    from app.web.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-key").set_defaults(func=cmd_gen_key)
    p = sub.add_parser("create-admin")
    p.add_argument("email")
    p.add_argument("--name")
    p.add_argument("--password", help="non-interactive (avoid in shell history)")
    p.set_defaults(func=cmd_create_admin)
    sub.add_parser("retention").set_defaults(func=cmd_retention)
    sub.add_parser("seed-demo").set_defaults(func=cmd_seed_demo)
    d = sub.add_parser("demo", help="demo data + demo login + start + open browser")
    d.add_argument("--port", type=int, default=8000)
    d.add_argument("--no-browser", action="store_true")
    d.set_defaults(func=cmd_demo)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
