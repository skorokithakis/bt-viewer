#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "odfpy",
# ]
# ///
"""
CSV and ODS to JSON converter for blood test data.

Converts CSV or ODS files with biomarker data to JSON format for the blood test viewer.
Handles both standard format "Name {unit} [low-high]" and Greek CSV format.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from odf import text, teletype
from odf.opendocument import load
from odf.table import Table, TableRow, TableCell


def parse_date(date_str: str) -> str:
    """
    Parse a date string and return it in ISO-8601 format (YYYY-MM-DD).

    Tries multiple common date formats.
    Raises ValueError if the date cannot be parsed.
    """
    date_formats = [
        "%Y-%m-%d",  # ISO format
        "%Y/%m/%d",
        "%d/%m/%Y",  # European format
        "%m/%d/%Y",  # US format
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%Y%m%d",  # Compact format
    ]

    date_str = date_str.strip()

    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    raise ValueError(f"Unable to parse date: '{date_str}'")


def read_ods_file(file_path: str) -> list:
    """
    Read an ODS file and return data as a list of dictionaries.

    Each dictionary represents a row with column headers as keys.
    """
    doc = load(file_path)
    tables = doc.getElementsByType(Table)

    if not tables:
        raise ValueError("No tables found in ODS file")

    # Use the first table.
    table = tables[0]
    rows = table.getElementsByType(TableRow)

    if not rows:
        raise ValueError("No rows found in ODS table")

    data = []
    headers = []

    for row_idx, row in enumerate(rows):
        cells = row.getElementsByType(TableCell)
        row_data = []

        for cell in cells:
            # Handle repeated cells.
            repeat_count = int(cell.getAttribute("numbercolumnsrepeated") or 1)

            # Get cell value.
            cell_value = ""
            paragraphs = cell.getElementsByType(text.P)
            if paragraphs:
                # Join all text content from all paragraphs.
                cell_value = "".join(teletype.extractText(p) for p in paragraphs)

            # Add value(s) to row data.
            for _ in range(repeat_count):
                row_data.append(cell_value)

        # First row contains headers.
        if row_idx == 0:
            headers = row_data
        else:
            # Skip empty rows.
            if any(val.strip() for val in row_data):
                # Create dictionary for this row.
                row_dict = {}
                for col_idx, header in enumerate(headers):
                    if col_idx < len(row_data):
                        row_dict[header] = row_data[col_idx]
                    else:
                        row_dict[header] = ""
                data.append(row_dict)

    return data


def parse_biomarker_header(header: str) -> dict:
    """
    Parse a biomarker header in format: Name {unit} [low-high]

    Returns a dict with name, unit, low, and high values.
    Unit is optional (returns None if not present).
    Range is optional (returns None for both if not present).
    """
    import re

    # Pattern to match: Name {unit} [range] where unit and range are optional.
    # First try with both unit and range.
    pattern_full = r"^(.+?)\s*\{([^}]*)\}\s*\[([^\]]*)\]$"
    match = re.match(pattern_full, header.strip())

    if match:
        name = match.group(1).strip()
        unit = match.group(2).strip() if match.group(2) else None
        range_str = match.group(3).strip()
    else:
        # Try with just unit, no range.
        pattern_unit = r"^(.+?)\s*\{([^}]*)\}$"
        match = re.match(pattern_unit, header.strip())

        if match:
            name = match.group(1).strip()
            unit = match.group(2).strip() if match.group(2) else None
            range_str = ""
        else:
            # Try with just range, no unit.
            pattern_range = r"^(.+?)\s*\[([^\]]*)\]$"
            match = re.match(pattern_range, header.strip())

            if match:
                name = match.group(1).strip()
                unit = None
                range_str = match.group(2).strip()
            else:
                # No unit or range, just the name.
                name = header.strip()
                unit = None
                range_str = ""

    # Parse the range.
    low = None
    high = None
    if range_str:
        if "-" in range_str:
            parts = range_str.split("-", 1)
            if parts[0].strip():
                try:
                    low = float(parts[0].strip())
                except ValueError:
                    pass
            if parts[1].strip():
                try:
                    high = float(parts[1].strip())
                except ValueError:
                    pass
        else:
            # Single value could be either low or high, we'll treat as high for now.
            try:
                high = float(range_str.strip())
            except ValueError:
                pass

    return {"name": name, "unit": unit, "low": low, "high": high}


def convert_data_to_json(data: list, output_path: str = "data.json") -> None:
    """
    Convert blood test data to JSON format.

    Data is a list of dictionaries where each dict represents a row.
    The data must have columns for Date and Lab, followed by biomarker columns.
    """
    tests = []
    categories = {}

    if not data:
        raise ValueError("No data to convert")

    # Get headers from first row.
    headers = list(data[0].keys())

    # Find Date and Lab columns (case-insensitive).
    date_col = None
    lab_col = None
    biomarker_columns = {}  # Map column name to parsed info.

    for field in headers:
        field_lower = field.lower().strip()
        if "date" in field_lower:
            date_col = field
        elif "lab" in field_lower:
            lab_col = field
        else:
            # Parse biomarker header.
            parsed = parse_biomarker_header(field)
            if parsed["name"]:  # Valid biomarker.
                biomarker_columns[field] = parsed

    if not date_col:
        print("Error: No 'Date' column found in data", file=sys.stderr)
        sys.exit(1)

    if not lab_col:
        print("Error: No 'Lab' column found in data", file=sys.stderr)
        sys.exit(1)

    if not biomarker_columns:
        print("Error: No valid biomarker columns found in data", file=sys.stderr)
        sys.exit(1)

    # Find where categories section starts.
    categories_start_index = None
    for idx, row in enumerate(data):
        # Check all cells in the row for "Categories".
        for value in row.values():
            if value and "Categories" in str(value):
                categories_start_index = idx + 1  # Start reading from next row.
                break
        if categories_start_index is not None:
            break

    # Process test data rows (stop at categories section if found).
    test_data = data[:categories_start_index - 1] if categories_start_index else data

    # Process each row.
    for row_num, row in enumerate(
        test_data, start=2
    ):  # Start at 2 because row 1 is headers.
        # Skip rows with empty date.
        if not row.get(date_col, "").strip():
            continue

        # Check for required fields.
        if not row.get(lab_col) or not row[lab_col].strip():
            print(f"Error: Missing lab name in row {row_num}", file=sys.stderr)
            sys.exit(1)

        # Parse the date.
        try:
            date_iso = parse_date(row[date_col])
        except ValueError as e:
            print(f"Error in row {row_num}: {e}", file=sys.stderr)
            sys.exit(1)

        # Process biomarkers for this test.
        biomarkers = []
        for col, info in biomarker_columns.items():
            value = row.get(col, "").strip()

            # Skip empty values (they may be legitimately missing for some tests).
            if value == "" or value == ".":
                continue

            # Add biomarker with parsed info.
            biomarkers.append(
                {
                    "name": info["name"],
                    "value": value,
                    "unit": info.get("unit"),
                    "low": info.get("low"),
                    "high": info.get("high"),
                }
            )

        if biomarkers:  # Only add test if it has biomarkers.
            tests.append(
                {
                    "date": date_iso,
                    "labName": row[lab_col].strip(),
                    "biomarkers": biomarkers,
                }
            )

    # Process categories section if found.
    if categories_start_index is not None and categories_start_index < len(data):
        for row in data[categories_start_index:]:
            # Get all values from the row.
            row_values = list(row.values())

            # Filter out empty values.
            non_empty_values = [v.strip() for v in row_values if v and v.strip()]

            if len(non_empty_values) >= 2:  # Need at least category name and one biomarker.
                category_name = non_empty_values[0]
                biomarker_names = non_empty_values[1:]
                categories[category_name] = biomarker_names

    # Create the output JSON structure
    output_data = {"schemaVersion": 1, "tests": tests}

    # Add categories if any were found.
    if categories:
        output_data["categories"] = categories

    # Write to output file
    with open(output_path, "w", encoding="utf-8") as jsonfile:
        json.dump(output_data, jsonfile, indent=2, ensure_ascii=False)

    print(f"Successfully converted {len(tests)} tests to {output_path}")


def main():
    """Main entry point for the converter."""
    if len(sys.argv) < 2:
        print(
            "Usage: python converter.py <input.csv|input.ods> [output.json]",
            file=sys.stderr,
        )
        print(
            "       Supports CSV and ODS (OpenDocument Spreadsheet) files",
            file=sys.stderr,
        )
        print(
            "       If output path is not specified, defaults to 'data.json'",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "data.json"

    if not Path(input_path).exists():
        print(f"Error: File '{input_path}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        # Determine file type and read data.
        file_extension = Path(input_path).suffix.lower()

        if file_extension == ".ods":
            # Read ODS file.
            data = read_ods_file(input_path)
        elif file_extension == ".csv":
            # Read CSV file.
            with open(input_path, "r", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                data = list(reader)
        else:
            print(
                f"Error: Unsupported file type '{file_extension}'. Use CSV or ODS.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Convert to JSON.
        convert_data_to_json(data, output_path)
        print(f"Successfully converted {Path(input_path).name} to {output_path}")

    except Exception as e:
        print(f"Error during conversion: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
