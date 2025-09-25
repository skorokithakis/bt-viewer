# Blood Test Viewer – Static HTML PRD
Contact: hi@stavros.io

## 1) Overview
A static HTML application for viewing personal blood test results. It provides:
- A single-report view that highlights important biomarkers and lists all results.
- A longitudinal view to visualize a single biomarker across time.
- No runtime server or database; all data is baked in at deployment via a CSV-to-JSON conversion.

Audience: Developers. This document is a detailed PRD for implementation.

## 2) Goals and Non-Goals
Goals
- Provide a clear, fast, static viewer for lab results.
- Make recent abnormalities easy to see.
- Enable deep dives into individual biomarkers over time with polished visualizations.
- Keep implementation vanilla (HTML/CSS/JS), no bundlers.

Non-Goals
- Multi-user accounts, auth, or data upload at runtime.
- Editing data in the app.
- Handling lab-specific unit transformations; units are assumed consistent per biomarker across labs.

## 3) Core User Jobs
1) View a single report (latest test by default).
2) View a biomarker across time (longitudinal view).
3) Highlight out-of-range values.

Definitions
- “Important biomarkers”: any biomarker that is out-of-range in the current test or was out-of-range in any of the previous four tests.
- “Previous four tests”: the four tests prior by date (global across labs). Same-day ties need no special handling.

## 4) Data Model

### 4.1 CSV Schema (Input to Converter)
- Columns:
  - Test date (string, ISO-8601 preferred; converter must parse into a comparable date).
  - Lab name (string).
  - Biomarker columns: one per biomarker.

- Biomarker column header format:
  - “Name {unit} [low-high]”
  - Examples:
    - Hemoglobin {g/dL} [13.5-17.5]
    - LDL Cholesterol {mg/dL} [0-99]
    - Vitamin D {ng/mL} [30-]  (no upper bound)
    - Ferritin {ng/mL} [-300]  (no lower bound)
    - CRP {mg/L} []            (no range)
  - Whitespace around numbers/brackets/braces should be tolerated.
  - Units (and ranges) are optional.
  - Bounds are inclusive when present.

- Cell values:
  - Numeric values: plain numbers (decimals allowed).
  - Non-numeric values (e.g., “<5”, “>200”, “POS”, “NEG”, “trace”) are permitted but are excluded from numeric calculations.

### 4.2 JSON Schema (Output of Converter)
- Location: Same directory as index.html (e.g., ./data.json).
- Top-level object:
  - schemaVersion: integer (starting at 1).
  - tests: array of test objects.

- Test object:
  - date: ISO-8601 string (e.g., “2024-06-12”).
  - labName: string.
  - biomarkers: array of:
    - name: string (biomarker display name from header).
    - value: string (store raw; UI parses numeric where possible).
    - unit: string or null if none in header.
    - low: number|null (inclusive lower bound if present).
    - high: number|null (inclusive upper bound if present).

- No derived data stored in JSON (e.g., abnormal counts); compute in the UI.

- Example:
  {
    "schemaVersion": 1,
    "tests": [
      {
        "date": "2024-06-12",
        "labName": "Acme Lab",
        "biomarkers": [
          { "name": "Hemoglobin", "value": "14.1", "unit": "g/dL", "low": 13.5, "high": 17.5 },
          { "name": "CRP", "value": "<5", "unit": "mg/L", "low": null, "high": null }
        ]
      }
    ]
  }

## 5) Static Architecture
- No backend, no runtime database.
- Data is prepared at build/deploy time by a Python script that converts a single CSV into data.json.
- App loads data.json on startup via fetch.
- Strictly vanilla HTML/CSS/JS; target latest Chrome/Firefox/Safari/Edge.

## 6) UI/UX Requirements

### 6.1 Layout
- Header (app title) + persistent left sidebar (tests list) + main content area.
- Default landing: single-report view for the latest test (by date).
- Sidebar is always visible.

### 6.2 Left Sidebar: Tests List
- Content per test item:
  - Date (primary).
  - Lab name.
  - Abnormal count (number of biomarkers out-of-range in that test).
- Behavior:
  - Reverse-chronological order.
  - Clicking a test loads its single-report view in main content.
  - The currently selected test is visually indicated.

### 6.3 Single-Report View (Main Content)
Order
1) Important biomarkers summary.
2) Full results table.

Important Biomarkers Summary
- Selection:
  - Include any biomarker that is out-of-range in the current test or was out-of-range in any of the previous four tests.
- Ordering:
  - By recency of abnormality (most recent abnormal occurrence first).
  - Tie-breaker: alphabetical by biomarker name.
- Each item shows:
  - Biomarker name.
  - Current value (large; colored):
    - Green if in-range now.
    - Red if out-of-range now.
    - Default/neutral color if no range is defined or value is non-numeric.
  - Previous value (from the immediately preceding test, smaller; colored using that test’s in/out-of-range status).
  - Direction arrow:
    - Up or down by comparing current value vs previous value when both are numeric.
    - Arrow color indicates improvement vs worsening relative to the midpoint of the range:
      - Improvement: moving closer to the midpoint.
      - Worsening: moving further from the midpoint.
      - If equal current/previous numeric values, arrow is green.
    - If no range is defined or values are non-numeric, still show arrow direction based on numeric comparables only; if either value is non-numeric, show a neutral-colored arrow (no improvement/worsening semantics).
- If no important biomarkers, display a friendly “No recent abnormalities” message.

Full Results Table
- Columns: Biomarker, Value, Unit, Reference Range, Status.
- Per-row status:
  - “In range”, “Out of range”, or “No range”.
  - Color text/iconography in line with colors above.

### 6.4 Longitudinal View (Biomarker Detail)
- Navigation:
  - Clicking any biomarker (from summary or table) opens its longitudinal view.
- Chart:
  - Sparkline or compact line chart across all tests.
  - Points plotted only for numeric values. Non-numeric results are omitted from the line but can be listed as annotations in a side note if desired (optional).
  - Background band showing the normal reference range (green shaded area). If only low or high is available, shade from bound to chart edge; if no range, no band.
- Interactions (must-have):
  - Hover tooltip showing date and value (and unit).
- Nice-to-haves:
  - Range brushing to zoom.
  - PNG export of the chart.
- Additional UI:
  - Date picker or scrubber for quickly jumping among tests.
- Library:
  - No mandated choice; vanilla SVG/Canvas or a lightweight library (e.g., uPlot, Chart.js) acceptable. Prioritize visual polish and interaction smoothness.

### 6.5 Visual Design and Accessibility
- No prescribed typography or palette.
- Ensure:
  - Sufficient contrast for in-range (green) and out-of-range (red) indications.
  - Keyboard navigation for sidebar and main interactions.
  - Screen reader labels for test items, biomarker names, values, and status.

## 7) Behavior and Logic

### 7.1 Determining In-Range vs Out-of-Range
- Use biomarker-specific low/high bounds from the CSV header.
- Inclusive bounds: in-range if (low == null or value >= low) and (high == null or value <= high).
- If both low and high are null (no range), status is “No range”.
- Non-numeric values:
  - Display raw string.
  - Exclude from numeric range checks; status “No numeric value” in logic but UI treats as “No range” for coloring and avoids red/green.

### 7.2 Previous Four Tests
- Determine the test’s position by date (descending).
- For the current test at index i, the “previous four” are tests i+1 through i+4.
- Same-day duplicates: no special logic required; keep CSV order or after sorting by date.

### 7.3 Important Biomarkers Selection and Ordering
- Select biomarkers that are:
  - Out-of-range in the current test, OR
  - Out-of-range in any of the previous four tests.
- For ordering:
  - Identify the most recent test (within current + previous four) where the biomarker was out-of-range; sort descending by that date.
  - Tie-break alphabetically by biomarker name.

### 7.4 Arrow and Color Logic (Summary Items)
- Compute midpoint m when both low and high exist: m = (low + high) / 2.
- For two numeric values: previous p and current c
  - Direction: if c > p → up; if c < p → down; if c == p → flat arrow (render as green by rule).
  - Improvement:
    - If both bounds exist:
      - d_prev = |p - m|, d_curr = |c - m|. If d_curr < d_prev → improved (green). If d_curr > d_prev → worsened (red). Equal → green.
    - If only one bound exists:
      - If only low exists: moving upward is improvement if p < low and c is closer to or above low; more precisely, improvement if |c - low| < |p - low|.
      - If only high exists: moving downward is improvement if p > high and c is closer to or below high; improvement if |c - high| < |p - high|.
    - If no bounds or either value non-numeric: arrow shown with neutral color (no improvement semantics).
- Value coloring:
  - Current value colored by current test in/out-of-range.
  - Previous value colored by previous test in/out-of-range.
  - If no range or non-numeric, use neutral color.

### 7.5 Abnormal Count (for Sidebar)
- Count of biomarkers with numeric values that are out-of-range for that test.

### 7.6 Units
- Unit per biomarker is determined by the header and assumed consistent across labs/tests.
- No unit conversion in-app.

## 8) Data Preparation (Python Converter)

### 8.1 Responsibilities
- Input: single CSV with schema described in 4.1.
- Output: data.json as per 4.2, in the same directory as index.html (or write to a known path during build and copied alongside index.html).
- Hard-fail on malformed input with a clear error message to stderr.

### 8.2 Fatal Errors (Cause Immediate Crash)
- Missing test date in any row.
- Missing lab name in any row.
- Empty biomarker value cells (empty string) in any row.
- Missing unit or reference range elements required by header syntax.
- Malformed biomarker header syntax (cannot parse Name, {unit}, and [range] tokens).
- Unparseable date formats.

Note: “Non-numeric” values like “<5” are allowed (not considered empty).

### 8.3 Header Parsing Rules
- Expected pattern: Name {unit} [low-high]
  - Name: non-empty string; may contain spaces.
  - Unit: required; plain string between braces; whitespace trimmed.
  - Range: required token with square brackets:
    - Contents may be “low-high”, “low-”, “-high”, or empty “”.
    - low/high must be numeric if present; missing bounds translate to null.
- Examples considered valid:
  - “CRP {mg/L} []” → low=null, high=null
  - “Ferritin {ng/mL} [-300]” → low=null, high=300
  - “Vitamin D {ng/mL} [30-]” → low=30, high=null
- Examples considered invalid (fatal):
  - Missing {unit}, malformed braces/brackets, non-numeric bounds like “thirty”.

### 8.4 Value Parsing
- Store raw cell value as string in JSON.
- UI will attempt numeric parse using a strict number regex; values that don’t parse are treated as non-numeric and excluded from numeric computations.

### 8.5 Sorting
- The UI is responsible for sorting by date. The converter does not need to sort, but preserving input order is fine.

### 8.6 Versioning
- Include schemaVersion=1 at the JSON top level for future migrations.

## 9) App Initialization
- On load:
  1) Fetch ./data.json.
  2) Validate schemaVersion == 1.
  3) Normalize and sort tests by date descending.
  4) Render sidebar with abnormal counts computed on the fly.
  5) Render single-report view for latest test.

- Error states:
  - If data.json fails to load or parse, show a user-friendly error screen (“Failed to load data. Please rebuild data.json.”). In practice, converter is expected to catch most issues by failing during build.

## 10) Interaction Details

### 10.1 Navigation
- Sidebar: select any test to update main content.
- Biomarker row or summary item: click to open longitudinal view for that biomarker.
- Provide a back affordance to return to the single-report view (e.g., breadcrumb or header button).

### 10.2 Tooltips
- Hover on chart points shows: Date, Value, Unit.
- For out-of-range points, optionally indicate “out of range” in tooltip.

### 10.3 Keyboard
- Arrow keys navigate test list when focused.
- Enter/Space activates selected test.
- Escape returns from longitudinal to single-report (optional).

## 11) Visual States and Edge Cases
- Missing range:
  - No red/green coloring; neutral text.
  - Longitudinal: no shaded band.
- Non-numeric values:
  - Show raw string in both views.
  - Excluded from numeric checks, arrows, and sparkline points.
- Sparse data:
  - If a biomarker only appears in some tests, omit missing test points in the sparkline.
- Equal values:
  - Arrow shown as flat or small neutral indicator; per rule, render green if numeric and equal (considered “not worse”).
- Same-day multiple tests:
  - Treated in input order when computing “previous four.”

## 12) Performance and Compatibility
- Target browsers: latest Chrome, Firefox, Safari, Edge.
- No explicit performance targets; keep data.json size reasonable (typical personal lab histories).
- No build tooling; ship as plain HTML/CSS/JS plus data.json.

## 13) Acceptance Criteria

Data Conversion
- Given a valid CSV with proper headers and at least one test row, the converter produces data.json matching the schema with schemaVersion=1.
- Converter crashes with a clear error message on:
  - Missing date/lab name/required header parts.
  - Empty biomarker value cells.
  - Malformed header or non-numeric bounds.
- Converter preserves raw cell values as strings.

Single-Report View
- Landing page shows the latest test’s single-report view.
- Sidebar lists all tests in reverse chronological order with dates, lab names, and abnormal counts.
- Important biomarkers summary:
  - Includes biomarkers out-of-range in the current or any of the previous four tests.
  - Ordered by recency of abnormality; tie-break alphabetical.
  - Shows current and previous values with appropriate coloring.
  - Arrow direction reflects numeric change; arrow color reflects improvement/worsening relative to midpoint or nearest bound; equal values render green.
- Full table lists all biomarkers with status and range.

Longitudinal View
- Clicking a biomarker navigates to its detail view.
- Sparkline plots only numeric values.
- Green shaded band reflects the biomarker’s reference range when present.
- Hover tooltip shows date, value, and unit.
- Optional: brushing and PNG export if implemented.

General
- No network requests beyond loading data.json.
- Works offline after first load (assuming data.json and assets are available).
- Basic keyboard navigation for sidebar and selection works.
- Non-numeric values are displayed raw and excluded from numeric computations and plots.

## 14) Pseudocode and Algorithms

Numeric Parsing
- isNumeric(s):
  - Return true if s matches /^[+-]?((d+(\\.d+)?)|(\\.d+))$/ (after trimming). Else false.
  - When true, parseFloat(s) yields value.

In-Range Check
- function inRange(value, low, high):
  - if value is not numeric: return null (“No numeric value”).
  - if low != null and value < low: return false.
  - if high != null and value > high: return false.
  - return true.

Midpoint and Improvement
- function improvementColor(prev, curr, low, high):
  - if prev or curr not numeric or (low==null and high==null): return "neutral".
  - if low!=null and high!=null:
    - m = (low+high)/2; dPrev = abs(prev-m); dCurr = abs(curr-m);
  - else if low!=null: dPrev = abs(prev-low); dCurr = abs(curr-low);
  - else if high!=null: dPrev = abs(prev-high); dCurr = abs(curr-high);
  - if dCurr < dPrev: return "green";
  - if dCurr > dPrev: return "red";
  - return "green".

Arrow Direction
- function arrowDir(prev, curr):
  - if prev or curr not numeric: return "neutral";
  - if curr > prev: return "up";
  - if curr < prev: return "down";
  - return "flat".

Abnormal Count
- For each test, count biomarkers where isNumeric(value) and inRange(value, low, high) == false.

Important Biomarkers
- For current test at index i, with biomarkers set B:
  - For each biomarker b in union across tests:
    - recentAbnormal = any t in [i..i+4] where isNumeric(value(t,b)) and inRange(value(t,b), low(b), high(b)) == false.
    - If recentAbnormal, include b.
  - Order by most recent t where out-of-range; tie-break by b.name.

## 15) Example CSV and Resulting JSON

CSV (excerpt)
```

Date,Lab,Hemoglobin {g/dL} [13.5-17.5],CRP {mg/L} [],LDL Cholesterol {mg/dL} [0-99]
2024-06-12,Acme Lab,14.1,<5,120
2024-03-10,Acme Lab,13.2,7,105
2023-12-01,Other Lab,15.0,NEG,95
```
data.json
```
{
  "schemaVersion": 1,
  "tests": [
    {
      "date": "2024-06-12",
      "labName": "Acme Lab",
      "biomarkers": [
        { "name": "Hemoglobin", "value": "14.1", "unit": "g/dL", "low": 13.5, "high": 17.5 },
        { "name": "CRP", "value": "<5", "unit": "mg/L", "low": null, "high": null },
        { "name": "LDL Cholesterol", "value": "120", "unit": "mg/dL", "low": 0, "high": 99 }
      ]
    },
    {
      "date": "2024-03-10",
      "labName": "Acme Lab",
      "biomarkers": [
        { "name": "Hemoglobin", "value": "13.2", "unit": "g/dL", "low": 13.5, "high": 17.5 },
        { "name": "CRP", "value": "7", "unit": "mg/L", "low": null, "high": null },
        { "name": "LDL Cholesterol", "value": "105", "unit": "mg/dL", "low": 0, "high": 99 }
      ]
    },
    {
      "date": "2023-12-01",
      "labName": "Other Lab",
      "biomarkers": [
        { "name": "Hemoglobin", "value": "15.0", "unit": "g/dL", "low": 13.5, "high": 17.5 },
        { "name": "CRP", "value": "NEG", "unit": "mg/L", "low": null, "high": null },
        { "name": "LDL Cholesterol", "value": "95", "unit": "mg/dL", "low": 0, "high": 99 }
      ]
    }
  ]
}
```

## 16) Open Questions / Future Enhancements
- Theming (light/dark modes).
- Export options (PDF of report, CSV of filtered data).
- Annotations: allow user notes per test/biomarker (would require non-static storage).
- Lab-specific normalization if units ever differ across labs (out of scope for now).
