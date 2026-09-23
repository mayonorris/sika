"""Reviewed source-specific identities for S0.3; never infer an unknown category."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata

MAPPING_VERSION = "inflation-2026-09-v1"

# Checksums bind these interpretations to the inspected local publications. A changed
# document must be reviewed before it can reuse these rules. Dates are reference
# periods, NOT publication or original-download timestamps.
PROFILES = {
    "inseed_ihpc_2026-05.pdf": dict(sha256="954080aafa7b065fc9be801a8d6651da79bfe3f47edb024e5529f91d03fb0e93", pages=7,
        source_key="inseed.ihpc", publisher="INSEED", title="Indice harmonisé des prix à la consommation",
        url="https://inseed.tg/download/7798/", basis="2023", variant="waemu:ihpc_2023"),
    "inseed_ihpc_2026-06.pdf": dict(sha256="792b218f6159ecd43d5fa9fbea7d79552fa5f458841d79d4e7be0e55ed8dfb30", pages=7,
        source_key="inseed.ihpc", publisher="INSEED", title="Indice harmonisé des prix à la consommation",
        url="https://inseed.tg/download/7866/", basis="2023", variant="waemu:ihpc_2023"),
    "inseed_bulletin_mensuel_2025-09.pdf": dict(sha256="21ab8c005358b5b2090e1804403749fa8c3230d0132dd9d12446062d5451de64", pages=77,
        source_key="inseed.bms", publisher="INSEED", title="Bulletin mensuel des statistiques",
        url="https://inseed.tg/download/7822/", basis="2014", variant="inseed:bms_2014_as_printed"),
    "bceao_politique_monetaire_2023-06.pdf": dict(sha256="ae86c55b3364fd2ee9c29112e258a0386e4fbfefa4bfdeab9979e7be62cbc1fd", pages=78,
        source_key="bceao.rpm", publisher="BCEAO", title="Rapport sur la politique monétaire",
        url="https://www.bceao.int/sites/default/files/2023-07/Rapport%20sur%20la%20politique%20mone%CC%81taire%20-%20Juin%202023.pdf",
        basis=None, variant="bceao:regional_quarterly_inflation"),
}


def normalize(value: str) -> str:
    value = value.replace("’", "'").replace("œ", "oe")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", value).strip()


GEOGRAPHIES = {
    "togo": ("TG", "Togo"), "benin": ("BJ", "Bénin"), "burkina faso": ("BF", "Burkina Faso"),
    "cote d'ivoire": ("CI", "Côte d'Ivoire"), "guinee-bissau": ("GW", "Guinée-Bissau"),
    "mali": ("ML", "Mali"), "niger": ("NE", "Niger"), "senegal": ("SN", "Sénégal"),
    "uemoa": ("WAEMU", "UEMOA"),
}

# (stable category, level, exact normalized aliases). No substring-based matching:
# e.g. food, food-and-beverages, and cereals must remain separate series.
CATEGORIES = (
    ("all_items", "total", "indice global|indice global (global)|global|ihpc au togo|ihpc au togo (glissement annuel)|evolution mensuelle"),
    ("food_beverages", "division", "produits alimentaires et boissons non alcoolisees"),
    ("alcohol_tobacco", "division", "boissons alcoolisees, tabacs et stupefiants"),
    ("clothing_footwear", "division", "articles d'habillement et chaussures"),
    ("housing_utilities", "division", "logement, eau, gaz, electricite et autres combustibles|logement, eau, electricite, gaz et autres combustibles"),
    ("household_maintenance", "division", "meubles, articles de menage et entretien courant du foyer"),
    ("health", "division", "sante"), ("transport", "division", "transport|transports"),
    ("information_communication", "division", "information et communication"),
    ("recreation_culture", "division", "loisirs et culture"), ("education", "division", "enseignement"),
    ("restaurants_accommodation", "division", "restaurants et services d'hebergement"),
    ("insurance_financial", "division", "assurance et services financiers"),
    ("personal_social_other", "division", "soins personnels, protection sociale et biens divers"),
    ("food", "group", "produits alimentaires"), ("cereals", "group", "cereales|dont : cereales"),
    ("meat", "group", "viande fraiche, refrigeree ou congelee"),
    ("preserved_fish", "group", "poisson, seche, sale, en saumure ou fume"),
    ("vegetable_oils", "group", "huiles vegetales"),
    ("fresh_vegetables", "group", "legumes-fruits, frais ou refrigeres"),
    ("tubers_plantains", "group", "tubercules, plantains et bananes a cuire"),
    ("communication_services", "group", "services d'information et de communication"),
    ("other_recreation_goods", "group", "autres biens de loisirs"),
    ("gardens_pets", "group", "jardins et animaux"), ("recreation_services", "group", "services de loisirs"),
    ("cultural_goods", "group", "biens culturels"), ("cultural_services", "group", "services culturels"),
    ("press_books_stationery", "group", "journaux, livres et papeterie"),
    ("package_holidays", "group", "voyages a forfait"),
    ("primary_education", "group", "petite enfance et enseignement primaire"),
    ("secondary_education", "group", "enseignement secondaire"),
    ("postsecondary_education", "group", "enseignement postsecondaire non superieur"),
    ("tertiary_education", "group", "enseignement tertiaire"),
    ("unspecified_education", "group", "enseignement non defini par niveau"),
    ("food_serving_services", "group", "services de service de nourriture et de boissons"),
    ("accommodation_services", "group", "services d'hebergement"), ("insurance", "group", "assurance"),
    ("financial_services", "group", "services financiers"),
    ("personal_care", "group", "biens et services pour soins personnels"),
    ("personal_effects", "group", "effets personnels nca"), ("social_protection", "group", "protection sociale"),
    ("other_services", "group", "autres services"),
    ("electricity_gas_fuels", "group", "electricite, gaz et autres combustibles"),
    ("energy", "secondary_group", "energie"), ("fresh_products", "secondary_group", "produits frais"),
    ("excluding_energy_fresh", "secondary_group", "hors energie et produits frais"),
    ("imported_products", "secondary_group", "importe"), ("local_products", "secondary_group", "local"),
)
CATEGORY_ALIASES = {label: (code, level) for code, level, labels in CATEGORIES for label in labels.split("|")}
CONCEPT_LABELS = {
    "cpi_index": "Indice des prix à la consommation", "inflation_rate_yoy": "Inflation en glissement annuel",
    "inflation_rate_mom": "Variation mensuelle des prix", "inflation_rate_3m": "Variation des prix sur trois mois",
    "inflation_rate_12m_average": "Inflation moyenne sur douze mois",
}
INDEX_KEYS = {
    "consumer_price_index", "cpi_index", "core_consumer_price_index", "energy_price_index", "food_price_index",
    "fresh_products_price_index", "imported_products_price_index", "local_products_price_index", "utilities_price_index",
}


def in_scope(row: dict) -> bool:
    """Include the complete legacy inflation/CPI family, even unsupported subtypes."""
    key = row["indicator"]
    return "inflation" in key or key.startswith("cpi_") or key in INDEX_KEYS


def period_frequency(period: str) -> str | None:
    if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}(?:-Q[1-4]|-(?:0[1-9]|1[0-2]))?", period):
        return None
    if period[:4] == "0000":
        return None
    return "annual" if len(period) == 4 else "quarterly" if "Q" in period else "monthly"


@dataclass(frozen=True)
class Mapping:
    concept: str
    geography: str
    geography_label: str
    frequency: str
    unit: str
    category: str
    aggregation_level: str
    price_basis: str
    source_variant: str

    def dimensions(self) -> dict:
        return dict(category=self.category, sector="all_sectors", aggregation_level=self.aggregation_level,
                    price_basis=self.price_basis, seasonal_adjustment="not_reported", source_variant=self.source_variant)


def map_row(row: dict, profile: dict) -> tuple[Mapping | None, str]:
    """Return an explicit mapping or a quarantine reason; never change numeric values."""
    frequency = period_frequency(row["period"])
    if not frequency:
        return None, "invalid_period"
    try:
        value = Decimal(str(row["value"]))
        if not value.is_finite() or abs(value) >= Decimal("1e18") or value != value.quantize(Decimal("0.0000000001")):
            return None, "unsupported_numeric_value"
    except (InvalidOperation, ValueError):
        return None, "unsupported_numeric_value"
    page = row["source_page"]
    if not isinstance(page, int) or not 1 <= page <= profile["pages"]:
        return None, "invalid_source_page"
    if row.get("confidence") is None or not 0.5 <= row["confidence"] <= 1:
        return None, "legacy_confidence_excluded"
    if normalize(row["indicator_label"]).startswith("variation mensuelle du prix"):
        return None, "retail_price_change_not_cpi"
    geo = normalize(row["geography"]).replace("burkina-faso", "burkina faso")
    if geo not in GEOGRAPHIES:
        return None, "geography_methodology_not_reviewed"
    key = row["indicator"]
    label = normalize(row["indicator_label"])
    concept = None
    if key in INDEX_KEYS or (key.startswith("cpi_") and key.endswith("_index")):
        concept = "cpi_index"
    elif key.endswith("_yoy") or key == "cpi_index_yoy":
        concept = "inflation_rate_yoy"
    elif key.endswith("_mom") or key == "cpi_index_mom":
        concept = "inflation_rate_mom"
    elif key in {"inflation_rate_3m", "inflation_rate_12m_average"}:
        concept = key
    elif key == "inflation_rate_qoq" and frequency == "monthly" and row["source_doc"].startswith("inseed_ihpc_"):
        concept = "inflation_rate_3m"
    if key == "cpi_subindex_weight":
        return None, "weight_scale_not_reviewed"
    if row["source_doc"] == "inseed_bulletin_mensuel_2025-09.pdf":
        if label in {"taux d'inflation", "inflation sous-jacente"}:
            return None, "inflation_window_ambiguous"
        if label.startswith("variation mensuelle du prix"):
            return None, "retail_price_change_not_cpi"
    if not concept:
        return None, "concept_not_reviewed"
    if row["source_doc"].startswith("bceao_"):
        if frequency == "annual":
            return None, "forecast_not_observation" if row["period"] in {"2023", "2024"} else "annual_aggregation_not_reviewed"
        if geo != "uemoa" or frequency != "quarterly" or label not in {"le taux d'inflation", "le taux d'inflation, en glissement annuel"}:
            return None, "bceao_series_not_reviewed"
        category, level = "all_items", "total"
    else:
        if frequency != "monthly":
            return None, "frequency_not_reviewed"
        label = re.sub(r"^[ivx]+\s+", "", label)
        for prefix in ("indices des prix a la consommation - ", "evolution de l'indice de la nomenclature - ",
                       "inflation 12 mois - ", "variation des prix depuis 12 mois - ", "variation (12 mois) - ", "indice - "):
            if label.startswith(prefix):
                label = label[len(prefix):]
                break
        label = re.sub(r" - (?:variation (?:1|3|12) mois|variation annuelle|variation mensuelle|indice)$", "", label)
        label = re.sub(r"\s*\((?:12mois|variations en % depuis 12 mois)\)$", "", label).rstrip("*")
        country_label = label.replace("burkina-faso", "burkina faso")
        if country_label in GEOGRAPHIES:
            if country_label != geo:
                return None, "label_geography_conflict"
            category, level = "all_items", "total"
        elif key == "core_inflation_rate_mom" and label == "l'inflation sous-jacente (variation mensuelle de l'indice hors energie, hors produits frais)":
            category, level = "excluding_energy_fresh", "secondary_group"
        elif key == "inflation_rate_12m_average" and label == "taux d'inflation, calcule sur la base des indices moyens des douze derniers mois au niveau national":
            category, level = "all_items", "total"
        elif key == "inflation_rate_qoq" and label == "niveau general des prix en evolution trimestrielle":
            category, level = "all_items", "total"
        elif label in CATEGORY_ALIASES:
            category, level = CATEGORY_ALIASES[label]
        else:
            return None, "category_not_reviewed"
        if geo != "togo" and country_label not in GEOGRAPHIES:
            return None, "category_geography_not_reviewed"
    unit = "index" if concept == "cpi_index" else "percent"
    if (unit == "percent" and row["unit"] != "%") or (unit == "index" and normalize(row["unit"]) not in {"index", "index (base 100 en 2023)"}):
        return None, "unit_not_reviewed"
    if concept == "cpi_index" and not profile["basis"]:
        return None, "index_base_unknown"
    code, geo_label = GEOGRAPHIES[geo]
    return Mapping(concept, code, geo_label, frequency, unit, category, level,
                   f"index_{profile['basis']}" if concept == "cpi_index" else "not_applicable", profile["variant"]), "reviewed_mapping"
