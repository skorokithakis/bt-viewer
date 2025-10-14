#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "odfpy",
#     "anthropic",
# ]
# ///
import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
from typing import Any

import anthropic

try:
    from odf import teletype, text
    from odf.opendocument import load
    from odf.table import Table, TableRow, TableCell
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise ModuleNotFoundError(
        "The 'odfpy' package is required to read OpenDocument files. "
        "Install it with 'pip install odfpy'."
    ) from exc


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


# This script will convert a PDF file of blood test results into a series of PNG
# images, then use Claude (Anthropic API) to OCR the images and extract
# biomarker data.
#
# It requires an OpenDocument spreadsheet (--ods), from which it extracts column
# names to pass to Claude. Claude is instructed to follow the exact order and
# spelling of those biomarker names when parsing the images. Any biomarkers not in
# the provided list are placed at the end, following the naming style of the
# existing biomarkers.
#
# The script outputs a JSON object with biomarker names, values, units, and reference
# ranges extracted from the blood test images.


def encode_image(image_path: pathlib.Path) -> str:
    """Encode an image file to base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def ocr_images_with_claude(
    image_paths: list[pathlib.Path],
    biomarker_names: list[str] = [],
    lab_names: set[str] = set(),
    model: str = DEFAULT_ANTHROPIC_MODEL,
) -> dict[str, Any]:
    """OCR blood test images using Claude and extract biomarker data.

    Args:
        image_paths: List of paths to PNG images to process
        biomarker_names: Optional list of known biomarker names to use as reference
        lab_names: Optional set of known lab names to choose from
        model: Anthropic model to use for OCR

    Returns:
        Dictionary containing extracted biomarker data with structure:
        {
            "lab_name": "Laboratory Name",
            "date": "YYYY-MM-DD",
            "biomarkers": [
                {
                    "name": "Biomarker Name",
                    "value": 123.45,
                    "unit": "mg/dL",
                    "reference_range": "100-200"
                },
                ...
            ]
        }
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.Anthropic(api_key=api_key)

    # Prepare the prompt
    lab_names_section = ""
    if lab_names:
        lab_names_section = textwrap.dedent(
            f"""

            LABORATORY NAMES: I have an existing list of lab names in my spreadsheet. If you can identify the lab name from the images, please choose from this list if possible:

            {json.dumps(list(lab_names), indent=2)}

            If the lab name in the image closely matches one of these (even with slight variations), use the EXACT name from the list above.
            If it doesn't match any of these, use the lab name as written in the image.
        """
        )

    prompt = textwrap.dedent(
        f"""
            Please analyze these blood test result images and extract the lab name, date, and all biomarker data.

            LABORATORY NAME AND DATE:
            - Extract the laboratory/clinic name from the images
            - Extract the date of the test (return in YYYY-MM-DD format if possible)
            {lab_names_section}

            BIOMARKERS: I have an existing spreadsheet with biomarker columns. You MUST follow the exact order and spelling of these biomarker names:

            {json.dumps(biomarker_names, indent=2)}

            For each biomarker found in the images:
            - If it matches one of the above names, use the EXACT spelling and capitalization from the list
            - Extract the value (as a number, not string), unit, and reference ranges
            - Return biomarkers in the SAME ORDER as the list above (only include biomarkers found in the images)

            If you find biomarkers that are NOT in the above list:
            - Add them at the END of the results
            - Follow the naming style/pattern of the existing biomarker names
            - Use clear, consistent naming

            For each biomarker, extract:
            - name: The name of the biomarker/test (exact spelling from list, or new name for unlisted biomarkers)
            - value: The numerical result (as a number, not string)
            - unit: The unit of measurement
            - range_lower: The lower end of the reference/normal range if provided
            - range_upper: The upper end of the reference/normal range if provided

            Return the data as a JSON object with this exact structure:
            {{
                "lab_name": "Laboratory Name",
                "date": "YYYY-MM-DD",
                "biomarkers": [
                    {{
                        "name": "Biomarker Name",
                        "value": 123.45,
                        "unit": "mg/dL",
                        "range_lower": 100,
                        "range_upper": 200
                    }}
                ]
            }}

            Important:
            - Extract the lab name and date from the top of the blood test report
            - Extract ALL biomarkers visible in the images
            - Use null for missing fields
            - Ensure values are numbers, not strings
            - Maintain the order: known biomarkers first (in list order), new biomarkers at the end
        """
    ).strip()

    # Prepare image content for the API
    content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
    ]

    # Add all images to the message
    for image_path in image_paths:
        encoded_image = encode_image(image_path)
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": encoded_image,
                },
            }
        )

    messages = [
        {
            "role": "user",
            "content": content,
        }
    ]

    # Make the API call
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=messages,
        temperature=0,  # Use low temperature for more consistent extraction
    )

    # Extract JSON from the response
    response_text = response.content[0].text

    # Try to parse the JSON response
    try:
        # Look for JSON in the response (it might be wrapped in markdown code blocks)
        json_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
        )
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON
            json_str = response_text

        result = json.loads(json_str)
        return result
    except json.JSONDecodeError as exc:
        # If we can't parse JSON, return the raw text for debugging
        print(f"Failed to parse JSON response: {exc}", file=sys.stderr)
        print(f"Raw response:\n{response_text}", file=sys.stderr)
        return {"error": "Failed to parse JSON", "raw_response": response_text}


def convert_pdf_to_images(pdf_path: pathlib.Path, prefix: str) -> pathlib.Path:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="bt-ocr-"))
    output_prefix = temp_dir / prefix

    command = [
        "pdftoppm",
        "-png",
        "-r",
        "300",
        str(pdf_path),
        str(output_prefix),
    ]

    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "pdftoppm command not found. Install poppler-utils and try again."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("pdftoppm failed to convert the PDF") from exc

    return temp_dir


def extract_columns_from_open_document_first_row(
    odf_path: pathlib.Path,
) -> tuple[list[str], bool]:
    """Return the biomarker column names and whether an assessment column exists.

    The assessment column, if present, should always be the last column in the spreadsheet.
    This function separates it from the biomarker columns so new biomarkers can be inserted
    before assessment rather than after it.

    Returns:
        Tuple of (biomarker_columns, has_assessment) where:
        - biomarker_columns: List of biomarker column names (excluding assessment)
        - has_assessment: True if the last column is "assessment"
    """

    odf_path = odf_path.expanduser().resolve()
    if not odf_path.is_file():
        raise FileNotFoundError(f"OpenDocument file not found: {odf_path}")

    try:
        document = load(str(odf_path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid OpenDocument file") from exc

    table: Table | None = None
    for element in document.getElementsByType(Table):
        table = element
        break

    if table is None:
        return [], False

    for row in table.getElementsByType(TableRow):
        cells: list[str] = []

        for cell in row.childNodes:
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or "1")
            text_content = teletype.extractText(cell).strip()
            if text_content:
                cells.extend([text_content] * repeat)

        columns = cells[2:]

        # Check if the last column is "assessment" (case-insensitive).
        if columns and columns[-1].strip().lower() == "assessment":
            return columns[:-1], True

        return columns, False

    return [], False


def extract_lab_names_from_open_document(
    odf_path: pathlib.Path,
) -> set[str]:
    """Return the lab names from the second column (index 1) up to the first empty row."""

    odf_path = odf_path.expanduser().resolve()
    if not odf_path.is_file():
        raise FileNotFoundError(f"OpenDocument file not found: {odf_path}")

    try:
        document = load(str(odf_path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid OpenDocument file") from exc

    table: Table | None = None
    for element in document.getElementsByType(Table):
        table = element
        break

    if table is None:
        return set()

    lab_names: list[str] = []
    for row in table.getElementsByType(TableRow):
        cells: list[str] = []

        for cell in row.childNodes:
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or "1")
            text_content = teletype.extractText(cell).strip()
            cells.extend([text_content] * repeat)

        # Get the second column (index 1)
        if len(cells) > 1:
            lab_name = cells[1].strip()
            if lab_name:
                lab_names.append(lab_name)
            else:
                # Stop at the first empty row in the second column
                break

    return set(lab_names[1:])


def format_ocr_results_for_display(
    ocr_result: dict[str, Any], existing_biomarkers: list[str]
) -> str:
    """Format OCR results in a nice human-readable table.

    Args:
        ocr_result: Dictionary containing OCR results with lab_name, date, and biomarkers
        existing_biomarkers: List of existing biomarker column names from the spreadsheet

    Returns:
        Formatted string with table showing all recognized information
    """
    lines = []
    lines.append("=" * 80)
    lines.append("OCR RESULTS")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Lab name: {ocr_result.get('lab_name', 'N/A')}")
    lines.append(f"Date: {ocr_result.get('date', 'N/A')}")
    lines.append("")

    # Create normalized lookup for matching
    normalized_existing = {name.strip().lower(): name for name in existing_biomarkers}

    # Categorize biomarkers as matched or new
    matched_biomarkers = []
    new_biomarkers = []

    for biomarker in ocr_result.get("biomarkers", []):
        name = biomarker.get("name") or ""
        if not name:
            continue  # Skip biomarkers without names.

        normalized_name = name.strip().lower()

        if normalized_name in normalized_existing:
            matched_name = normalized_existing[normalized_name]
            matched_biomarkers.append((matched_name, biomarker, True))
        else:
            new_biomarkers.append((name, biomarker, False))

    # Print matched biomarkers first
    if matched_biomarkers:
        lines.append("MATCHED TO EXISTING COLUMNS:")
        lines.append("-" * 80)
        lines.append(
            f"{'Biomarker':<30} {'Value':>10} {'Unit':<10} {'Reference Range':<20}"
        )
        lines.append("-" * 80)

        for spreadsheet_name, biomarker, _ in matched_biomarkers:
            value = biomarker.get("value")
            unit = biomarker.get("unit") or ""
            range_lower = biomarker.get("range_lower")
            range_upper = biomarker.get("range_upper")

            # Format value for display.
            value_str = str(value) if value is not None else "N/A"

            # Format reference range.
            if range_lower is not None and range_upper is not None:
                ref_range = f"{range_lower}-{range_upper}"
            elif range_lower is not None:
                ref_range = f"≥{range_lower}"
            elif range_upper is not None:
                ref_range = f"≤{range_upper}"
            else:
                ref_range = ""

            lines.append(
                f"{spreadsheet_name:<30} {value_str:>10} {unit:<10} {ref_range:<20}"
            )
        lines.append("")

    # Print new biomarkers
    if new_biomarkers:
        lines.append("NEW BIOMARKERS (will be added as new columns):")
        lines.append("-" * 80)
        lines.append(
            f"{'Biomarker':<30} {'Value':>10} {'Unit':<10} {'Reference Range':<20}"
        )
        lines.append("-" * 80)

        for name, biomarker, _ in new_biomarkers:
            value = biomarker.get("value")
            unit = biomarker.get("unit") or ""
            range_lower = biomarker.get("range_lower")
            range_upper = biomarker.get("range_upper")

            # Format value for display.
            value_str = str(value) if value is not None else "N/A"

            # Format reference range.
            if range_lower is not None and range_upper is not None:
                ref_range = f"{range_lower}-{range_upper}"
            elif range_lower is not None:
                ref_range = f"≥{range_lower}"
            elif range_upper is not None:
                ref_range = f"≤{range_upper}"
            else:
                ref_range = ""

            lines.append(f"{name:<30} {value_str:>10} {unit:<10} {ref_range:<20}")
        lines.append("")

    # Summary
    lines.append("=" * 80)
    lines.append(
        f"Total biomarkers: {len(matched_biomarkers) + len(new_biomarkers)} "
        f"(matched: {len(matched_biomarkers)}, new: {len(new_biomarkers)})"
    )
    lines.append("=" * 80)

    return "\n".join(lines)


def format_biomarker_column_name(biomarker: dict[str, Any]) -> str:
    """Format a biomarker object into a column name following the convention.

    Format: {name} {{unit}} [{range_low}-{range_upper}]

    Args:
        biomarker: Dictionary containing name, unit, range_lower, range_upper

    Returns:
        Formatted column name string
    """
    name = biomarker.get("name", "")
    unit = biomarker.get("unit", "")
    range_lower = biomarker.get("range_lower")
    range_upper = biomarker.get("range_upper")

    # Start with the name
    parts = [name]

    # Add unit in curly braces if present
    if unit:
        parts.append(f"{{{unit}}}")

    # Add reference range in square brackets if present
    if range_lower is not None and range_upper is not None:
        parts.append(f"[{range_lower}-{range_upper}]")
    elif range_upper is not None:
        parts.append(f"[-{range_upper}]")
    elif range_lower is not None:
        parts.append(f"[{range_lower}-]")

    return " ".join(parts)


def write_results_to_ods(
    odf_path: pathlib.Path,
    ocr_result: dict[str, Any],
    existing_biomarkers: list[str],
    has_assessment: bool,
) -> None:
    """Write OCR results to the ODS file by inserting a new row at position 2.

    Args:
        odf_path: Path to the ODS file
        ocr_result: Dictionary containing OCR results with lab_name, date, and biomarkers
        existing_biomarkers: List of existing biomarker column names from the header
        has_assessment: Whether the spreadsheet has an assessment column as the last column
    """
    odf_path = odf_path.expanduser().resolve()
    if not odf_path.is_file():
        raise FileNotFoundError(f"OpenDocument file not found: {odf_path}")

    # Load the document
    document = load(str(odf_path))

    # Get the first table
    table: Table | None = None
    for element in document.getElementsByType(Table):
        table = element
        break

    if table is None:
        raise ValueError("No table found in ODS file")

    # Get all rows
    rows = table.getElementsByType(TableRow)
    if len(rows) < 1:
        raise ValueError("ODS file must have at least a header row")

    # Parse the biomarkers from the OCR result.
    # Store full biomarker objects (not just values) to preserve units and reference ranges.
    biomarkers_dict = {}
    biomarkers_order = []

    # Create a normalized lookup for existing biomarkers (strip whitespace, lowercase).
    normalized_existing = {name.strip().lower(): name for name in existing_biomarkers}

    for biomarker in ocr_result.get("biomarkers", []):
        name = biomarker.get("name") or ""
        if not name:
            continue  # Skip biomarkers without names.

        # Try to match against existing biomarker names (normalized).
        normalized_name = name.strip().lower()
        if normalized_name in normalized_existing:
            # Use the exact name from the spreadsheet.
            matched_name = normalized_existing[normalized_name]
            biomarkers_dict[matched_name] = biomarker
        else:
            # New biomarker not in existing list - format according to convention.
            formatted_name = format_biomarker_column_name(biomarker)
            biomarkers_dict[formatted_name] = biomarker
            biomarkers_order.append(formatted_name)

    # Determine if there are new biomarkers (maintain order from OCR result)
    new_biomarkers = [
        name for name in biomarkers_order if name not in existing_biomarkers
    ]

    # Detailed column mapping output.
    print("\n" + "=" * 80, file=sys.stderr)
    print("COLUMN MAPPING (writing to ODS)", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"Found {len(biomarkers_dict)} biomarkers from OCR", file=sys.stderr)
    print(
        f"Matched to existing columns: {len(biomarkers_dict) - len(new_biomarkers)}",
        file=sys.stderr,
    )
    print(f"New biomarkers to add: {len(new_biomarkers)}", file=sys.stderr)

    if new_biomarkers:
        print("\nNew biomarker names that will be added as columns:", file=sys.stderr)
        for nb in new_biomarkers:
            print(f"  - {nb}", file=sys.stderr)

    # Show the final column order.
    print("\nFinal column order (after Date and Lab name):", file=sys.stderr)
    all_biomarkers_preview = existing_biomarkers + new_biomarkers
    for idx, col_name in enumerate(all_biomarkers_preview, start=1):
        biomarker_obj = biomarkers_dict.get(col_name)
        if biomarker_obj:
            value = biomarker_obj.get("value", "N/A")
            print(f"  {idx:3}. {col_name:<30} = {value}", file=sys.stderr)
        else:
            print(f"  {idx:3}. {col_name:<30} = (empty)", file=sys.stderr)
    print("=" * 80 + "\n", file=sys.stderr)

    # Update header row if there are new biomarkers.
    if new_biomarkers:
        header_row = rows[0]

        # Find where to insert new columns: before "assessment" if it exists, otherwise before repeated empty columns.
        header_cells = list(header_row.childNodes)
        insert_before = None

        if has_assessment:
            # Find the assessment cell by looking for the cell with "assessment" text.
            # Count from the beginning: Date (0), Lab name (1), then biomarkers start at index 2.
            # Assessment should be at index 2 + len(existing_biomarkers).
            assessment_index = 2 + len(existing_biomarkers)
            if assessment_index < len(header_cells):
                insert_before = header_cells[assessment_index]
        else:
            # No assessment column, insert before repeated empty columns.
            if header_cells:
                last_cell = header_cells[-1]
                # If the last cell has numbercolumnsrepeated, insert new cells before it.
                if last_cell.getAttribute("numbercolumnsrepeated"):
                    insert_before = last_cell

        # Add the new biomarker columns.
        for new_biomarker in new_biomarkers:
            cell = TableCell()
            p = text.P(text=new_biomarker)
            cell.addElement(p)
            if insert_before:
                header_row.insertBefore(cell, insert_before)
            else:
                header_row.addElement(cell)

    # Create the new data row
    new_row = TableRow()

    # First column: Date
    date_cell = TableCell()
    date_value = ocr_result.get("date", "")
    if date_value:
        # Set as date value to avoid the leading single quote.
        date_cell.setAttribute("valuetype", "date")
        date_cell.setAttribute("datevalue", date_value)
    date_p = text.P(text=date_value)
    date_cell.addElement(date_p)
    new_row.addElement(date_cell)

    # Second column: Lab name
    lab_cell = TableCell()
    lab_p = text.P(text=ocr_result.get("lab_name", ""))
    lab_cell.addElement(lab_p)
    new_row.addElement(lab_cell)

    # Add biomarker columns (existing + new).
    all_biomarkers = existing_biomarkers + new_biomarkers
    for biomarker_name in all_biomarkers:
        cell = TableCell()
        biomarker_obj = biomarkers_dict.get(biomarker_name)

        if biomarker_obj is not None:
            # Extract the value from the biomarker object.
            cell_value = biomarker_obj.get("value")

            if cell_value is not None and cell_value != "":
                # Set as numeric value to avoid the leading single quote.
                cell.setAttribute("valuetype", "float")
                cell.setAttribute("value", str(cell_value))
                p = text.P(text=str(cell_value))
                cell.addElement(p)
        # Leave cell empty if no biomarker or value.

        new_row.addElement(cell)

    # Add assessment column if it exists (always last, always empty for OCR results).
    if has_assessment:
        assessment_cell = TableCell()
        new_row.addElement(assessment_cell)

    # Insert the new row at position 1 (right after header, which is at position 0)
    # This will push the existing row 2 down
    table.insertBefore(new_row, rows[1] if len(rows) > 1 else None)

    # Save the document
    document.save(str(odf_path))
    print(f"Results written to {odf_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a PDF of blood test results to PNG images and OCR them with Claude to extract biomarker data."
    )
    parser.add_argument("pdf", type=pathlib.Path, help="Path to the PDF file")
    parser.add_argument(
        "--prefix",
        default="page",
        help="Filename prefix for the generated images (default: page)",
    )
    parser.add_argument(
        "--ods",
        type=pathlib.Path,
        required=True,
        help=(
            "Path to the OpenDocument spreadsheet with existing biomarker column names. "
            "Claude will follow the exact order and spelling of these names during OCR."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_ANTHROPIC_MODEL,
        help=f"Anthropic model to use for OCR (default: {DEFAULT_ANTHROPIC_MODEL})",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Skip OCR and only convert PDF to images",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        help="Output JSON file for OCR results (default: stdout)",
    )

    args = parser.parse_args()

    try:
        biomarker_names, has_assessment = extract_columns_from_open_document_first_row(
            args.ods
        )
        lab_names = extract_lab_names_from_open_document(args.ods)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    try:
        temp_dir = convert_pdf_to_images(args.pdf, args.prefix)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    images = sorted(temp_dir.glob(f"{args.prefix}-*.png"))
    if not images:
        print("No images were generated.", file=sys.stderr)
        raise SystemExit(1)

    if args.no_ocr:
        # Just print the image paths without performing OCR
        print(temp_dir)
        for image_path in images:
            print(image_path)
    else:
        # Perform OCR on the images using Claude (default behavior)
        print("Performing OCR on images with Claude...", file=sys.stderr)
        try:
            result = ocr_images_with_claude(
                images, biomarker_names, lab_names, args.model
            )

            # Display OCR results in a nice format for verification.
            print("\n" + format_ocr_results_for_display(result, biomarker_names))
            print()

            # Write results back to the ODS file.
            write_results_to_ods(args.ods, result, biomarker_names, has_assessment)

            # Output the results (if requested)
            if args.output:
                with open(args.output, "w") as f:
                    json.dump(result, f, indent=2)
                print(f"OCR results also saved to {args.output}", file=sys.stderr)
        except Exception as exc:
            print(f"OCR failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
