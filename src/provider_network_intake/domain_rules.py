"""Healthcare operational domain rules distilled from multi-payer directory reconciliations.

Provides deterministic classification without claiming unverified clinical or licensure truth:
- Hospital-based specialty visibility leaks (Radiology, Anesthesiology, EM, Pathology, Hospitalist, CRNA)
- Payer regulatory regime distinction (Commercial NSA vs. CMS Medicare Advantage vs. Medicaid MCO vs. Delegated Dental/Vision)
- Phone conflict triage (Toll-free centralized desks vs. same-clinic PBX prefixes vs. different area codes)
- Licensure state-consistency gating (strictly enforces self-reported != primary-source verified)
"""

from __future__ import annotations

import re

HOSPITAL_BASED_TAXONOMY_PREFIXES: tuple[str, ...] = (
    "2085R",   # Radiology
    "207L",    # Anesthesiology
    "207P",    # Emergency Medicine
    "207Z",    # Pathology
    "208M",    # Hospitalist
    "367500",  # Certified Registered Nurse Anesthetist (CRNA)
)

HOSPITAL_BASED_KEYWORDS: tuple[str, ...] = (
    "radiology",
    "anesthesiology",
    "emergency medicine",
    "pathology",
    "hospitalist",
    "crna",
    "nurse anesthetist",
)

TOLL_FREE_AREA_CODES = frozenset({"800", "888", "877", "866", "855", "844", "833"})

ACCEPTING_VALUES = frozenset(
    {"newpt", "accepting", "open", "true", "yes", "y", "accepting_new_patients", "accepting new patients"}
)


def is_hospital_based_taxonomy(value: str | None) -> bool:
    """Return True if a NUCC taxonomy code or label matches typically hospital-based specialties."""
    if not value or not isinstance(value, str):
        return False
    cleaned = value.strip()
    token = cleaned.split()[0].upper() if cleaned else ""
    if any(token.startswith(prefix) for prefix in HOSPITAL_BASED_TAXONOMY_PREFIXES):
        return True
    lowered = cleaned.lower()
    return any(kw in lowered for kw in HOSPITAL_BASED_KEYWORDS)


def is_accepting_new_patients_value(value: str | None) -> bool:
    """Return True if a panel/accepting status indicates accepting new patients."""
    if not value or not isinstance(value, str):
        return False
    return " ".join(value.strip().lower().split()) in ACCEPTING_VALUES


def classify_regulatory_regime(network_or_payer: str | None) -> dict[str, str]:
    """Classify payer/network context into its applicable directory verification regime."""
    text = (network_or_payer or "").strip().lower()
    if any(k in text for k in ("tenncare", "medicaid", "community plan", "chip")):
        return {
            "regime": "medicaid_mco",
            "label": "Medicaid / TennCare MCO",
            "sla_note": "Governed by state Medicaid MCO network-adequacy & CMS Medicaid managed-care directory standards (separate from commercial NSA rules).",
        }
    if any(k in text for k in ("medicare advantage", "midsouth", "d-snp", "c-snp", "dual complete", "ma ", "ma-", "-ma")):
        return {
            "regime": "medicare_advantage",
            "label": "CMS Medicare Advantage",
            "sla_note": "Subject to CMS Medicare Advantage directory accuracy oversight and REAL Health Providers Act verification cadence.",
        }
    if any(k in text for k in ("dental", "vision", "fedvip", "optometry", "spectera", "healthplex")):
        return {
            "regime": "delegated_dental_vision",
            "label": "Delegated Dental / Vision",
            "sla_note": "Often routed through delegated dental/vision vendor hubs; check for multi-site corporate hub fan-out.",
        }
    if any(k in text for k in ("commercial", "ppo", "epo", "hmo", "erisa", "volunteer", "choice", "options")):
        return {
            "regime": "commercial_nsa",
            "label": "Commercial / NSA ERISA",
            "sla_note": "Subject to No Surprises Act >=90-day verification cycle and 2-business-day update window.",
        }
    return {
        "regime": "unclassified_regime",
        "label": "Unclassified Payer Regime",
        "sla_note": "Do not apply commercial No Surprises Act deadlines indiscriminately before confirming line of business.",
    }


def _digits10(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    raw = re.sub(r"\D", "", value.split("x")[0].split("X")[0])
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    return raw if len(raw) == 10 else None


def classify_phone_conflict(phone_a: str | None, phone_b: str | None) -> dict[str, str]:
    """Classify a phone disagreement to distinguish centralized desks from stale office lines."""
    pa = _digits10(phone_a)
    pb = _digits10(phone_b)
    if not pa or not pb:
        return {
            "category": "missing_phone",
            "label": "Missing Phone Comparison",
            "guidance": "One or both sources lack a valid 10-digit US telephone number.",
        }
    if pa == pb:
        return {
            "category": "exact_match",
            "label": "Exact Match",
            "guidance": "Normalized 10-digit numbers agree.",
        }
    if pa[:3] in TOLL_FREE_AREA_CODES or pb[:3] in TOLL_FREE_AREA_CODES:
        return {
            "category": "toll_free_centralized_line",
            "label": "Toll-Free Centralized Desk",
            "guidance": "One source lists an 800/8xx call center instead of a local clinic desk; verify patient scheduling preference before overwriting.",
        }
    if pa[:6] == pb[:6]:
        return {
            "category": "same_npa_nxx_clinic_pbx",
            "label": "Same Clinic Prefix (NPA-NXX)",
            "guidance": "First 6 digits match, indicating another DID/extension on the same clinic/hospital PBX rather than an unrelated address.",
        }
    if pa[:3] == pb[:3]:
        return {
            "category": "same_area_code_different_exchange",
            "label": "Same Area Code (Different Exchange)",
            "guidance": "Same metro area code with different exchange; confirm whether clinic moved or uses an off-site scheduling hub.",
        }
    return {
        "category": "different_area_code_or_state",
        "label": "Different Area Code / State",
        "guidance": "Different area code or out-of-state number; high priority for stale billing-office or corporate hub routing check.",
    }


def assess_licensure_state_consistency(
    practice_state: str | None,
    license_state: str | None,
    *,
    psv_verified: bool = False,
) -> dict[str, str]:
    """Evaluate licensure gate status; never promotes self-reported roster/NPPES data to verified."""
    prac = (practice_state or "").strip().upper()
    lic = (license_state or "").strip().upper()
    if psv_verified:
        return {
            "status": "primary_source_verified",
            "label": "Primary-Source Verified (State Board)",
            "psv_required": "false",
            "note": "Confirmed directly against state licensing board primary-source verification record.",
        }
    if lic and prac and lic != prac:
        return {
            "status": "out_of_state_license_only_unverified_psv",
            "label": "Out-of-State Self-Reported License (PSV Required)",
            "psv_required": "true",
            "note": f"Self-reported {lic} license does not match {prac} practice site; verify compact privilege (e.g., eNLC) or active {prac} license via primary source.",
        }
    if lic:
        return {
            "status": "self_reported_state_match_unverified_psv",
            "label": "Self-Reported State License (PSV Required)",
            "psv_required": "true",
            "note": "Self-reported roster/NPPES license state matches clinic state, but CMS notes issuing an NPI or roster value never replaces primary-source board verification.",
        }
    return {
        "status": "no_license_state_recorded_unverified_psv",
        "label": "No License Recorded (PSV Required)",
        "psv_required": "true",
        "note": "No license state recorded in intake/NPPES; manual primary-source board verification required.",
    }
