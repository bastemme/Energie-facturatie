# READY → Excel: fase 1 (omgevingsinspectie)

Doel van het project: vanuit één Nederlandstalig venster een klantnummer en periode invoeren, READY de juiste
meter laten exporteren met *Export klant simpel*, de nieuwe TXT in Downloads oppikken en een gecontroleerd
XLSX-bestand opleveren waarin EAN-codes exact als tekst staan.

## Wat in fase 1 is vastgesteld

De ontwikkelomgeving waarin deze map is gemaakt is een Linux-cloudcontainer. Daarin is het volgende **niet**
aanwezig, dus ook niet geïnspecteerd:

| Onderdeel | Status |
|---|---|
| Windows | Niet beschikbaar (Linux-container) |
| READY (programma, venster, proces, installatiepad) | Niet aanwezig |
| UI Automation-elementen van READY | Niet inspecteerbaar |
| Voorbeeld-TXT van *Export klant simpel* | Niet aangeleverd |
| Screenshots van READY | Niet meegestuurd in deze sessie |
| Windows-map Downloads | Niet aanwezig |
| Python | 3.13 (container); op de READY-pc nog onbekend |

Er zijn daarom **nog geen selectors, venstertitels, exe-paden of kolomindelingen** in de code opgenomen.
Die moeten van de echte READY-pc komen. Deze map bevat de tools om dat veilig te doen.

## De inspectietools

Alle tools zijn **alleen-lezen**: ze klikken niet, typen niet, activeren geen venster en starten geen export.
Klantnummers, EAN-codes, namen, adressen en meetwaarden worden standaard gemaskeerd (`privacy.py`).

| Bestand | Wat het doet |
|---|---|
| `inspecteer_ready.py omgeving` | Windows-versie, Python, pakketten, DPI/schermen, echte Downloads-map (Known Folder API), aantallen bestanden per extensie en tijdelijke downloadbestanden |
| `inspecteer_ready.py vensters` | Alle zichtbare vensters met PID, exe en vensterklasse, om READY te herkennen |
| `inspecteer_ready.py boom --pid N` | De volledige UI Automation-boom (UIA én win32) van alle vensters van READY: control types, AutomationId's, klassen, UI-framework en ondersteunde patronen (Invoke, Value, SelectionItem, ExpandCollapse, Grid…) |
| `analyseer_txt.py bestand.txt` | Encoding, regeleinden, scheidingsteken, header, metadata-regels en per kolom de *vorm* van de waarden (cijferlengte, voorloopnullen, decimaalteken), zonder de waarden zelf |

Alleen `boom` heeft een extra pakket nodig (`pywinauto`); de rest gebruikt de standaardbibliotheek.

## Stappen op de READY-pc

1. Installeer Python 3.11 of nieuwer van python.org (vink *Add python.exe to PATH* aan).
2. Kopieer deze map `ready-export` naar de pc en dubbelklik **`Start inspectie (Windows).bat`**.
   Dit maakt een lokale `.venv`, installeert `pywinauto` en draait `omgeving` en `vensters`.
3. Open READY en log in zoals gewoonlijk. Zoek in de vensterlijst de PID van READY.
4. Leg drie schermtoestanden vast. Doe de bediening in READY **zelf**, het script kijkt alleen:

   ```bat
   rem a. Hoofdscherm met de sectie Zoeken zichtbaar
   python inspecteer_ready.py boom --pid <PID> --label hoofdscherm
   rem b. Na handmatig zoeken op een (test)klantnummer, met de meterlijst gevuld
   python inspecteer_ready.py boom --pid <PID> --label na-zoeken
   rem c. Met het exportvenster open (NIET op Exporteren klikken, wel mag u het open laten)
   python inspecteer_ready.py boom --pid <PID> --label exportvenster
   ```

   Open in stap c ook even de dropdown *Formaat* en de datumvelden, en draai het daarna nog eens met
   `--label exportvenster-dropdown-open`: sommige dropdowns tonen hun opties pas in de UI-boom als ze open zijn.
5. Analyseer een bestaande TXT-export (bij voorkeur van een testklant):

   ```bat
   python analyseer_txt.py "%USERPROFILE%\Downloads\<bestand>.txt" --uitvoer inspectie-uitvoer\txt-structuur.md
   ```

6. **Lees de bestanden in `inspectie-uitvoer` door** voordat u ze deelt. Het maskeren werkt op basis van het
   type element; een klantnaam in een los tekstlabel buiten een lijst kan er nog doorheen komen.
7. Stuur de inhoud van `inspectie-uitvoer` terug, plus de screenshots van zoekscherm, meterlijst en exportvenster.

## Wat de uitkomst bepaalt

- **UIA-boom bevat de zoekvelden, de meterlijst en de exportknoppen met naam of AutomationId** → automatisering
  volledig via UI Automation; geen coördinaten.
- **UIA-boom is vrijwel leeg (alleen Window/Pane), win32 wel bruikbaar** → win32-backend.
- **Beide leeg of onbruikbaar** (bv. een eigen getekende interface) → gecontroleerde toetsenbord/muis-fallback,
  vastgelegd ten opzichte van de vensterpositie, met een controle na elke stap.
- **Meterlijst toont geen EAN** → uitzoeken waar READY de EAN wel toont (detailscherm, extra kolom of alleen in
  de TXT). De applicatie verzint de EAN niet.
- **TXT-structuur** bepaalt de parser: encoding, scheidingsteken, header, metadata-regels en welke kolommen als
  tekst moeten worden opgeslagen (alle cijferreeksen met voorloopnul of langer dan 15 cijfers).

## Volgende fasen (na ontvangst van de inspectie)

2. Proof of concept die READY herkent en de zoek-/exportelementen terugvindt, nog zonder export.
3. Klant zoeken en meter selecteren. 4. Exportinstellingen met bevestiging. 5. Downloaddetectie en conversie.
6. Validatie (EAN-vergelijking na herinlezen van de XLSX). 7. Tests en installatiehandleiding.

## Tests (ontwikkelaar)

```bash
python -m pytest ready-export/tests
```
