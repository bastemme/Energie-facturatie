# Factuurspoor bekijken op je eigen computer

1. Download het project: groene knop **Code** → **Download ZIP** op GitHub, en pak het ZIP-bestand uit.
2. Open de uitgepakte map en dubbelklik:
   - **Windows:** `Start Factuurspoor (Windows).bat`
   - **Mac:** `Start Factuurspoor (Mac).command` (de eerste keer: rechtsklik → Open → Open)
3. De eerste keer duurt het een paar minuten. Daarna opent de browser vanzelf op <http://127.0.0.1:8000>.
4. Inloggen via **Inloggen** rechtsboven:
   - E-mailadres: `demo@factuurspoor.nl`
   - Wachtwoord: `demo-wachtwoord-2026`

Stoppen: sluit het zwarte venster. De voorbeeldgegevens zijn fictief (synthetisch).

## Je eerste leadgeneratie

1. Klik links op **Leads** → **Leads zoeken**.
2. Laat staan: 25 bedrijven, Nederland, *Energie-intensieve B2B*. Klik **Leads zoeken**.
3. Kijk mee in **AI Operations**: de Lead Researcher zoekt echte bedrijven (OpenStreetMap + hun eigen website),
   de Lead Qualifier beoordeelt ze, de Contact Researcher zoekt beslissers op hun website en de Outreach-agent
   schrijft concept-e-mails. Dit vraagt internet en duurt een paar minuten.
4. Ga naar **Outreach → Wacht op goedkeuring**. Lees elke e-mail, pas hem eventueel aan (**Bewerken**),
   en kies **Goedkeuren en versturen** of **Afwijzen**. Er gaat nooit iets weg zonder jouw klik.
5. Standaard staat e-mail op **MOCK**: goedgekeurde e-mails worden niet echt verstuurd. Met **Antwoord simuleren**
   (op een verstuurde e-mail) test je hoe een reactie wordt herkend en de lead wordt bijgewerkt.
6. Echt versturen: zet in een bestand `.env` naast deze map `ER_EMAIL_PROVIDER=smtp` en de SMTP/IMAP-gegevens
   van je mailbox (zie `.env.example`), en start opnieuw. Controleer bij **Instellingen** of alles op *Live* staat.
