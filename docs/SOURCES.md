# Sources

Primary texts were read on 12 November 2026 (Paris). The PDFs and the long text extracts are **not** in this repository. The clause map quotes short anchors and records where they were read. `config/clause_map.yaml` is the machine-readable copy; `docs/CLAUSE-MAP.md` is generated from it.

| Text | Where | Used for |
| --- | --- | --- |
| HKMA, *Discussion Paper on Agentic A.I. Governance in the Banking Sector*, November 2026, 57 pages | https://www.hkma.gov.hk/media/eng/doc/key-information/press-release/2026/20261102e3a1.pdf | HK-01 … HK-24 |
| HKMA press release, Hong Kong FinTech Week 2026, 2 November 2026 | https://www.hkma.gov.hk/eng/news-and-media/press-releases/2026/11/20261102-3/ | Pointer to the PDF |
| ANSSI, *Les Essentiels – Sécuriser un système IA agentique*, v1.0 (10/26), published 30 October 2026 | https://messervices.cyber.gouv.fr/guides/essentiels-ia-agentique and https://messervices.cyber.gouv.fr/documents-guides/anssi_essentiels_ia_agentique_1.0.pdf | AN-01 … AN-08. French quotes are from the PDF. English in the notes is a gloss, not an official translation. |
| Regulation (EU) 2024/1689 (AI Act), OJ L, 12.7.2024 | http://data.europa.eu/eli/reg/2024/1689/oj | EU-01 … EU-13, and the article wording cited by EU-15 |
| Regulation (EU) 2026/1744 (Digital Omnibus on AI), OJ L, 2026/1744, 24.7.2026, in force 27 July 2026 (Article 4) | https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng | EU-13 (what it does **not** amend), EU-14 (Article 1(40)(b)), EU-15 (Articles 12, 14, 19 and 26 are not rewritten) |

The build spec recorded SHA-256 prefixes of the three PDFs read before Regulation (EU) 2026/1744 was opened: HKMA `52ef9054…119d`, ANSSI `38602e96…b6bd`, AI Act OJ `bba63044…f55e`. Those files are not shipped here, so the full digests are not restated.

## What changed when 2026/1744 was read

On 12 November 2026 the amending regulation was read from EUR-Lex. All 47 map rows are verified.

- **EU-14.** Article 1(40)(b) replaces Article 113, third paragraph, point (c). Chapter III, Sections 1, 2 and 3 (except Article 6(5)) apply from 2 December 2027 for Article 6(2) / Annex III systems, and from 2 August 2028 for Article 6(1) / Annex I systems.
- **EU-15.** The regulation does not amend the wording of Articles 12, 14, 19 or 26, including the six-month log retention in Article 26(6). Article 1(3) adds Article 2(13), which allows a future limitation of Articles 9 to 15 and 17 to 25 for Annex I systems by delegated act, under conditions. That insertion does not rewrite those articles and does not cover Article 26. Quotes of Articles 12, 14, 19 and 26 are the OJ L 12.7.2024 text.
- **EU-13.** Annex III points 5(b) and 5(c), and Article 6(2), are not amended. Article 1(8) only inserts Article 6(1a) to 6(1c).

The card renderer can still print `UNVERIFIED` for a row with `verified: false`. No current row uses that state.

Re-read the map before any use other than this demo. The HKMA paper takes comments until 1 February 2027 and says, at §2.3, that it creates no new obligations.
