# Family Tree Viewer

Upload a GEDCOM file, explore the tree in the browser, and print proper
wall-sized posters of it.

## Why

Genealogy software is either expensive, subscription locked, or so dense with
features that using it becomes its own project. Getting a decent printable chart
out of the free options is harder still. I wanted something you could open,
point at a file, and click one button to get a poster worth framing, without
needing to understand what a GEDCOM is or read a manual to find the export.

So the whole thing is one page: upload, click a person, click export. No
accounts, no subscription, nothing to configure.

## What it does

- **Explore the tree**: an ancestor chart you can pan and zoom, expanding
  parents and siblings a branch at a time.
- **Life summary**: click anyone for their timeline of births, marriages,
  residences, occupations and deaths, with the source citations behind each one.
- **A2 poster (PDF)**: a single page with a life timeline, vitals, immediate
  family and a local family tree.
- **Generation series (ZIP)**: one PDF per ancestral generation, each with a
  pedigree chart highlighting that generation, plus a detail column per person.
- **Map**: birth, residence and death places plotted across Great Britain and
  Ireland, with lines tracing each person's movements.

## Tech stack

- **Backend**: Python, FastAPI, Uvicorn
- **PDF generation**: ReportLab, drawing directly to an A2 canvas
- **Frontend**: vanilla JavaScript with D3.js, no build step
- **Geocoding**: geopy against Nominatim, cached to disk
- **GEDCOM parsing**: hand rolled, no dependency
- **Tests**: pytest

The parser is written from scratch because the available libraries either
assumed a stricter GEDCOM than real exports produce, or pulled in more than the
job needed. Ancestry files in particular nest citations and continuation lines
in ways that a lenient, level aware reader handles better.

## Running it

```powershell
.\run.bat
```

First run creates a `.venv`, installs the dependencies into it, and starts the
server. Then open http://localhost:8000 and upload a `.ged` file.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
```

The suite never touches the network. It runs against a real Ancestry export
when one is present and skips those tests otherwise.

## Notes and limits

- The **root person** is whichever individual appears first in the file.
  Relationship labels are stated relative to that person by name, since a
  GEDCOM does not record who is reading it.
- The **map** geocodes through Nominatim, which allows one lookup per second.
  A large tree takes a couple of minutes the first time, then results are
  cached and reuse is instant.
- The server holds **one tree at a time**. It is built to be run locally by one
  person, not deployed for several at once.
- **GEDCOM files are personal data.** A family tree carries names, dates of
  birth and addresses for people who may well be living. `.gitignore` excludes
  `*.ged` and the sample export for that reason. Keep it that way.

See [walkthrough.md](walkthrough.md) for more detail on the interface.
