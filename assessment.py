#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "odfpy",
#     "anthropic",
# ]
# ///
"""
Blood test assessment generator.

This script reads an ODS file with blood test data, identifies important biomarkers
for each test, and uses Claude (Anthropic API) to generate medical assessments.
"""

import argparse
import os
import pathlib
import re
import sys
import textwrap
from typing import Any

import anthropic

try:
    from odf import teletype, text
    from odf.opendocument import load
    from odf.table import Table, TableRow, TableCell
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "The 'odfpy' package is required to read OpenDocument files. "
        "Install it with 'pip install odfpy'."
    ) from exc


DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-1"


def parse_biomarker_header(header: str) -> dict[str, Any]:
    """Parse a biomarker header in format: Name {unit} [low-high]

    Returns a dict with name, unit, low, and high values.
    Unit is optional (returns None if not present).
    Range is optional (returns None for both if not present).
    """
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


def read_ods_tests(ods_path: pathlib.Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Read blood test data from ODS file.

    Returns:
        Tuple of (tests, biomarker_info, assessment_column_index) where:
        - tests: List of test dictionaries with date, lab, biomarkers, assessment
        - biomarker_info: List of biomarker metadata dicts with name, unit, low, high
        - assessment_column_index: Column index for the Assessment column
    """
    ods_path = ods_path.expanduser().resolve()
    if not ods_path.is_file():
        raise FileNotFoundError(f"OpenDocument file not found: {ods_path}")

    try:
        document = load(str(ods_path))
    except Exception as exc:
        raise ValueError("Invalid OpenDocument file") from exc

    table: Table | None = None
    for element in document.getElementsByType(Table):
        table = element
        break

    if table is None:
        raise ValueError("No table found in ODS file")

    rows = table.getElementsByType(TableRow)
    if len(rows) < 2:
        raise ValueError("ODS file must have at least a header row and one data row")

    # Parse header row to get column names.
    header_row = rows[0]
    headers = []
    for cell in header_row.childNodes:
        repeat = int(cell.getAttribute("numbercolumnsrepeated") or "1")
        text_content = teletype.extractText(cell).strip()
        # Stop at large repeated empty columns to avoid excessive processing.
        if repeat > 100:
            break
        for _ in range(repeat):
            headers.append(text_content)

    # Find Date, Lab, and Assessment columns.
    date_column_index = None
    lab_column_index = None
    assessment_column_index = None

    for idx, header in enumerate(headers):
        header_lower = header.lower()
        if "date" in header_lower and date_column_index is None:
            date_column_index = idx
        elif "lab" in header_lower and lab_column_index is None:
            lab_column_index = idx
        elif "assessment" in header_lower and assessment_column_index is None:
            assessment_column_index = idx

    if date_column_index is None:
        raise ValueError("No 'Date' column found in ODS file")
    if lab_column_index is None:
        raise ValueError("No 'Lab' column found in ODS file")
    if assessment_column_index is None:
        raise ValueError("No 'Assessment' column found in ODS file")

    # Parse biomarker columns (all columns between Lab and Assessment).
    biomarker_info = []
    biomarker_start_index = lab_column_index + 1

    for idx in range(biomarker_start_index, assessment_column_index):
        if idx < len(headers) and headers[idx]:
            parsed = parse_biomarker_header(headers[idx])
            biomarker_info.append(
                {
                    "column_index": idx,
                    "name": parsed["name"],
                    "unit": parsed["unit"],
                    "low": parsed["low"],
                    "high": parsed["high"],
                }
            )

    # Read test data rows.
    tests = []
    for row_index in range(1, len(rows)):
        row = rows[row_index]
        cells = []
        for cell in row.childNodes:
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or "1")
            text_content = teletype.extractText(cell).strip()
            if repeat > 100:
                break
            for _ in range(repeat):
                cells.append(text_content)

        # Skip rows without a date.
        if date_column_index >= len(cells) or not cells[date_column_index]:
            continue

        date = cells[date_column_index]
        lab = cells[lab_column_index] if lab_column_index < len(cells) else ""
        assessment = (
            cells[assessment_column_index]
            if assessment_column_index < len(cells)
            else ""
        )

        # Extract biomarker values for this test.
        biomarkers = []
        for bio_info in biomarker_info:
            idx = bio_info["column_index"]
            value_str = cells[idx] if idx < len(cells) else ""

            # Skip empty values.
            if not value_str or value_str == ".":
                continue

            # Try to convert to float.
            value = None
            try:
                value = float(value_str)
            except ValueError:
                # Keep as string if conversion fails.
                value = value_str

            biomarkers.append(
                {
                    "name": bio_info["name"],
                    "value": value,
                    "unit": bio_info["unit"],
                    "low": bio_info["low"],
                    "high": bio_info["high"],
                }
            )

        tests.append(
            {
                "row_index": row_index,
                "date": date,
                "lab": lab,
                "biomarkers": biomarkers,
                "assessment": assessment,
            }
        )

    return tests, biomarker_info, assessment_column_index


def is_value_out_of_range(value: float | str | None, low: float | None, high: float | None) -> bool:
    """Check if a biomarker value is out of its reference range.

    Returns True if the value is outside the range [low, high].
    Handles None values for value, low, and high appropriately.
    """
    # Can't determine if out of range without a value.
    if value is None:
        return False

    # Convert value to float if it's a string.
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return False

    # If neither bound is set, can't be out of range.
    if low is None and high is None:
        return False

    # Check against bounds.
    if low is not None and value < low:
        return True
    if high is not None and value > high:
        return True

    return False


def identify_important_biomarkers(
    tests: list[dict[str, Any]], current_test_index: int
) -> set[str]:
    """Identify important biomarkers for a given test.

    A biomarker is important if it's out of range in the current test OR
    was out of range in any of the previous 3 tests.

    Args:
        tests: List of all tests in chronological order
        current_test_index: Index of the current test in the tests list

    Returns:
        Set of biomarker names that are important for this test
    """
    important_biomarkers = set()
    current_test = tests[current_test_index]

    # Check which biomarkers are out of range in the current test.
    for biomarker in current_test["biomarkers"]:
        if is_value_out_of_range(
            biomarker["value"], biomarker["low"], biomarker["high"]
        ):
            important_biomarkers.add(biomarker["name"])

    # Check previous 3 tests for out-of-range biomarkers.
    previous_test_start = max(0, current_test_index - 3)
    for test_index in range(previous_test_start, current_test_index):
        previous_test = tests[test_index]
        for biomarker in previous_test["biomarkers"]:
            if is_value_out_of_range(
                biomarker["value"], biomarker["low"], biomarker["high"]
            ):
                important_biomarkers.add(biomarker["name"])

    return important_biomarkers


def get_historical_values(
    tests: list[dict[str, Any]], current_test_index: int, biomarker_name: str
) -> list[dict[str, Any]]:
    """Get historical values for a biomarker from previous tests.

    Returns up to 3 previous test values for the given biomarker.

    Args:
        tests: List of all tests in chronological order
        current_test_index: Index of the current test
        biomarker_name: Name of the biomarker to get history for

    Returns:
        List of dicts with date and value for previous tests (most recent first)
    """
    history = []
    previous_test_start = max(0, current_test_index - 3)

    # Iterate from most recent to oldest.
    for test_index in range(current_test_index - 1, previous_test_start - 1, -1):
        test = tests[test_index]
        # Find the biomarker in this test.
        for biomarker in test["biomarkers"]:
            if biomarker["name"] == biomarker_name:
                history.append({"date": test["date"], "value": biomarker["value"]})
                break

    return history


def build_assessment_prompt(
    test: dict[str, Any],
    important_biomarkers: set[str],
    tests: list[dict[str, Any]],
    current_test_index: int,
) -> str:
    """Build a prompt for Claude to assess the blood test results.

    Args:
        test: The current test to assess
        important_biomarkers: Set of biomarker names that are important
        tests: All tests for historical context
        current_test_index: Index of current test in tests list

    Returns:
        Formatted prompt string
    """
    # Build biomarker summary for current test.
    biomarker_lines = []
    for biomarker in test["biomarkers"]:
        name = biomarker["name"]
        value = biomarker["value"]
        unit = biomarker["unit"] or ""
        low = biomarker["low"]
        high = biomarker["high"]

        # Format reference range.
        if low is not None and high is not None:
            ref_range = f"[{low}-{high}]"
        elif low is not None:
            ref_range = f"[≥{low}]"
        elif high is not None:
            ref_range = f"[≤{high}]"
        else:
            ref_range = ""

        # Format the line.
        is_important = name in important_biomarkers
        marker = "⚠️ " if is_important else "  "
        biomarker_lines.append(f"{marker}{name}: {value} {unit} {ref_range}".strip())

    current_biomarkers_text = "\n".join(biomarker_lines)

    # Build historical data for important biomarkers.
    historical_text = ""
    if important_biomarkers:
        historical_lines = []
        for biomarker_name in sorted(important_biomarkers):
            history = get_historical_values(tests, current_test_index, biomarker_name)
            if history:
                history_values = ", ".join(
                    [f"{h['date']}: {h['value']}" for h in history]
                )
                historical_lines.append(f"- {biomarker_name}: {history_values}")

        if historical_lines:
            historical_text = (
                "\n\nHISTORICAL VALUES FOR IMPORTANT BIOMARKERS (previous 3 tests):\n"
                + "\n".join(historical_lines)
            )

    prompt = textwrap.dedent(
        f"""
        You are a medical AI assistant analyzing blood test results. Please provide a concise assessment of the following blood test.

        TEST DATE: {test['date']}
        LABORATORY: {test['lab']}

        BIOMARKERS (⚠️ indicates biomarkers that are currently out of range or were out of range in the previous 3 tests):

        {current_biomarkers_text}
        {historical_text}

        Please provide a brief medical assessment (2-4 paragraphs) that:
        1. Highlights general trends (which biomarkers are improving, which are worsening)
        2. Identifies any health concerns or areas that need attention
        3. Notes any medically relevant patterns or relationships between biomarkers
        4. Provides context about what these results might mean for overall health

        Focus on actionable insights and meaningful trends rather than simply restating reference ranges.
        Write in a clear, professional medical tone suitable for a patient reviewing their results.
        """
    ).strip()

    return prompt


def generate_assessment_with_claude(
    test: dict[str, Any],
    important_biomarkers: set[str],
    tests: list[dict[str, Any]],
    current_test_index: int,
    model: str = DEFAULT_ANTHROPIC_MODEL,
) -> str:
    """Generate an assessment for a blood test using Claude.

    Args:
        test: The test to assess
        important_biomarkers: Set of important biomarker names
        tests: All tests for context
        current_test_index: Index of current test
        model: Anthropic model to use

    Returns:
        Assessment text generated by Claude
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = build_assessment_prompt(
        test, important_biomarkers, tests, current_test_index
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    assessment_text = response.content[0].text
    return assessment_text.strip()


def write_assessments_to_ods(
    ods_path: pathlib.Path,
    assessments: dict[int, str],
    assessment_column_index: int,
) -> None:
    """Write assessments back to the ODS file.

    Args:
        ods_path: Path to the ODS file
        assessments: Dictionary mapping row_index to assessment text
        assessment_column_index: Column index for the Assessment column
    """
    ods_path = ods_path.expanduser().resolve()
    if not ods_path.is_file():
        raise FileNotFoundError(f"OpenDocument file not found: {ods_path}")

    # Load the document.
    document = load(str(ods_path))

    # Get the first table.
    table: Table | None = None
    for element in document.getElementsByType(Table):
        table = element
        break

    if table is None:
        raise ValueError("No table found in ODS file")

    rows = table.getElementsByType(TableRow)

    # Write assessments to the appropriate rows.
    for row_index, assessment_text in assessments.items():
        if row_index >= len(rows):
            continue

        row = rows[row_index]
        cells = list(row.childNodes)

        # Find the assessment cell, handling repeated columns properly.
        current_column_index = 0
        target_cell = None
        cell_list_index = None
        offset_in_repeat = 0

        for cell_idx, cell in enumerate(cells):
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or "1")

            # Check if the assessment column is within this cell's range.
            if current_column_index <= assessment_column_index < current_column_index + repeat:
                target_cell = cell
                cell_list_index = cell_idx
                offset_in_repeat = assessment_column_index - current_column_index
                break

            current_column_index += repeat

        if target_cell is None:
            print(
                f"Warning: Could not find assessment cell for row {row_index}",
                file=sys.stderr,
            )
            continue

        # If the target cell is part of a repeated range, we need to split it.
        repeat = int(target_cell.getAttribute("numbercolumnsrepeated") or "1")

        if repeat > 1:
            # We need to split the repeated cell into three parts:
            # 1. Cells before the target (if offset > 0)
            # 2. The target cell itself
            # 3. Cells after the target (if offset < repeat - 1)

            # Remove the repeated cell attribute from the target cell.
            if target_cell.getAttribute("numbercolumnsrepeated"):
                target_cell.removeAttribute("numbercolumnsrepeated")

            # Create cells before if needed.
            if offset_in_repeat > 0:
                before_cell = TableCell()
                before_cell.setAttribute("numbercolumnsrepeated", str(offset_in_repeat))
                row.insertBefore(before_cell, target_cell)

            # Create cells after if needed.
            if offset_in_repeat < repeat - 1:
                after_cell = TableCell()
                after_cell.setAttribute(
                    "numbercolumnsrepeated", str(repeat - offset_in_repeat - 1)
                )
                # Insert after the target cell.
                # Find the next sibling of target_cell.
                next_sibling = None
                for node in row.childNodes:
                    if node == target_cell:
                        # Get next sibling.
                        idx = list(row.childNodes).index(node)
                        if idx + 1 < len(row.childNodes):
                            next_sibling = row.childNodes[idx + 1]
                        break

                if next_sibling:
                    row.insertBefore(after_cell, next_sibling)
                else:
                    row.addElement(after_cell)

        # Clear existing content in the target cell.
        for child in list(target_cell.childNodes):
            target_cell.removeChild(child)

        # Add the new assessment text as a paragraph.
        p = text.P(text=assessment_text)
        target_cell.addElement(p)

    # Save the document.
    document.save(str(ods_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate blood test assessments using Claude AI."
    )
    parser.add_argument(
        "ods",
        type=pathlib.Path,
        help="Path to the OpenDocument spreadsheet with blood test data",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_ANTHROPIC_MODEL,
        help=f"Anthropic model to use for assessment (default: {DEFAULT_ANTHROPIC_MODEL})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of tests to process (for testing purposes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: generate a dummy assessment without calling Claude API",
    )

    args = parser.parse_args()

    # Read the ODS file.
    print("Reading blood test data from ODS file...", file=sys.stderr)
    try:
        tests, biomarker_info, assessment_column_index = read_ods_tests(args.ods)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Found {len(tests)} tests in the spreadsheet.", file=sys.stderr)
    print(f"Found {len(biomarker_info)} biomarker columns.", file=sys.stderr)

    # Process each test that doesn't have an assessment.
    assessments_to_write = {}
    tests_to_process = [
        (idx, test) for idx, test in enumerate(tests) if not test["assessment"]
    ]

    # Apply limit if specified.
    if args.limit and args.limit > 0:
        tests_to_process = tests_to_process[: args.limit]
        print(f"Limiting processing to {len(tests_to_process)} tests.", file=sys.stderr)

    if not tests_to_process:
        print("All tests already have assessments. Nothing to do.", file=sys.stderr)
        return

    print(
        f"\nProcessing {len(tests_to_process)} tests without assessments...",
        file=sys.stderr,
    )

    for idx, test in tests_to_process:
        print(f"\nProcessing test {idx + 1}/{len(tests)}: {test['date']}", file=sys.stderr)

        # Identify important biomarkers.
        important_biomarkers = identify_important_biomarkers(tests, idx)
        print(
            f"  Found {len(important_biomarkers)} important biomarkers.",
            file=sys.stderr,
        )

        # Generate assessment with Claude.
        try:
            if args.dry_run:
                print("  Generating dummy assessment (dry-run mode)...", file=sys.stderr)
                assessment = f"[DRY RUN] Assessment for test on {test['date']} with {len(important_biomarkers)} important biomarkers."
            else:
                print("  Generating assessment with Claude...", file=sys.stderr)
                assessment = generate_assessment_with_claude(
                    test, important_biomarkers, tests, idx, args.model
                )
            assessments_to_write[test["row_index"]] = assessment
            print(f"  ✓ Assessment generated ({len(assessment)} characters)", file=sys.stderr)
        except Exception as exc:
            print(f"  ✗ Failed to generate assessment: {exc}", file=sys.stderr)
            continue

    # Write assessments back to the ODS file.
    if assessments_to_write:
        print(
            f"\nWriting {len(assessments_to_write)} assessments back to ODS file...",
            file=sys.stderr,
        )
        try:
            write_assessments_to_ods(
                args.ods, assessments_to_write, assessment_column_index
            )
            print(f"✓ Successfully wrote assessments to {args.ods}", file=sys.stderr)
        except Exception as exc:
            print(f"✗ Failed to write assessments: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\nNo assessments were generated.", file=sys.stderr)


if __name__ == "__main__":
    main()
