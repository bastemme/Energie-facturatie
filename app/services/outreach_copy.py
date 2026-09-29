"""Dutch B2B e-mail copy for outreach, follow-ups and reply drafts.

Templates filled only with stored facts (company, industry, city, locations from the website, the person's
role). Each fact used is returned in `personalization` with its source, so the reviewer sees why the e-mail
says what it says. No savings are promised and nothing is claimed about the company's own energy use.
Kept short: the body (without signature and opt-out line) stays under ~150 words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.domain.enums import ReplyCategory

PROPOSITION = ("Wij controleren zakelijke energiefacturen op mogelijke afwijkingen in verbruik, tarieven en "
               "facturatie, en dienen correcties in bij de leverancier. Als we niets terugvinden, betaalt u niets.")
SUBJECT = "Controle van de energiefacturen van {company}"
UNSUBSCRIBE = 'Liever geen bericht meer van ons? Antwoord met "afmelden", dan benaderen we u niet opnieuw.'
MAX_WORDS = 150

# Why this industry, phrased as a general industry trait (never as a fact about the company).
SECTOR_WHY = {
    "manufacturing": "In de maakindustrie zijn energiefacturen groot en ingewikkeld, met capaciteits- en "
                     "transporttarieven naast levering.",
    "food": "In de voedselproductie wordt dag en nacht gekoeld en verwarmd; de facturen bevatten veel tariefregels.",
    "chemicals": "In de chemie en kunststofverwerking zijn energiefacturen groot en bevatten ze veel tariefregels.",
    "paper": "Bij papier- en verpakkingsbedrijven is energie doorgaans een grote kostenpost met complexe facturen.",
    "cold_storage": "Koel- en vrieshuizen verbruiken continu stroom; elke tariefregel op de factuur weegt zwaar.",
    "logistics": "Logistieke bedrijven hebben vaak meerdere hallen en aansluitingen, en dus veel facturen.",
    "swimming": "Zwembaden verbruiken veel gas en stroom voor water, lucht en pompen.",
    "supermarkets": "Supermarkten koelen dag en nacht, waardoor elke tariefregel op de factuur zwaar weegt.",
    "bakeries": "Bakkerijen gebruiken gas én stroom voor ovens en koeling, vaak met meerdere tarieven.",
    "hotels": "Hotels verbruiken het hele jaar energie, vaak over meerdere aansluitingen.",
    "laundries": "Wasserijen verbruiken veel gas en stroom; kleine tariefafwijkingen tellen snel op.",
    "care": "Zorginstellingen verbruiken 24 uur per dag energie, vaak verspreid over meerdere gebouwen.",
    "fitness": "Sportscholen hebben lange openingstijden met veel verlichting, ventilatie en douches.",
    "restaurants": "Restaurants hebben een hoog verbruik in keuken, koeling en afzuiging.",
}
SECTOR_NOUN = {"manufacturing": "productiebedrijf", "food": "voedselproducent", "chemicals": "bedrijf in de chemie",
               "paper": "papier- of verpakkingsbedrijf", "cold_storage": "koel- en vrieshuis",
               "logistics": "logistiek bedrijf", "swimming": "zwembad", "supermarkets": "supermarkt",
               "bakeries": "bakkerij", "hotels": "hotel", "laundries": "wasserij", "care": "zorginstelling",
               "fitness": "sportschool", "restaurants": "restaurant"}
GENERIC_WHY = "Zakelijke energiefacturen combineren levering, netbeheer en belastingen; dat maakt ze foutgevoelig."
WHAT_GOES_WRONG = ("Daarin gaat geregeld iets mis: een tarief dat afwijkt van het contract, dubbel berekende vaste "
                   "kosten of verbruik op basis van een geschatte meterstand.")


@dataclass
class Draft:
    subject: str
    body: str
    personalization: list[dict]


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def core_text(body: str) -> str:
    """The part of a body that counts towards MAX_WORDS: without signature and opt-out line."""
    return body.split("\nMet vriendelijke groet")[0]


def _signature() -> str:
    s = get_settings()
    return f"Met vriendelijke groet,\n\n{s.outreach_from_name}\n{s.operator_name}"


def _greeting(contact_name: str | None) -> str:
    return f"Beste {contact_name}," if contact_name else "Goedendag,"


def initial_email(*, company: str, sector: str | None, sector_label: str | None, city: str | None,
                  multi_location_text: str | None, contact_name: str | None, contact_role: str | None,
                  generic_mailbox: bool, source_label: str | None = None, source_url: str | None = None,
                  website: str | None = None) -> Draft:
    facts: list[dict] = []
    if sector_label:
        where = f" in {city}" if city else ""
        noun = SECTOR_NOUN.get(sector or "", f"bedrijf in de branche {sector_label.lower()}")
        opening = f"Ik kwam {company} tegen als {noun}{where}."
        facts.append({"fact": f"{company}: {sector_label.lower()}{where}", "source": source_url or source_label})
    else:
        opening = f"Ik neem contact met u op namens {get_settings().operator_name}, over de energiefacturen van " \
                  f"{company}."
    if multi_location_text:
        opening += f" Op uw website noemt u {multi_location_text}; dat betekent meestal meerdere aansluitingen en " \
                   "facturen."
        facts.append({"fact": f"Website noemt '{multi_location_text}'", "source": website})
    why = SECTOR_WHY.get(sector or "", GENERIC_WHY)
    if generic_mailbox:
        cta = "Wilt u dit bericht doorsturen naar degene die over de energiekosten gaat?"
        facts.append({"fact": "Algemeen e-mailadres van het bedrijf (GENERAL_COMPANY_EMAIL)", "source": website})
    else:
        cta = "Zullen we volgende week een kwartier bellen? Dan vertel ik welke facturen we nodig hebben."
        if contact_role:
            facts.append({"fact": f"{contact_name}: {contact_role}", "source": "Contact Researcher (zie contact)"})
    body = "\n".join([_greeting(contact_name), "", opening, "", f"{why} {WHAT_GOES_WRONG}", "", PROPOSITION, "",
                      cta, "", _signature(), "", UNSUBSCRIBE])
    return Draft(SUBJECT.format(company=company)[:300], body, facts)


def follow_up_email(*, company: str, contact_name: str | None, original_subject: str) -> Draft:
    body = "\n".join([
        _greeting(contact_name), "",
        f"Vorige week stuurde ik u een bericht over de energiefacturen van {company}. Kort samengevat:", "",
        PROPOSITION, "",
        "Ligt dit bij een collega, dan hoor ik graag bij wie. Past het nu niet, laat het me weten; dan stoppen "
        "we hier.",
        "", _signature(), "", UNSUBSCRIBE])
    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
    return Draft(subject[:300], body, [{"fact": "Geen reactie op het eerste bericht", "source": "Outreach"}])


REPLY_TEMPLATES: dict[ReplyCategory, str] = {
    ReplyCategory.INTERESTED: (
        "Dank voor uw reactie. Het eenvoudigste is een gesprek van een kwartier, waarin ik laat zien hoe een "
        "controle werkt en welke facturen we nodig hebben. Past [dag en tijd] of [dag en tijd]?"),
    ReplyCategory.MEETING_REQUEST: (
        "Dank u, graag. Ik stuur u een uitnodiging voor [dag en tijd]. Het helpt als u de energiefacturen van de "
        "afgelopen 12 maanden bij de hand heeft, maar nodig is het niet."),
    ReplyCategory.MORE_INFORMATION: (
        "Dank voor uw vraag. In het kort: u levert de energiefacturen (en zo mogelijk het contract) aan, wij "
        "controleren elke regel op tarieven, verbruik, vaste kosten en belastingen en onderbouwen elke mogelijke "
        "fout met de plek op de factuur. Na uw akkoord dienen wij de correctie in bij de leverancier. U betaalt "
        "alleen een percentage van wat daadwerkelijk wordt terugbetaald; vinden we niets, dan betaalt u niets.\n\n"
        "Zal ik dit in een kort gesprek laten zien?"),
    ReplyCategory.WRONG_PERSON: (
        "Dank voor het laten weten. Weet u wie binnen uw organisatie over de energiefacturen gaat? Dan benader ik "
        "die persoon rechtstreeks."),
}  # no draft for UNSUBSCRIBE (we do not write to someone who asked us to stop) or NOT_INTERESTED


def reply_draft(category: ReplyCategory, *, contact_name: str | None, subject: str) -> Draft | None:
    text = REPLY_TEMPLATES.get(category)
    if text is None:
        return None
    body = "\n".join([_greeting(contact_name), "", text, "", _signature()])
    subj = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    from app.domain.replies import LABELS_NL

    return Draft(subj[:300], body, [{"fact": f"Reactie: {LABELS_NL[category]}", "source": "Email-agent"}])
