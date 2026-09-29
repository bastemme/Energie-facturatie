# Import formats (CSV / XLSX)

Structured exports are the most reliable input. Most supplier portals and energy brokers can export
invoice data to Excel. The importer accepts Dutch or English column names, in any order, with the header
row in the first 15 rows. CSV delimiters `;`, `,` and tab are detected automatically. Numbers can use Dutch
(`1.234,56`) or English (`1234.56`) notation, and dates can be `dd-mm-jjjj`, `jjjj-mm-dd` or `1 januari 2025`.

## Invoice lines (one row per invoice line)

The header fields repeat on every line of the same invoice. Rows are grouped by `factuurnummer`.

| Column (NL) | Alternatives | Required | Notes |
|---|---|---|---|
| factuurnummer | invoice_number, notanummer | yes | |
| bedrag | amount, bedrag_excl_btw | yes | line amount excl. VAT |
| factuurdatum | invoice_date | | |
| leverancier | supplier | | needed for contract matching |
| ean | ean_code, aansluiting | | 18 digits, check digit validated |
| meternummer | meter_number | | |
| periode_start / periode_eind | period_start / period_end, van / tot | | inclusive dates |
| factuurtype | invoice_type | | `factuur` or `creditnota` |
| correctie_op | corrects_invoice_number | | invoice corrected by a credit note |
| product | commodity | | elektriciteit / gas |
| omschrijving | description | | used for category detection |
| categorie | category | | optional explicit category (e.g. `ELECTRICITY_NORMAL`) |
| hoeveelheid | quantity, verbruik | | |
| eenheid | unit | | kWh, MWh, m3, maand, dag, jaar |
| tarief | unit_price, prijs | | |
| btw_percentage | vat_rate | | |
| subtotaal / btw_bedrag / totaal | subtotal / vat_amount / total | | invoice totals (repeat per row) |
| regel_van / regel_tot | line_period_start / line_period_end | | if a line covers a sub-period |

Example:

```
factuurnummer;factuurdatum;leverancier;ean;periode_start;periode_eind;omschrijving;hoeveelheid;eenheid;tarief;bedrag;subtotaal;btw_bedrag;totaal
2026-0012;31-01-2026;Eneco;871685900000000011;01-01-2026;31-01-2026;Levering normaal;1.000;kWh;0,25;250,00;257,50;54,08;311,58
2026-0012;31-01-2026;Eneco;871685900000000011;01-01-2026;31-01-2026;Vaste leveringskosten;1;maand;7,50;7,50;257,50;54,08;311,58
```

## Meter readings

| Column | Alternatives | Required |
|---|---|---|
| datum | reading_date, opnamedatum | yes |
| stand | meterstand, reading | yes |
| ean | | recommended (needed for matching) |
| meternummer | meter_number | |
| register | telwerk | `normaal`, `dal`, `enkel`, `teruglevering` |
| type | reading_type | `werkelijk` / `geschat` |
| vermenigvuldigingsfactor | multiplier | default 1 |
| eenheid | unit | `kWh` (default) or `m3` |

A reading on date *X* is matched to a billing period that starts or ends on *X* (±1 day).
