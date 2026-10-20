"""Structuuranalyse van een READY-TXT-export, zonder de inhoud prijs te geven.

Rapporteert encoding, regeleinden, scheidingsteken, header, metadata-regels en per kolom
de vorm van de waarden (cijfers -> 9, letters -> A). Geen klantnummers, EAN-codes,
adressen of meetwaarden komen in het rapport; wel de lengte van cijferreeksen,
zodat zichtbaar is of een kolom EAN-codes (18 cijfers) of voorloopnullen bevat.

Gebruik:
    python analyseer_txt.py "pad\\naar\\export.txt"
Alleen Python-standaardbibliotheek; werkt ook zonder pywinauto.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

from privacy import maskeer_cijferreeksen, vorm

KANDIDAAT_SCHEIDINGSTEKENS = {"\t": "tab", ";": "puntkomma", ",": "komma", "|": "pipe"}

BOMS = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]


def bepaal_encoding(data: bytes) -> tuple[str, list[str]]:
    """Kies de encoding op basis van BOM of strikte decodering. Geeft (encoding, opmerkingen)."""
    opmerkingen = []
    for bom, naam in BOMS:
        if data.startswith(bom):
            return naam, [f"BOM gevonden: {naam}"]
    if b"\x00" in data[:4096]:
        opmerkingen.append("Nul-bytes zonder BOM: mogelijk UTF-16 zonder BOM. Handmatig controleren.")
    if all(b < 0x80 for b in data):
        return "ascii", opmerkingen + ["Alleen ASCII-tekens; utf-8 en cp1252 geven hetzelfde resultaat."]
    try:
        data.decode("utf-8")
        return "utf-8", opmerkingen + ["Geldige UTF-8 met niet-ASCII-tekens."]
    except UnicodeDecodeError:
        pass
    try:
        data.decode("cp1252")
        return "cp1252", opmerkingen + ["Geen geldige UTF-8; decodeert als Windows-1252."]
    except UnicodeDecodeError:
        return "latin-1", opmerkingen + ["Noch UTF-8 noch cp1252; latin-1 als noodoplossing. Controleren!"]


def kies_scheidingsteken(regels: list[str]) -> tuple[str | None, dict]:
    """Het teken dat op de meeste regels even vaak voorkomt (en minstens één keer)."""
    statistiek = {}
    for teken in KANDIDAAT_SCHEIDINGSTEKENS:
        aantallen = Counter(r.count(teken) for r in regels)
        modus, freq = aantallen.most_common(1)[0]
        statistiek[teken] = {"modus": modus, "aandeel": freq / len(regels)}
    geschikt = {t: s for t, s in statistiek.items() if s["modus"] > 0}
    if not geschikt:
        return None, statistiek
    beste = max(geschikt, key=lambda t: (geschikt[t]["aandeel"], geschikt[t]["modus"]))
    return beste, statistiek


def profiel(waarden: list[str]) -> dict:
    gevuld = [w for w in waarden if w.strip()]
    vormen = Counter(vorm(w) for w in gevuld)
    alleen_cijfers = [w.strip() for w in gevuld if re.fullmatch(r"\s*\d+\s*", w)]
    p = {
        "aantal": len(waarden),
        "leeg": len(waarden) - len(gevuld),
        "max_lengte": max((len(w) for w in waarden), default=0),
        "vormen": vormen.most_common(4),
        "aantal_vormen": len(vormen),
        "spaties_rond_waarde": sum(1 for w in gevuld if w != w.strip()),
    }
    if alleen_cijfers:
        p["alleen_cijfers"] = len(alleen_cijfers)
        p["cijferlengtes"] = dict(Counter(len(w) for w in alleen_cijfers).most_common(5))
        p["voorloopnul"] = sum(1 for w in alleen_cijfers if len(w) > 1 and w.startswith("0"))
        p["langer_dan_15"] = sum(1 for w in alleen_cijfers if len(w) > 15)
    p["decimaal_komma"] = sum(1 for w in gevuld if re.fullmatch(r"-?\d+,\d+", w.strip()))
    p["decimaal_punt"] = sum(1 for w in gevuld if re.fullmatch(r"-?\d+\.\d+", w.strip()))
    p["duizendtal_scheiding"] = sum(1 for w in gevuld if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+([.,]\d+)?", w.strip()))
    return p


def lijkt_header(velden: list[str]) -> bool:
    gevuld = [v for v in velden if v.strip()]
    return bool(gevuld) and sum(1 for v in gevuld if re.search(r"[^\W\d_]", v) and not re.search(r"\d{4,}", v)) \
        >= 0.8 * len(gevuld)


def analyseer(pad: Path) -> str:
    data = pad.read_bytes()
    r = [f"# Structuuranalyse: {maskeer_cijferreeksen(pad.name)}", "", f"- Bestandsgrootte: {len(data)} bytes"]
    if not data:
        return "\n".join(r + ["- **Bestand is leeg.**"]) + "\n"

    encoding, opm = bepaal_encoding(data)
    tekst = data.decode(encoding, errors="replace")
    r.append(f"- Encoding: **{encoding}**")
    r += [f"  - {o}" for o in opm]
    r.append(f"- Regeleinden: CRLF={tekst.count(chr(13) + chr(10))}, "
             f"LF-alleen={tekst.count(chr(10)) - tekst.count(chr(13) + chr(10))}, "
             f"CR-alleen={tekst.count(chr(13)) - tekst.count(chr(13) + chr(10))}")
    r.append(f"- Eindigt met regeleinde: {tekst.endswith(chr(10)) or tekst.endswith(chr(13))}")

    alle = tekst.splitlines()
    niet_leeg = [(i + 1, s) for i, s in enumerate(alle) if s.strip()]
    r.append(f"- Regels: {len(alle)} totaal, {len(alle) - len(niet_leeg)} leeg")
    lege_posities = [i + 1 for i, s in enumerate(alle) if not s.strip()]
    if lege_posities:
        r.append(f"  - Lege regels op regelnummer (max 20): {lege_posities[:20]}")
    if not niet_leeg:
        return "\n".join(r + ["- **Geen gegevens.**"]) + "\n"

    teken, stat = kies_scheidingsteken([s for _, s in niet_leeg])
    r += ["", "## Scheidingsteken", ""]
    for t, s in stat.items():
        r.append(f"- {KANDIDAAT_SCHEIDINGSTEKENS[t]}: meest voorkomend aantal per regel {s['modus']} "
                 f"op {s['aandeel']:.0%} van de regels")
    if teken is None:
        r.append("- **Geen scheidingsteken gevonden.** Mogelijk vaste kolombreedte; zie vormen hieronder.")
        r += ["", "## Vorm van de eerste 15 regels", ""]
        r += [f"- regel {nr}: `{vorm(s)}` (lengte {len(s)})" for nr, s in niet_leeg[:15]]
        return "\n".join(r) + "\n"
    r.append(f"- Gekozen: **{KANDIDAAT_SCHEIDINGSTEKENS[teken]}**")
    r.append(f"- Aanhalingstekens in bestand: {chr(34) in tekst}")

    rijen = [(nr, next(csv.reader(io.StringIO(s), delimiter=teken))) for nr, s in niet_leeg]
    kolomaantallen = Counter(len(v) for _, v in rijen)
    standaard = kolomaantallen.most_common(1)[0][0]
    r += ["", "## Regelopbouw", "", f"- Velden per regel: {dict(kolomaantallen.most_common())}"]
    afwijkend = [(nr, v) for nr, v in rijen if len(v) != standaard]
    if afwijkend:
        r.append(f"- {len(afwijkend)} regels wijken af van {standaard} velden (metadata of fout?). Eerste 15:")
        r += [f"  - regel {nr}: {len(v)} velden, vorm `{vorm(teken.join(v))[:120]}`" for nr, v in afwijkend[:15]]

    data_rijen = [(nr, v) for nr, v in rijen if len(v) == standaard]
    header_nr, header = None, None
    if lijkt_header(data_rijen[0][1]):
        header_nr, header = data_rijen[0]
        data_rijen = data_rijen[1:]
    r.append(f"- Header: {'regel ' + str(header_nr) if header else 'niet herkend'}")
    r.append(f"- Gegevensregels met {standaard} velden: {len(data_rijen)}")

    r += ["", "## Kolommen", ""]
    for k in range(standaard):
        naam = maskeer_cijferreeksen(header[k]) if header else f"kolom {k + 1}"
        p = profiel([v[k] for _, v in data_rijen])
        r.append(f"### {k + 1}. {naam!r}")
        r.append(f"- gevuld {p['aantal'] - p['leeg']}/{p['aantal']}, max lengte {p['max_lengte']}, "
                 f"{p['aantal_vormen']} verschillende vormen")
        r.append(f"- meest voorkomende vormen: {', '.join(f'`{v}` ({n}x)' for v, n in p['vormen'])}")
        if "alleen_cijfers" in p:
            r.append(f"- alleen cijfers: {p['alleen_cijfers']}x, lengtes {p['cijferlengtes']}, "
                     f"met voorloopnul {p['voorloopnul']}x, langer dan 15 cijfers {p['langer_dan_15']}x"
                     + (" → **als tekst opslaan**" if p["voorloopnul"] or p["langer_dan_15"] else ""))
        if p["decimaal_komma"] or p["decimaal_punt"] or p["duizendtal_scheiding"]:
            r.append(f"- decimalen: komma {p['decimaal_komma']}x, punt {p['decimaal_punt']}x, "
                     f"mogelijk duizendtalscheiding {p['duizendtal_scheiding']}x")
        if p["spaties_rond_waarde"]:
            r.append(f"- waarden met spaties ervoor/erna: {p['spaties_rond_waarde']}x")
        r.append("")
    return "\n".join(r) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Structuuranalyse van een READY-TXT zonder inhoud te tonen.")
    parser.add_argument("bestand", type=Path)
    parser.add_argument("--uitvoer", type=Path, help="Rapport ook opslaan als Markdown-bestand")
    args = parser.parse_args(argv)
    if not args.bestand.is_file():
        sys.exit(f"Bestand niet gevonden: {args.bestand}")
    rapport = analyseer(args.bestand)
    sys.stdout.reconfigure(encoding="utf-8")
    print(rapport)
    if args.uitvoer:
        args.uitvoer.write_text(rapport, encoding="utf-8")
        print(f"Opgeslagen: {args.uitvoer}")


if __name__ == "__main__":
    main()
