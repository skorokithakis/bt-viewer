#!/usr/bin/env python3
"""
CSV to JSON converter for blood test data.

Converts CSV files with biomarker data to JSON format for the blood test viewer.
Handles both standard format "Name {unit} [low-high]" and Greek CSV format.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path


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


def convert_csv_to_json(csv_path: str, output_path: str = "data.json") -> None:
    """
    Convert a CSV file with blood test data to JSON format.

    The CSV must have columns for Date and Lab, followed by biomarker columns.
    """
    tests = []

    with open(csv_path, "r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        if not reader.fieldnames:
            raise ValueError("CSV file is empty or has no headers")

        # Find Date and Lab columns (case-insensitive)
        date_col = None
        lab_col = None
        biomarker_columns = {}  # Map column name to parsed info

        for field in reader.fieldnames:
            field_lower = field.lower().strip()
            if "date" in field_lower:
                date_col = field
            elif "lab" in field_lower:
                lab_col = field
            else:
                # Parse biomarker header.
                parsed = parse_biomarker_header(field)
                if parsed["name"]:  # Valid biomarker
                    biomarker_columns[field] = parsed

        if not date_col:
            print("Error: No 'Date' column found in CSV", file=sys.stderr)
            sys.exit(1)

        if not lab_col:
            print("Error: No 'Lab' column found in CSV", file=sys.stderr)
            sys.exit(1)

        if not biomarker_columns:
            print("Error: No valid biomarker columns found in CSV", file=sys.stderr)
            sys.exit(1)

        # Process each row
        for row_num, row in enumerate(
            reader, start=2
        ):  # Start at 2 because row 1 is headers
            # Skip rows with empty date
            if not row.get(date_col, "").strip():
                continue

            # Check for required fields
            if not row[lab_col] or not row[lab_col].strip():
                print(f"Error: Missing lab name in row {row_num}", file=sys.stderr)
                sys.exit(1)

            # Parse the date
            try:
                date_iso = parse_date(row[date_col])
            except ValueError as e:
                print(f"Error in row {row_num}: {e}", file=sys.stderr)
                sys.exit(1)

            # Process biomarkers for this test
            biomarkers = []
            for col, info in biomarker_columns.items():
                value = row.get(col, "").strip()

                # Skip empty values (they may be legitimately missing for some tests)
                if value == "" or value == ".":
                    continue

                # Add biomarker with parsed info
                biomarkers.append(
                    {
                        "name": info["name"],
                        "value": value,
                        "unit": info.get("unit"),
                        "low": info.get("low"),
                        "high": info.get("high"),
                    }
                )

            if biomarkers:  # Only add test if it has biomarkers
                tests.append(
                    {
                        "date": date_iso,
                        "labName": row[lab_col].strip(),
                        "biomarkers": biomarkers,
                    }
                )

    # Create the output JSON structure
    output_data = {"schemaVersion": 1, "tests": tests}

    # Write to output file
    with open(output_path, "w", encoding="utf-8") as jsonfile:
        json.dump(output_data, jsonfile, indent=2, ensure_ascii=False)

    print(f"Successfully converted {len(tests)} tests to {output_path}")


def main():
    """Main entry point for the converter."""
    if len(sys.argv) < 2:
        print("Usage: python converter.py <input.csv> [output.json]", file=sys.stderr)
        print(
            "       If output path is not specified, defaults to 'data.json'",
            file=sys.stderr,
        )
        sys.exit(1)

    csv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "data.json"

    if not Path(csv_path).exists():
        print(f"Error: CSV file '{csv_path}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        convert_csv_to_json(csv_path, output_path)
    except Exception as e:
        print(f"Error during conversion: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
