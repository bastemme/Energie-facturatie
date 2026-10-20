from privacy import maskeer_cijferreeksen, veilige_naam, vorm


def test_cijferreeksen_worden_gemaskeerd_met_lengte():
    assert maskeer_cijferreeksen("Klant 012345678 - EAN 871234567890123456") == "Klant <9 cijfers> - EAN <18 cijfers>"
    assert maskeer_cijferreeksen("Rij 12") == "Rij 12"


def test_vorm_toont_cijferlengte_maar_geen_letters():
    assert vorm("Kerkstraat 12") == "A 99"
    assert vorm("2026-10-01 00:15") == "9999-99-99 99:99"
    assert vorm("012,5") == "999,9"


def test_labels_blijven_data_wordt_verborgen():
    assert veilige_naam("Button", "Exporteren", False) == "Exporteren"
    assert veilige_naam("Text", "Zoeken", False) == "Zoeken"
    assert veilige_naam("Text", "Jansen", True).startswith("<verborgen")
    assert veilige_naam("Edit", "012345678", False).startswith("<verborgen")
    assert "871234567890123456" not in veilige_naam("ListItem", "871234567890123456", True)
    assert veilige_naam("Edit", "geheim", False, maskeren=False) == "geheim"
