# Family Tree Application Walkthrough

## Overview
A Python web application that parses GEDCOM files, visualises them with D3.js,
and generates print-ready A2 PDFs.

## How to Run
1.  **Open Terminal**: Navigate to the `gedcom` directory.
2.  **Run Script**: Double-click `run.bat` or run it from the command line.
    ```powershell
    .\run.bat
    ```
    On first run this creates a `.venv` and installs the dependencies into it,
    so it does not touch whatever Python happens to be on your PATH.
3.  **Open Browser**: Go to `http://localhost:8000`.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
```
The suite runs against `gedcomexample.txt`, the real Ancestry export, because
that is where the awkward cases are. It never touches the network: the map
tests stub the geocoder.

## Features
- **Upload**: Click the large area in the sidebar to upload a GEDCOM file.
- **Visualize**: The family tree is rendered as an ancestor tree.
    - **Click a card**: Opens the life summary.
    - **+ / -**: Expand or collapse that person's ancestors.
    - **> / <**: Show or hide that person's siblings.
    - **Zoom/Pan**: Mouse wheel, and drag on the background.
- **Exports**, from the life summary modal:
    - **A2 Poster (PDF)**: Timeline, vitals, immediate family and a local tree.
    - **Generation Series (ZIP)**: One PDF per ancestral generation.
    - **Image**: High resolution PNG of the A2 layout.
- **Map (UK)**, from the sidebar: plots birth, residence and death places across
  Great Britain and Ireland, with travel lines between them.

## Notes
- The **root person** is whichever INDI appears first in the file. Relationship
  labels on the poster are stated relative to that person by name, since the
  file does not record who is reading it.
- The **map geocodes via Nominatim**, which permits one request per second. A
  large tree takes a couple of minutes the first time; results are cached to
  `app/services/geocode_cache.json` and reused after that.
- The server holds **one tree at a time** in memory. It is built to be run
  locally by one person, not deployed.

## Layout
- `app/`: Backend. `gedcom_parser.py` (parsing and relationships),
  `services/pdf_generator.py` (all PDF layout), `services/geocoding.py`.
- `static/`: Frontend (HTML/CSS/JS).
- `tests/`: pytest suite.
- `scripts/fetch_map.py`: One-off, regenerates `app/services/uk.json` coastline
  data. Needs `requests`, which is not a runtime dependency.
- `sample.ged`, `mock_data.ged`: Small test trees.
