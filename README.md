# vardaDataAi
# 2D-Recipe-Miner (Phase 1A) — README

A lightweight pipeline for **mining 2D semiconductor process “recipes”** from papers and review tables and converting them into **condition-level JSONL records** (one record per experiment condition). This is the foundation for downstream analytics, optimization, and ML/LLM fine-tuning.

---

## What this project does

### Goal

Turn messy paper content (tables + methods text) into a clean dataset:

* **Input:** paper tables (like “Table 3” in review articles) and later full Methods sections
* **Output:** `JSONL` where **each line is one experiment condition** with:

  * normalized fields (temperature, time, pressure, flows, substrate, sources)
  * evidence snippets (what text supported each field)
  * quality flags (missingness, ranges, ambiguous values)

### Why “one record per condition” (Option B)

Optimization and ML models want **rows** that represent **specific experimental settings**, not a single “paper-level” summary.

---

## Current scope (v0.1)

✅ **Table-mode extraction (deterministic, no LLM):**

* Parses a pasted / extracted table block with a header row (TSV-like or whitespace separated)
* Produces condition-level records in `extractions.jsonl`
* Handles common recipe table patterns:

  * “ambient” pressure (optionally converts to 760 Torr)
  * pressure in `Pa` or `Torr`
  * gas flows like `N2 10 sccm` and mixed flows like `Ar14 sccm H2/2 sccm`
  * ranges and profiles (e.g., `30–60 min`, `780–650 °C`) without guessing
  * multi-condition rows (e.g., paired MoO₃/S loads) → splits into multiple records

🟡 **Text-mode extraction (planned):**

* Methods/captions parsing, likely hybrid with LLM + deterministic validators

---

## Repository layout (recommended)

```
2d-recipe-miner/
  README.md
  src/
    extract_table.py
  data/
    raw/
      table_blocks/
        cvde201500060_table3.txt
    out/
      extractions.jsonl
  notebooks/         (optional)
  tests/             (optional)
```

---

## Output format (JSONL)

Each line is one JSON object (one condition):

* `paper`: metadata (DOI, title, year)
* `condition`: the recipe (temp/time/pressure/gases/substrate/reactants)
* `outcomes`: placeholders for device metrics (v0.1 mostly null)
* `evidence`: snippets supporting the extracted values
* `quality`: confidence + flags + missing fields

Example keys:

* `condition.temperature_C` (float or null)
* `condition.pressure_Torr` (float or null)
* `condition.gas_flows_sccm` (dict like `{"Ar": 14, "H2": 2}`)
* `quality.flags` includes labels like:

  * `review_table_row`
  * `pressure_assumed_ambient`
  * `range_value_unresolved`
  * `temperature_profile_unparsed`
  * `row_split_into_multiple_conditions`

---

## How to run (v0.1)

### 1) Requirements

* Python 3.9+ recommended
* No external libraries required (standard library only)

### 2) Prepare a table block file

Create a text file like:

`data/raw/table_blocks/cvde201500060_table3.txt`

Must include a header row and rows. Tabs are best, but 2+ spaces works too.

Example header (TSV):

```
Mo source    Sulfur source   Temp. Time   Pressure   Carrier gas Flow rate   Substrate/ Set-up   Ref
```

### 3) Run the extractor

From repo root:

```bash
python src/extract_table.py \
  --input data/raw/table_blocks/cvde201500060_table3.txt \
  --output data/out/extractions.jsonl \
  --doi 10.1002/cvde.201500060 \
  --title "CVD Growth of MoS2-based Two-dimensional Materials" \
  --year 2015 \
  --venue "Chemical Vapor Deposition" \
  --table-id cvde201500060_table3
```

> If you keep the script as the single-file version I drafted earlier, you’ll paste the table block into `table_text` and run it directly. The CLI interface above is the recommended next tiny refactor.

---

## Parsing rules (what the script currently assumes)

### 1) One record per table row

Each row becomes one condition **unless** the row clearly encodes multiple paired conditions (example: multiple MoO₃ loads and multiple S loads listed in sequence). In that case the script splits the row into multiple records.

### 2) No guessing

If a cell is ambiguous or expressed as a range:

* `30–60 min` → `growth_time_min = null` + flag `range_value_unresolved`
* `780–650 °C` → `temperature_C = null` + flag `temperature_profile_unparsed`
* `∼650 °C` → `temperature_C = null` + flag `approximate_value_present`

### 3) Pressure normalization

* `ambient` → either:

  * `pressure_Torr = 760` + flag `pressure_assumed_ambient` (current behavior), or
  * `pressure_Torr = null` + flag `pressure_reported_ambient` (stricter alternative)
* `30 Pa` → converted to Torr using: `Torr = Pa / 133.322`
* `2 Torr` → `2`

### 4) Gas flow parsing

Supports:

* `Ar 10 sccm`
* `Ar14 sccm H2/2 sccm`
* multiple gases → populates:

  * `condition.carrier_gas = ["Ar","H2"]`
  * `condition.gas_flows_sccm = {"Ar":14, "H2":2}`

### 5) Confidence scoring (simple, rule-based)

Table-derived records start high and lose points when key fields are missing or ambiguous.

---

## Known limitations (v0.1)

* Doesn’t yet extract:

  * ramp rates
  * source-substrate distances
  * boat positions / tube geometry
  * cleaning steps / seeding promoters
* Doesn’t resolve “Ref 14” → actual DOI (planned Phase 1A+)
* Range handling is conservative (stores in notes/flags instead of creating multiple records)

---

## Roadmap

### v0.2 — Better structured fields

* Add explicit fields:

  * `mo_source_type` (MoO₃ / MoCl₅ / MoS₂)
  * `mo_source_load_g`, `s_source_load_g` (when numeric)
  * `cited_reference` (separate from notes)
* Improve range representation:

  * `growth_time_range_min: [30,60]`
  * `temperature_profile_C: {"start":780,"end":650}`

### v0.3 — Hybrid “table + LLM repair”

* Deterministic parser runs first
* If parsing fails or is low confidence, send **only**:

  * table row + caption
    to an open-weights LLM for structured repair
* Same output schema; validator still enforces “no guessing”

### v1.0 — Full Phase 1A pipeline

* PDF ingestion
* automatic table detection
* extraction from tables + methods
* DOI/ref resolution
* dataset QA dashboard (coverage/accuracy/confidence)

---

## Notes on ethics / licensing

Publishers may restrict redistribution of full text for training. This project is designed to store **structured factual parameters** and **short evidence snippets** (not full paper text). Always keep provenance:

* review DOI
* ref numbers
* (later) cited paper DOI where possible

---

## Quick checklist for “good” records

A usable condition record ideally includes:

* temperature
* time
* pressure
* carrier gas + flow
* substrate
* clear Mo + S sources

When any are missing, flags should explain why.

---

If you want, I can also:

* refactor the script into a clean CLI (`argparse`) with the folder structure above, **or**
* generate a minimal `pyproject.toml` + `make` commands + unit tests for the tricky parsers (pressure, gas flows, multi-load splitting).
