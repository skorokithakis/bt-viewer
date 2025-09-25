# Blood test viewer

A static HTML application for viewing personal blood test results with no backend or database required. All data is embedded at build time from a CSV file.

## Features

- **Single report view**: View individual blood test results with highlighted abnormalities
- **Longitudinal tracking**: Visualize biomarker trends over time with interactive charts
- **Important biomarkers**: Automatically highlights biomarkers that are currently out of range or have been abnormal in recent tests
- **Static deployment**: No server required - works as a simple HTML file with embedded data

## Getting started

### Prerequisites

- Python 3.x for the CSV to JSON converter
- A modern web browser (Chrome, Firefox, Safari, or Edge)

### Setup

1. Prepare your blood test data in CSV format (see format below)
2. Run the Python converter to generate `data.json`:
   ```bash
   python converter.py input.csv
   ```
3. Open `index.html` in your web browser

## CSV input format

Your CSV file should have the following structure:

### Required columns
- **Date**: Test date (ISO-8601 format recommended, e.g., "2024-06-12")
- **Lab**: Name of the laboratory

### Biomarker columns
Each biomarker should be a column with this header format:
```
Name {unit} [low-high]
```

Examples:
- `Hemoglobin {g/dL} [13.5-17.5]` - with both bounds
- `Vitamin D {ng/mL} [30-]` - minimum only
- `Ferritin {ng/mL} [-300]` - maximum only
- `CRP {mg/L} []` - no reference range

### Sample CSV
```csv
Date,Lab,Hemoglobin {g/dL} [13.5-17.5],LDL Cholesterol {mg/dL} [0-99],Vitamin D {ng/mL} [30-]
2024-06-12,Acme Lab,14.1,120,45
2024-03-10,Acme Lab,13.2,105,28
2023-12-01,Other Lab,15.0,95,35
```

### Value formats
- **Numeric values**: Plain numbers (e.g., `14.1`, `120`)
- **Non-numeric values**: Strings like `<5`, `>200`, `NEG`, `POS` are allowed but won't appear in charts
