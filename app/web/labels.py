"""Dutch labels and status colours for the sales pipeline. One place, so every screen says the same thing."""

from app.domain.enums import LeadStage, OutreachKind, OutreachStatus, Qualification, ReplyCategory

# (label, chip class)
STAGES = {
    LeadStage.NEW: ("Nieuw", "neutral"),
    LeadStage.RESEARCHING: ("In onderzoek", "quiet"),
    LeadStage.QUALIFIED: ("Gekwalificeerd", "brand"),
    LeadStage.CONTACTED: ("Benaderd", "brand"),
    LeadStage.RESPONDED: ("Gereageerd", "review"),
    LeadStage.MEETING: ("Gesprek", "positive"),
    LeadStage.PILOT: ("Pilot", "positive"),
    LeadStage.CUSTOMER: ("Klant", "positive"),
    LeadStage.REJECTED: ("Afgewezen", "quiet"),
}
QUALIFICATIONS = {
    Qualification.STRONG: ("Sterk", "positive"),
    Qualification.GOOD: ("Goed", "brand"),
    Qualification.WEAK: ("Zwak", "review"),
    Qualification.UNQUALIFIED: ("Niet geschikt", "quiet"),
}
OUTREACH = {
    OutreachStatus.DRAFT: ("Concept", "quiet"),
    OutreachStatus.PENDING_APPROVAL: ("Wacht op goedkeuring", "review"),
    OutreachStatus.SCHEDULED: ("Ingepland", "brand"),
    OutreachStatus.SENT: ("Verstuurd", "positive"),
    OutreachStatus.OUTBOX: ("Niet verzonden · lokaal", "review"),
    OutreachStatus.FAILED: ("Mislukt", "critical"),
    OutreachStatus.REJECTED: ("Afgewezen", "quiet"),
}
OUTREACH_KIND = {OutreachKind.INITIAL: "Eerste e-mail", OutreachKind.FOLLOW_UP: "Opvolging",
                 OutreachKind.REPLY: "Antwoord"}
REPLIES = {
    ReplyCategory.INTERESTED: ("Geïnteresseerd", "positive"),
    ReplyCategory.MEETING_REQUEST: ("Wil een gesprek", "positive"),
    ReplyCategory.MORE_INFORMATION: ("Wil meer informatie", "brand"),
    ReplyCategory.NOT_INTERESTED: ("Geen interesse", "quiet"),
    ReplyCategory.WRONG_PERSON: ("Verkeerde persoon", "review"),
    ReplyCategory.FOLLOW_UP: ("Later opvolgen", "review"),
    ReplyCategory.UNSUBSCRIBE: ("Afgemeld", "critical"),
    ReplyCategory.OUT_OF_OFFICE: ("Afwezig", "quiet"),
    ReplyCategory.OTHER: ("Overig", "neutral"),
}
ROLE_CATEGORIES = {"ENERGY": "Energie", "CFO": "CFO", "FINANCE": "Financieel", "PROCUREMENT": "Inkoop",
                   "FACILITY": "Facilitair", "OPERATIONS": "Operationeel", "MANAGEMENT": "Directie",
                   "GENERAL": "Algemeen"}
SOURCES = {"openstreetmap": "OpenStreetMap", "mock": "Testdata", "aanvraag": "Website-aanvraag", "manual": "Handmatig",
           "website": "Eigen website", "form": "Aanvraagformulier", "test": "Testdata", "demo": "Demodata"}
SECTOR_LABELS_EXTRA = {"unknown": "Onbekend"}

# Pages that belong to a navigation item without living under its URL.
NAV_ALIASES = {
    "/app/clients": ("/app/documents", "/app/invoices", "/app/contracts"),
    "/app/analyses": ("/app/review", "/app/anomalies", "/app/admin"),
    "/app/settings": ("/app/users", "/app/audit", "/app/reference-rates", "/app/designsysteem"),
}

TEMPLATE_GLOBALS = dict(STAGES=STAGES, QUALIFICATIONS=QUALIFICATIONS, OUTREACH=OUTREACH, OUTREACH_KIND=OUTREACH_KIND,
                        REPLIES=REPLIES, ROLE_CATEGORIES=ROLE_CATEGORIES, SOURCES=SOURCES, NAV_ALIASES=NAV_ALIASES)
