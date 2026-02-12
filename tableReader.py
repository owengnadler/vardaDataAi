import re
import json
import hashlib
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

RE_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

def sha_id(*parts: str) -> str:
    h = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()
    return h[:16]

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def parse_g_mass_list(cell: str) -> List[Optional[float]]:
    """
    Extracts a list of masses in grams from a cell that may contain multiple values.
    Examples:
      "0.0003 g 0.0003 g 0.0005 g" -> [0.0003, 0.0003, 0.0005]
      "-" -> [None]
      "<0.1 g" -> [None]  (stored as notes; numeric not set)
    """
    cell = clean(cell).replace(" ", " ")
    if cell in {"-", "–", "—"}:
        return [None]
    if "<" in cell or ">" in cell or "rich" in cell.lower() or "∼" in cell:
        return [None]
    vals = re.findall(rf"({RE_FLOAT})\s*g\b", cell)
    if not vals:
        # Might be "MoO3 nanoribbons" etc.
        return [None]
    return [float(v) for v in vals]

def parse_s_amount_notes(cell: str) -> str:
    return clean(cell).replace(" ", " ")

def parse_temp_time(cell: str) -> Tuple[Optional[float], Optional[float], List[str], Dict[str, List[str]]]:
    """
    Returns temperature_C, growth_time_min, flags, evidence_map
    Handles:
      "650 °C 15 min" -> (650, 15, [], evidence)
      "780–650 °C 10 min" -> (None, 10, ["temperature_profile_unparsed"], evidence)
      "530 °C 30–60 min" -> (530, None, ["range_value_unresolved"], evidence)
      "∼650 °C/ 15 – 20 min" -> (None, None, ["approximate_value_present","range_value_unresolved"], evidence)
    """
    raw = clean(cell).replace(" ", " ")
    flags: List[str] = []
    evidence = {"temperature_C": [raw], "growth_time_min": [raw]}

    # Approximate marker
    if "∼" in raw or "~" in raw:
        flags.append("approximate_value_present")

    # Temperature profile like 780–650 °C
    if re.search(r"(\d+)\s*[–-]\s*(\d+)\s*°?C", raw):
        flags.append("temperature_profile_unparsed")
        temp_C = None
    else:
        m = re.search(rf"({RE_FLOAT})\s*°?C", raw)
        temp_C = float(m.group(1)) if m else None

    # Time range like 30–60 min or 2–6 min or 10 – 15 min
    if re.search(rf"({RE_FLOAT})\s*[–-]\s*({RE_FLOAT})\s*min", raw):
        flags.append("range_value_unresolved")
        time_min = None
    else:
        m = re.search(rf"({RE_FLOAT})\s*min", raw)
        time_min = float(m.group(1)) if m else None

    # If time present but temp is profile, we keep time_min (e.g., 10 min)
    if "temperature_profile_unparsed" in flags:
        m = re.search(rf"({RE_FLOAT})\s*min", raw)
        if m:
            time_min = float(m.group(1))

    return temp_C, time_min, flags, evidence

def parse_pressure(cell: str) -> Tuple[Optional[float], List[str], List[str]]:
    """
    Returns pressure_Torr, flags, evidence_snippets
    Handles:
      "ambient" -> (760, ["pressure_assumed_ambient"], ["ambient"])
      "–" -> (None, ["pressure_missing"], ["–"])
      "30 Pa" -> (~0.225, [], ["30 Pa"])
      "2 Torr" -> (2, [], ["2 Torr"])
      "780 Torr" -> (780, [], ["780 Torr"])
    """
    raw = clean(cell).replace(" ", " ")
    evid = [raw]
    if raw.lower() == "ambient":
        return 760.0, ["pressure_assumed_ambient"], evid
    if raw in {"-", "–", "—"}:
        return None, ["pressure_missing"], evid

    m = re.search(rf"({RE_FLOAT})\s*Pa\b", raw, re.IGNORECASE)
    if m:
        pa = float(m.group(1))
        torr = pa / 133.322
        return torr, [], evid

    m = re.search(rf"({RE_FLOAT})\s*Torr\b", raw, re.IGNORECASE)
    if m:
        return float(m.group(1)), [], evid

    # Unknown
    return None, ["pressure_unparsed"], evid

def parse_gas_flows(cell: str) -> Tuple[List[str], Dict[str, float], List[str], List[str]]:
    """
    Returns carrier_gas list, gas_flows_sccm dict, flags, evidence_snippets.
    Handles:
      "N2 1 sccm"
      "Ar 10 sccm"
      "Ar14 sccm H2/2 sccm"  (no space)
      "Ar14 sccm H2/2 sccm"  (with slash)
    """
    raw = clean(cell).replace(" ", " ")
    evid = [raw]
    flags: List[str] = []

    # Find patterns like "Ar 10 sccm" or "Ar14 sccm" or "H2/2 sccm"
    # Gas token: letters/numbers/() e.g., N2, Ar, H2, (C2H5)2S (we’ll keep as-is)
    pairs: List[Tuple[str, float]] = []

    # Normalize separators
    tmp = raw.replace("/", " ")
    # Match "Gas 10 sccm" where gas may be adjacent to number (Ar14)
    for m in re.finditer(rf"([A-Za-z0-9\(\)]+)\s*({RE_FLOAT})\s*sccm\b", tmp):
        gas = m.group(1)
        val = float(m.group(2))
        pairs.append((gas, val))

    if not pairs:
        flags.append("gas_flow_unparsed")
        return [], {}, flags, evid

    flows: Dict[str, float] = {}
    gases: List[str] = []
    for gas, val in pairs:
        flows[gas] = val
        gases.append(gas)

    # De-dup while preserving order
    seen = set()
    gases_unique = []
    for g in gases:
        if g not in seen:
            gases_unique.append(g)
            seen.add(g)

    return gases_unique, flows, flags, evid

def split_multi_load_row(mo_cell: str, s_cell: str) -> Optional[List[Tuple[Optional[float], str]]]:
    """
    If the Mo and S cells each contain multiple loads (like Ref 56),
    return a list of paired (mo_g, s_note) entries.
    Otherwise return None.
    """
    mo_cell_c = clean(mo_cell).replace(" ", " ")
    s_cell_c = clean(s_cell).replace(" ", " ")

    mo_vals = re.findall(rf"({RE_FLOAT})\s*g\b", mo_cell_c)
    s_vals = re.findall(rf"(<\s*{RE_FLOAT}\s*g\b|{RE_FLOAT}\s*g\b)", s_cell_c)

    # Heuristic: if Mo has 2+ numeric grams AND S has 2+ tokens, we treat as paired lists
    if len(mo_vals) >= 2 and len(s_vals) >= 2:
        mo_list = [float(v) for v in mo_vals]
        s_list = [clean(v) for v in s_vals]
        # If lengths mismatch, don’t split deterministically
        if len(mo_list) != len(s_list):
            return None
        return list(zip(mo_list, s_list))

    return None

@dataclass
class Record:
    record_id: str
    paper: Dict[str, Any]
    condition: Dict[str, Any]
    outcomes: Dict[str, Any]
    evidence: Dict[str, List[str]]
    quality: Dict[str, Any]

def make_base(paper_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, List[str]], Dict[str, Any]]:
    paper = dict(paper_meta)
    condition = {
        "material": None,
        "growth_method": None,
        "substrate": None,
        "temperature_C": None,
        "pressure_Torr": None,
        "growth_time_min": None,
        "carrier_gas": [],
        "reactants": [],
        "gas_flows_sccm": {},
        "ramp_rate_C_per_min": None,
        "cooldown": None,
        "anneal": {"temperature_C": None, "time_min": None, "atmosphere": None},
        "transfer_method": None,
        "notes": None,
    }
    outcomes = {
        "device_type": None,
        "mobility_cm2_Vs": None,
        "on_off_ratio": None,
        "vth_V": None,
        "contact_resistance_Ohm_um": None,
        "yield_percent": None,
        "layer_count": None,
        "domain_size_um": None,
        "defect_density_cm2": None,
    }
    evidence = {
        "temperature_C": [],
        "pressure_Torr": [],
        "growth_time_min": [],
        "gas_flows_sccm": [],
        "substrate": [],
        "reactants": [],
        "growth_method": [],
    }
    quality = {"confidence": 0.0, "missing_required_fields": [], "flags": []}
    return paper, condition, outcomes, evidence, quality

def row_to_records(cols: Dict[str, str], paper_meta: Dict[str, Any], table_id: str, row_index: int) -> List[Record]:
    mo = cols["Mo source"]
    s = cols.get("Sulfur source", "")
    temp_time = cols.get("Temp. Time", "")
    pressure = cols.get("Pressure", "")
    gas = cols.get("Carrier gas Flow rate", "")
    substrate = cols.get("Substrate/ Set-up", "")
    ref = clean(cols.get("Ref", ""))

    # Multi-load paired split (Ref 56-like)
    paired = split_multi_load_row(mo, s)

    def build_one(mo_override_g: Optional[float], s_override_note: Optional[str], suffix: Optional[str]) -> Record:
        paper, condition, outcomes, evidence, quality = make_base(paper_meta)

        # material: this table is MoS2 growth parameters (assume MoS2, but you can set null if you want stricter)
        condition["material"] = "MoS2"
        condition["growth_method"] = "TVD CVD"
        evidence["growth_method"].append("Table 3 (TVD growth parameters)")

        condition["substrate"] = clean(substrate)
        evidence["substrate"].append(clean(substrate))

        # temp/time
        tC, tmin, tflags, tev = parse_temp_time(temp_time)
        condition["temperature_C"] = tC
        condition["growth_time_min"] = tmin
        for k, v in tev.items():
            evidence[k].extend(v)
        quality["flags"].extend(tflags)

        # pressure
        pT, pflags, pev = parse_pressure(pressure)
        condition["pressure_Torr"] = pT
        evidence["pressure_Torr"].extend(pev)
        quality["flags"].extend(pflags)

        # gas flows
        gases, flows, gflags, gev = parse_gas_flows(gas)
        condition["carrier_gas"] = gases
        condition["gas_flows_sccm"] = flows
        evidence["gas_flows_sccm"].extend(gev)
        quality["flags"].extend(gflags)

        # reactants + loads notes
        reactants = []
        mo_clean = clean(mo).replace(" ", " ")
        s_clean = clean(s).replace(" ", " ")

        # Detect source species label
        if "MoCl5" in mo_clean:
            reactants.append("MoCl5 powder")
        elif "MoS2" in mo_clean:
            reactants.append("MoS2 powder")
        elif "nanoribbons" in mo_clean:
            reactants.append("MoO3 nanoribbons")
        else:
            reactants.append("MoO3 powder")

        if "Single source" not in mo_clean and "MoS2 powder" not in mo_clean:
            # sulfur source exists for most rows
            if "H2S" in s_clean:
                reactants.append("H2S")
            elif s_clean:
                reactants.append("S powder")

        condition["reactants"] = reactants
        evidence["reactants"].append(mo_clean)
        if s_clean:
            evidence["reactants"].append(s_clean)

        notes_parts = []
        if mo_override_g is not None:
            notes_parts.append(f"Mo source load override from paired list: {mo_override_g} g")
        else:
            notes_parts.append(f"Mo source cell: {mo_clean}")

        if s_override_note is not None:
            notes_parts.append(f"S source load override from paired list: {s_override_note}")
        elif s_clean:
            notes_parts.append(f"S source cell: {s_clean}")

        notes_parts.append(f"Cited ref: {ref}.")
        condition["notes"] = " ".join(notes_parts)

        # Flags for vague/inequality
        if "<" in s_clean or (s_override_note and "<" in s_override_note):
            quality["flags"].append("inequality_value_present")
        if "rich" in s_clean.lower():
            quality["flags"].append("sulfur_amount_vague")
        if mo_clean.endswith("-") or mo_clean.strip() in {"-", "–", "—"}:
            quality["flags"].append("mo_source_amount_missing")
        if "∼" in mo_clean or "∼" in s_clean:
            quality["flags"].append("approximate_value_present")

        # Required-field tracking (soft)
        missing = []
        if condition["material"] is None: missing.append("material")
        if condition["growth_method"] is None: missing.append("growth_method")
        if condition["temperature_C"] is None: missing.append("temperature_C")
        quality["missing_required_fields"] = missing

        # Confidence
        conf = 0.90  # table mode base
        for fld in ("temperature_C", "pressure_Torr", "growth_time_min"):
            if condition[fld] is None:
                conf -= 0.15
        if "ambiguous_value" in quality["flags"]:
            conf -= 0.10
        if "range_value_unresolved" in quality["flags"]:
            conf -= 0.08
        if "temperature_profile_unparsed" in quality["flags"]:
            conf -= 0.12
        conf = max(0.0, min(1.0, conf))
        quality["confidence"] = conf

        # Always include provenance flags
        quality["flags"].append("review_table_row")
        if ref:
            quality["flags"].append(f"cited_ref_{ref}")

        rid = f"{table_id}_r{row_index}" + (f"_{suffix}" if suffix else "")
        rec_id = sha_id(paper_meta.get("doi",""), rid)
        return Record(record_id=rec_id, paper=paper, condition=condition, outcomes=outcomes, evidence=evidence, quality=quality)

    records: List[Record] = []
    if paired:
        # Split into multiple conditions
        for i, (mo_g, s_note) in enumerate(paired, start=1):
            rec = build_one(mo_override_g=mo_g, s_override_note=s_note, suffix=str(i))
            rec.quality["flags"].append("row_split_into_multiple_conditions")
            records.append(rec)
    else:
        records.append(build_one(mo_override_g=None, s_override_note=None, suffix=None))

    return records

def parse_table_block(block: str) -> List[Dict[str, str]]:
    lines = [l for l in block.splitlines() if clean(l)]
    header = re.split(r"\t+", lines[0].strip())
    rows: List[Dict[str, str]] = []
    for line in lines[1:]:
        parts = re.split(r"\t+", line.strip())
        # If tabs were lost, fall back to 2+ spaces as delimiter
        if len(parts) == 1:
            parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < len(header):
            # Skip malformed line
            continue
        row = {header[i]: parts[i] for i in range(len(header))}
        rows.append(row)
    return rows

def main():
    # Paste your table block here (must include header row)
    table_text = """Mo source\tSulfur source\tTemp. Time\tPressure\tCarrier gas Flow rate\tSubstrate/ Set-up\tRef
MoO3 powder 0.4 g\tS powder 0.8 g\t650 C 15 min\tambient\tN2 1 sccm\tSiO2/Si face-down\t14
"""
    paper_meta = {
        "doi": "10.1002/cvde.201500060",
        "title": "CVD Growth of MoS2-based Two-dimensional Materials",
        "year": 2015,
        "venue": "Chemical Vapor Deposition",
        "url": None,
    }

    rows = parse_table_block(table_text)
    out_recs: List[Record] = []
    for idx, cols in enumerate(rows, start=1):
        out_recs.extend(row_to_records(cols, paper_meta, table_id="cvde201500060_table3", row_index=idx))

    # Write JSONL
    with open("extractions.jsonl", "w", encoding="utf-8") as f:
        for r in out_recs:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    print(f"Wrote {len(out_recs)} records to extractions.jsonl")

if __name__ == "__main__":
    main()
