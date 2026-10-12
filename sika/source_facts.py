"""Checksum-bound source spot checks, not extraction or publication authority.

These comparison anchors were inspected in the complete rendered source pages on
2026-10-11. They cover selected cells only. Never infer missing values, a release
date, reuse rights, or a row-level revision from a matching anchor. Extending this
registry requires source inspection and a versioned change, not a QA workaround.
"""

REVIEW_VERSION = "inflation-source-review-1.1"

# Keep literal hashes independent of PROFILES: changing an ingestion profile must
# not silently transfer these checks to different PDF bytes.
_SOURCES = {
    "inseed_ihpc_2026-05.pdf": (
        "954080aafa7b065fc9be801a8d6651da79bfe3f47edb024e5529f91d03fb0e93", "2023", "waemu:ihpc_2023"),
    "inseed_ihpc_2026-06.pdf": (
        "792b218f6159ecd43d5fa9fbea7d79552fa5f458841d79d4e7be0e55ed8dfb30", "2023", "waemu:ihpc_2023"),
    "inseed_bulletin_mensuel_2025-09.pdf": (
        "21ab8c005358b5b2090e1804403749fa8c3230d0132dd9d12446062d5451de64", "2014", "inseed:bms_2014_as_printed"),
    "bceao_politique_monetaire_2023-06.pdf": (
        "ae86c55b3364fd2ee9c29112e258a0386e4fbfefa4bfdeab9979e7be62cbc1fd", None, "bceao:regional_quarterly_inflation"),
}


def _fact(doc: str, page: int, concept: str, period: str, value: str, locator: str,
          geography: str = "TG", category: str = "all_items", level: str = "total") -> dict:
    digest, base, variant = _SOURCES[doc]
    index = concept == "cpi_index"
    return dict(fact_id=f"{doc}:{page}:{concept}:{geography}:{category}:{period}",
                source_doc=doc, content_sha256=digest, source_page=page,
                concept=concept, geography=geography, unit="index" if index else "percent",
                frequency="quarterly" if doc.startswith("bceao_") else "monthly",
                category=category, sector="all_sectors", aggregation_level=level,
                price_basis=f"index_{base}" if index else "not_applicable",
                seasonal_adjustment="not_reported", source_variant=variant,
                period=period, value=value, locator=locator)


def _headline(doc: str, indices: tuple[tuple[str, str], ...], rates: tuple[str, str, str]) -> list[dict]:
    period = indices[-1][0]
    facts = [_fact(doc, 1, "cpi_index", month, value,
                   f"Tableau 1 / INDICE GLOBAL / indice {month} (base 100 en 2023)")
             for month, value in indices]
    facts.extend(_fact(doc, 1, concept, period, value,
                       f"Tableau 1 / INDICE GLOBAL / variation (%) {window}")
                 for concept, value, window in zip(
                     ("inflation_rate_mom", "inflation_rate_3m", "inflation_rate_yoy"),
                     rates, ("1 mois", "3 mois", "12 mois")))
    return facts


_MAY = "inseed_ihpc_2026-05.pdf"
_JUNE = "inseed_ihpc_2026-06.pdf"
_BMS = "inseed_bulletin_mensuel_2025-09.pdf"
_BCEAO = "bceao_politique_monetaire_2023-06.pdf"

SPOT_CHECKS = tuple(
    _headline(_MAY, (("2025-05", "104.6"), ("2026-02", "103.6"), ("2026-03", "104.5"),
                     ("2026-04", "104.6"), ("2026-05", "105.0")), ("0.4", "1.3", "0.4"))
    + _headline(_JUNE, (("2025-06", "106.1"), ("2026-03", "104.5"), ("2026-04", "104.6"),
                        ("2026-05", "105.0"), ("2026-06", "106.2")), ("1.2", "1.7", "0.1"))
    + [_fact(_BMS, 8, "cpi_index", period, value, f"Tableau 2 / Global / indice {period} (base 2014)")
       for period, value in (("2024-09", "102.6"), ("2025-08", "103.8"), ("2025-09", "102.0"))]
    + [_fact(_BMS, 8, "inflation_rate_mom", "2025-09", "-1.7", "Tableau 2 / Global / variation mensuelle (%)"),
       _fact(_BMS, 8, "inflation_rate_yoy", "2025-09", "-0.6", "Tableau 2 / Global / variation annuelle (%)"),
       _fact(_BCEAO, 13, "inflation_rate_yoy", "2023-Q1", "5.8", "Résumé / paragraphe 7 / UEMOA / premier trimestre 2023", "WAEMU"),
       _fact(_BCEAO, 13, "inflation_rate_yoy", "2022-Q4", "7.8", "Résumé / paragraphe 7 / UEMOA / trimestre précédent", "WAEMU"),
       _fact(_BMS, 9, "cpi_index", "2025-09", "103.5", "Tableau 3 / Energie / indice Sept-25 (base 2014)",
             category="energy", level="secondary_group"),
       _fact(_BMS, 9, "cpi_index", "2025-09", "101.2", "Tableau 3 / Produits frais / indice Sept-25 (base 2014)",
             category="fresh_products", level="secondary_group")]
    # The regional table is one month behind the bulletin's reference month.
    + [_fact(doc, 5, concept, period, value, f"Tableau 2 / {geo} / {column}", geo)
       for doc, period, cells in (
           (_MAY, "2026-04", (("BJ", "102.4", "-0.6"), ("WAEMU", "103.5", "0.0"))),
           (_JUNE, "2026-05", (("BJ", "102.6", "-0.6"), ("WAEMU", "104.0", "0.4"))))
       for geo, index, yoy in cells
       for concept, value, column in (("cpi_index", index, f"indice {period}"),
                                     ("inflation_rate_yoy", yoy, "variation (%) depuis 12 mois"))]
    + [_fact(_JUNE, 7, concept, "2026-06", value, f"Tableau 5 / Services de service de nourriture et de boissons / {column}",
             category="food_serving_services", level="group")
       for concept, value, column in (("cpi_index", "107.9", "indice juin-26"),
                                     ("inflation_rate_yoy", "1.3", "variation (%) depuis 12 mois"))]
)

# These rows cite prose, not the continuation table on a different physical page.
# Keep the original citation and explicit comparison window; do not relocate it
# simply because the same number also occurs elsewhere in the publication.
SPOT_CHECKS += tuple(
    dict(_fact(_JUNE, 1, "inflation_rate_yoy", "2026-06", value,
               f"Résumé des évolutions / évolution annuelle juin 2026 vs juin 2025 / {label}",
               category=category, level="division"), method="inspected_narrative")
    for category, value, label in (
        ("transport", "4.0", "Transport"),
        ("housing_utilities", "5.2", "Logement, eau, électricité, gaz et autres combustibles"),
        ("restaurants_accommodation", "1.3", "Restaurants et services d'hébergement"),
        ("health", "0.8", "Santé"),
        ("personal_social_other", "0.8", "Soins personnels, protection sociale et biens divers"),
        ("clothing_footwear", "0.3", "Articles d'habillement et chaussures"),
        ("information_communication", "-3.4", "Information et communication"),
        ("recreation_culture", "-0.9", "Loisirs et culture"),
    )
)
SPOT_CHECKS += (
    dict(_fact(_MAY, 4, "inflation_rate_12m_average", "2026-05", "0.1",
               "Paragraphe taux d'inflation national / indices moyens des douze derniers mois"),
         method="inspected_narrative"),
    dict(_fact(_MAY, 4, "inflation_rate_mom", "2026-05", "-0.1",
               "Paragraphe inflation sous-jacente / variation mensuelle / hors énergie et produits frais",
               category="excluding_energy_fresh", level="secondary_group"),
         method="inspected_narrative"),
)
