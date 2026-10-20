"""Synthetische, geanonimiseerde invoer; geen echte READY-structuur aangenomen."""

from analyseer_txt import analyseer, bepaal_encoding

EAN_A = "000000000000000017"
EAN_B = "871000000000000019"


def schrijf(tmp_path, inhoud: bytes):
    pad = tmp_path / "export.txt"
    pad.write_bytes(inhoud)
    return pad


def test_leeg_bestand(tmp_path):
    assert "Bestand is leeg" in analyseer(schrijf(tmp_path, b""))


def test_encoding_detectie():
    assert bepaal_encoding("é".encode())[0] == "utf-8"
    assert bepaal_encoding("é".encode("cp1252"))[0] == "cp1252"
    assert bepaal_encoding(b"\xef\xbb\xbfabc")[0] == "utf-8-sig"


def test_rapport_bevat_structuur_maar_geen_waarden(tmp_path):
    regels = ["Exportinfo", "EAN;Datum;Stand", f"{EAN_A};01-10-2026 00:15;012,5", f"{EAN_B};01-10-2026 00:30;3,25", ""]
    rapport = analyseer(schrijf(tmp_path, "\r\n".join(regels).encode("cp1252")))
    assert "**puntkomma**" in rapport
    assert "Header: regel 2" in rapport
    assert "Gegevensregels met 3 velden: 2" in rapport
    assert "lengtes {18: 2}" in rapport
    assert "met voorloopnul 1x" in rapport
    assert "als tekst opslaan" in rapport
    assert "decimalen: komma 2x" in rapport
    assert "1 regels wijken af" in rapport
    for geheim in (EAN_A, EAN_B, "012,5", "01-10-2026"):
        assert geheim not in rapport


def test_tab_gescheiden_zonder_header(tmp_path):
    rapport = analyseer(schrijf(tmp_path, b"1\t2\n3\t4\n"))
    assert "**tab**" in rapport
    assert "Header: niet herkend" in rapport
