"""Fase 1/2: read-only inspectie van de Windows-omgeving en het READY-venster.

Dit script klikt niet, typt niet, activeert geen venster en start geen export.
Het leest alleen wat Windows UI Automation en de Windows-API rapporteren.

Gebruik (op de Windows-pc waar READY draait):
    python inspecteer_ready.py omgeving
    python inspecteer_ready.py vensters [--filter tekst]
    python inspecteer_ready.py boom --pid 1234 [--backend uia|win32|beide] [--label hoofdscherm]

De uitvoer komt in de map 'inspectie-uitvoer' naast dit script. Klantgegevens worden
standaard gemaskeerd (zie privacy.py). Controleer de bestanden voordat u ze deelt.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
import time
from collections import Counter
from ctypes import wintypes
from datetime import datetime
from importlib import metadata
from pathlib import Path

from privacy import DATA_CONTAINERS, maskeer_cijferreeksen, veilige_naam

UITVOER = Path(__file__).resolve().parent / "inspectie-uitvoer"

# Patronen die bepalen hoe een element te bedienen is zonder muiscoördinaten.
UIA_PATRONEN = [
    "Invoke", "Value", "Toggle", "Selection", "SelectionItem", "ExpandCollapse",
    "Grid", "GridItem", "Table", "TableItem", "Scroll", "RangeValue", "Window", "LegacyIAccessible",
]

TIJDELIJKE_DOWNLOADS = {".crdownload", ".part", ".partial", ".tmp", ".download"}


def vereis_windows() -> None:
    if sys.platform != "win32":
        sys.exit("Dit script moet op Windows draaien, op de computer waar READY geïnstalleerd is.")


def schrijf(naam: str, inhoud: str) -> Path:
    UITVOER.mkdir(exist_ok=True)
    pad = UITVOER / naam
    pad.write_text(inhoud, encoding="utf-8")
    return pad


# --------------------------------------------------------------------------- omgeving

def downloads_map() -> Path:
    """De echte Downloads-map van de huidige gebruiker (ook als die is verplaatst), via de Known Folder API."""

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
    folderid = GUID(0x374DE290, 0x123F, 0x4565, (wintypes.BYTE * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    pad_ptr = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folderid), 0, None, ctypes.byref(pad_ptr))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath gaf foutcode {hr:#x}")
    try:
        return Path(pad_ptr.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pad_ptr)


def pakketversie(naam: str) -> str:
    try:
        return metadata.version(naam)
    except metadata.PackageNotFoundError:
        return "niet geïnstalleerd"


def cmd_omgeving(_args: argparse.Namespace) -> None:
    vereis_windows()
    regels = ["# Omgeving", ""]
    regels.append(f"- Windows: {platform.platform()} ({platform.machine()})")
    regels.append(f"- Python: {sys.version.split()[0]} ({'64' if sys.maxsize > 2**32 else '32'}-bit)")
    for pakket in ["pywinauto", "pywin32", "comtypes", "openpyxl", "PySide6", "pyautogui"]:
        regels.append(f"- {pakket}: {pakketversie(pakket)}")

    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForSystem()
        regels.append(f"- Systeem-DPI: {dpi} (schaal {dpi / 96:.0%})")
    except AttributeError:
        regels.append("- Systeem-DPI: onbekend (oudere Windows-versie)")
    regels.append(f"- Primair scherm: {user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)}")
    regels.append(f"- Aantal schermen: {user32.GetSystemMetrics(80)}")

    regels += ["", "## Downloads", ""]
    try:
        dl = downloads_map()
        regels.append(f"- Map gevonden via Known Folder API; standaardlocatie: {dl == Path.home() / 'Downloads'}")
        regels.append(f"- Bestaat: {dl.is_dir()}")
        if dl.is_dir():
            bestanden = [p for p in dl.iterdir() if p.is_file()]
            extensies = Counter(p.suffix.lower() or "(geen)" for p in bestanden)
            regels.append(f"- Aantal bestanden: {len(bestanden)}")
            regels.append(f"- Per extensie: {dict(extensies.most_common(15))}")
            tijdelijk = sum(n for ext, n in extensies.items() if ext in TIJDELIJKE_DOWNLOADS)
            regels.append(f"- Tijdelijke downloadbestanden aanwezig: {tijdelijk}")
            txts = sorted((p for p in bestanden if p.suffix.lower() == ".txt"), key=lambda p: p.stat().st_mtime)
            regels.append("- Laatste 5 TXT-bestanden (naam gemaskeerd, grootte, gewijzigd):")
            for p in txts[-5:]:
                st = p.stat()
                regels.append(f"  - {maskeer_cijferreeksen(p.name)} | {st.st_size} bytes | "
                              f"{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
    except OSError as fout:
        regels.append(f"- Downloads-map niet te bepalen: {fout}")

    pad = schrijf("omgeving.md", "\n".join(regels) + "\n")
    print("\n".join(regels))
    print(f"\nOpgeslagen: {pad}")


# --------------------------------------------------------------------------- vensters

def proces_pad(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "(geen toegang)"
    try:
        buf = ctypes.create_unicode_buffer(32768)
        grootte = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(grootte)):
            return buf.value
        return "(onbekend)"
    finally:
        kernel32.CloseHandle(handle)


def zichtbare_vensters() -> list[dict]:
    user32 = ctypes.windll.user32
    resultaat: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        lengte = user32.GetWindowTextLengthW(hwnd)
        titel = ctypes.create_unicode_buffer(lengte + 1)
        user32.GetWindowTextW(hwnd, titel, lengte + 1)
        klasse = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, klasse, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if titel.value:
            resultaat.append({"hwnd": hwnd, "titel": titel.value, "klasse": klasse.value, "pid": pid.value,
                              "exe": proces_pad(pid.value)})
        return True

    user32.EnumWindows(callback, 0)
    return resultaat


def cmd_vensters(args: argparse.Namespace) -> None:
    vereis_windows()
    filt = (args.filter or "").lower()
    regels = ["# Zichtbare vensters", "",
              "| PID | Exe | Vensterklasse | Titel (cijferreeksen gemaskeerd) |", "|---|---|---|---|"]
    for v in zichtbare_vensters():
        if filt and filt not in v["titel"].lower() and filt not in v["exe"].lower():
            continue
        # Het volledige pad kan een gebruikersnaam bevatten; de exe-naam en map zijn genoeg om READY te herkennen.
        exe = Path(v["exe"])
        exe_tekst = f"{exe.parent.name}\\{exe.name}" if exe.name else v["exe"]
        regels.append(f"| {v['pid']} | {exe_tekst} | {v['klasse']} | {maskeer_cijferreeksen(v['titel'])} |")
    pad = schrijf("vensters.md", "\n".join(regels) + "\n")
    print("\n".join(regels))
    print(f"\nOpgeslagen: {pad}")
    print("Zoek de regel van READY en draai daarna: python inspecteer_ready.py boom --pid <PID>")


# --------------------------------------------------------------------------- UI-boom

class Teller:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.aantal = 0
        self.per_type: Counter[str] = Counter()
        self.frameworks: Counter[str] = Counter()
        self.patronen: Counter[str] = Counter()


def uia_patronen(element_info) -> list[str]:
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    gevonden = []
    for naam in UIA_PATRONEN:
        try:
            get_elem_interface(element_info.element, naam)
            gevonden.append(naam)
        except NoPatternInterfaceError:
            pass
        except Exception:  # noqa: BLE001,S110 - een patroon dat niet uit te lezen is, telt als afwezig
            pass
    return gevonden


def lees_element(info, backend: str, diepte: int, pad: str, in_data: bool, args, teller: Teller) -> dict:
    teller.aantal += 1
    if backend == "uia":
        control_type = info.control_type or ""
        knoop = {
            "pad": pad,
            "control_type": control_type,
            "naam": veilige_naam(control_type, info.name or "", in_data, not args.niet_maskeren),
            "automation_id": maskeer_cijferreeksen(info.automation_id or ""),
            "class_name": info.class_name or "",
            "framework": info.framework_id or "",
            "patronen": uia_patronen(info),
        }
        teller.frameworks[knoop["framework"] or "(leeg)"] += 1
        for p in knoop["patronen"]:
            teller.patronen[p] += 1
    else:
        control_type = info.class_name or ""
        # win32 kent geen control types; leid uit de vensterklasse af of de tekst een vast label is.
        privacy_type = "Window" if diepte == 0 else ("Button" if "button" in control_type.lower() else "")
        knoop = {
            "pad": pad,
            "class_name": control_type,
            "naam": veilige_naam(privacy_type, info.name or "", in_data, not args.niet_maskeren),
            "control_id": info.control_id,
        }
    teller.per_type[control_type] += 1
    rect = info.rectangle
    knoop["rect"] = [rect.left, rect.top, rect.right, rect.bottom]
    knoop["zichtbaar"] = bool(info.visible)
    knoop["actief"] = bool(info.enabled)

    if diepte >= args.max_diepte:
        knoop["kinderen_afgekapt"] = "maximale diepte bereikt"
        return knoop
    try:
        kinderen = info.children()
    except Exception as fout:  # noqa: BLE001 - een onleesbaar element mag de inspectie niet stoppen
        knoop["fout"] = type(fout).__name__
        return knoop
    in_data_kind = in_data or control_type in DATA_CONTAINERS or "grid" in (info.class_name or "").lower()
    knoop["aantal_kinderen"] = len(kinderen)
    knoop["kinderen"] = []
    for i, kind in enumerate(kinderen):
        if i >= args.max_kinderen or teller.aantal >= teller.maximum:
            knoop["kinderen_afgekapt"] = f"{len(kinderen) - i} niet getoond"
            break
        knoop["kinderen"].append(lees_element(kind, backend, diepte + 1, f"{pad}/{i}", in_data_kind, args, teller))
    return knoop


def als_tekst(knoop: dict, inspringing: int = 0) -> list[str]:
    ruimte = "  " * inspringing
    soort = knoop.get("control_type") or knoop.get("class_name")
    delen = [f"{ruimte}[{soort}] {knoop['naam']!r}"]
    if knoop.get("automation_id"):
        delen.append(f"id={knoop['automation_id']!r}")
    if knoop.get("control_id"):
        delen.append(f"ctrl_id={knoop['control_id']}")
    if knoop.get("control_type") and knoop.get("class_name"):
        delen.append(f"class={knoop['class_name']!r}")
    if knoop.get("patronen"):
        delen.append(f"patronen={','.join(knoop['patronen'])}")
    if not knoop["zichtbaar"]:
        delen.append("ONZICHTBAAR")
    if not knoop["actief"]:
        delen.append("UITGESCHAKELD")
    delen.append(f"rect={knoop['rect']} pad={knoop['pad']}")
    regels = [" ".join(delen)]
    for kind in knoop.get("kinderen", []):
        regels += als_tekst(kind, inspringing + 1)
    if knoop.get("kinderen_afgekapt"):
        regels.append(f"{ruimte}  ... {knoop['kinderen_afgekapt']}")
    return regels


def cmd_boom(args: argparse.Namespace) -> None:
    vereis_windows()
    try:
        from pywinauto import Desktop
    except ImportError:
        sys.exit("pywinauto ontbreekt. Installeer met: pip install -r requirements-inspectie.txt")

    backends = ["uia", "win32"] if args.backend == "beide" else [args.backend]
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = f"{args.label}-" if args.label else ""
    for backend in backends:
        start = time.monotonic()
        teller = Teller(args.max_elementen)
        # Alleen lezen: Desktop(...).windows() activeert of focust geen venster.
        vensters = Desktop(backend=backend).windows(process=args.pid)
        if not vensters:
            print(f"[{backend}] Geen vensters gevonden voor PID {args.pid}.")
            continue
        bomen = [lees_element(w.element_info, backend, 0, str(i), False, args, teller) for i, w in enumerate(vensters)]
        duur = time.monotonic() - start

        samenvatting = [
            f"# UI-boom READY ({backend}) — {args.label or 'zonder label'}",
            "",
            f"- PID: {args.pid}",
            f"- Topniveauvensters van dit proces: {len(bomen)}",
            f"- Elementen gelezen: {teller.aantal} (max {teller.maximum}) in {duur:.1f} s",
            f"- Per type: {dict(teller.per_type.most_common())}",
        ]
        if backend == "uia":
            samenvatting.append(f"- UI-framework(s): {dict(teller.frameworks.most_common())}")
            samenvatting.append(f"- Ondersteunde patronen: {dict(teller.patronen.most_common())}")
        tekst = samenvatting + ["", "```"] + [r for b in bomen for r in als_tekst(b)] + ["```"]
        md = schrijf(f"boom-{label}{backend}-{stempel}.md", "\n".join(tekst) + "\n")
        js = schrijf(f"boom-{label}{backend}-{stempel}.json", json.dumps(bomen, ensure_ascii=False, indent=1))
        print("\n".join(samenvatting))
        print(f"Opgeslagen: {md}\n            {js}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only inspectie van Windows en READY (geen klikken of export).")
    sub = parser.add_subparsers(dest="opdracht", required=True)

    p = sub.add_parser("omgeving", help="Windows, Python, pakketten, schermen en Downloads-map")
    p.set_defaults(fn=cmd_omgeving)

    p = sub.add_parser("vensters", help="Zichtbare vensters met proces en exe")
    p.add_argument("--filter", help="Toon alleen vensters waarvan titel of exe deze tekst bevat")
    p.set_defaults(fn=cmd_vensters)

    p = sub.add_parser("boom", help="UI Automation-boom van alle vensters van één proces")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--backend", choices=["uia", "win32", "beide"], default="beide")
    p.add_argument("--label", help="Naam van de schermtoestand, bv. hoofdscherm, na-zoeken, exportvenster")
    p.add_argument("--max-diepte", type=int, default=30)
    p.add_argument("--max-kinderen", type=int, default=40, help="Per element; lange lijsten worden afgekapt")
    p.add_argument("--max-elementen", type=int, default=4000)
    p.add_argument("--niet-maskeren", action="store_true",
                   help="Toon alle namen onversleuteld. Alleen voor eigen gebruik; deel deze uitvoer niet.")
    p.set_defaults(fn=cmd_boom)

    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
