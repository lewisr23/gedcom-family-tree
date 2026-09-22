# Family Tree Viewer

Upload a GEDCOM file, explore the tree in the browser, and export print-ready
A2 posters of it.

**[Live demo](#)** &nbsp;·&nbsp; *(add your Render URL here once deployed)*

---

## Why

Genealogy software is either subscription locked or so dense with features that
using it becomes its own project, and getting a decent printable chart out of
the free options is harder still. This is one page: upload a file, click a
person, click export. No account, no subscription, nothing to configure.

Anyone who can open a browser and find a file should be able to get a framable
poster out of their family history without reading a manual.

## What it does

- **Explore the tree.** Pan and zoom an ancestor chart, expanding parents and
  siblings a branch at a time.
- **Life summaries.** Click anyone for a timeline of births, marriages,
  residences, occupations and deaths, with the source citation behind each one.
- **A2 poster (PDF).** A single sheet with a life timeline, vitals, immediate
  family and a local family tree, drawn to scale for printing.
- **Generation series (ZIP).** One PDF per ancestral generation, each with a
  pedigree chart highlighting that generation and a detail column per person.
- **High resolution PNG.** The poster rendered client side at 4961 x 3508, the
  full A2 print size.
- **Migration map.** Birth, residence and death places plotted across Great
  Britain and Ireland, with lines tracing each person's movements.

## Tech stack

| | |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| PDF generation | ReportLab, drawing directly to an A2 canvas |
| Frontend | Vanilla JavaScript, D3.js, no build step |
| Geocoding | geopy against Nominatim, cached to disk |
| GEDCOM parsing | Hand written, no dependency |
| Tests | pytest, 115 tests, no network access |
| Deployment | Docker, Render |

## Engineering notes

The interesting problems here were not the visualisation. They were the data
and the concurrency.

### Real GEDCOM files are hostile

The format is a 1980s line oriented tree, and every genealogy vendor writes it
slightly differently. Parsing it properly meant tracking nesting depth rather
than matching tag names, because the naive approach silently misattributes
data: source citations belonging to the record were landing on whichever event
happened to be parsed last. On a real 144 person Ancestry export that affected
**90 events**, one of which accumulated 13 citations that belonged elsewhere.

Handling depth correctly, plus `CONC`/`CONT` continuation lines, also recovered
notes that were previously truncated to their first line.

The parser is written from scratch because the available libraries either
assumed a stricter file than real exports produce or pulled in more than the
job needed. It parses 144 individuals and 45 families in 8 ms.

### Place names are worse

Genealogy place strings are typed by hand over decades. A literal geocoder
lookup resolved only **60%** of them, failing on unbalanced brackets
(`Scarborough (Central Ward, North Riding, England`), historic counties that no
longer exist (`Yorkshire West Riding`), joined parishes, and street addresses.

The fix was progressive fallback: try the string as written, then successively
simpler forms, stopping at the first that resolves. That lifted the match rate
to **94%**.

The ordering turned out to matter more than the cleaning. Coarsening from the
left discards the town first, so `South Shields, St Thomas, Westoe, Durham,
England` resolved to the Durham county centroid, and thirteen distinct places
collapsed onto one pin. Asking for the settlement before the county fixed it.

The system deliberately refuses to fall back as far as a country. A pin in the
middle of England is not a weaker answer than a correct one, it is a wrong one,
and on a map it looks equally authoritative.

### Serving more than one person

The first version held a single parsed tree in a module level global. That is
fine for a local tool and breaks immediately when deployed: a second visitor's
upload replaces the first's, and running multiple workers means an export
request can land on a process that has never seen your file.

Trees now live in a TTL'd, size capped session store keyed by a signed HttpOnly
cookie, and are held **in memory only**. Nothing is written to disk, so an
abandoned session leaves nothing behind.

Sizing came from measurement rather than guesswork: the app idles at 57 MB with
FastAPI and ReportLab loaded and costs about 2 MB per held tree, so the session
cap is set to 50 to stay comfortably inside a 512 MB instance. Peak memory
under five loaded trees and three concurrent ZIP exports was **72 MB**.

### Blocking work and a shared rate limit

ReportLab is CPU bound and geocoding is blocking network IO, both of which were
originally running in `async def` handlers and therefore holding the event
loop. A map export could stall every other visitor for minutes. Declaring those
endpoints `def` hands them to FastAPI's threadpool; verified by serving 40 of 40
concurrent requests during an 11 second export, worst case latency 80 ms.

Nominatim permits one request per second, and that budget is shared by every
visitor rather than granted per user, which makes it the first thing to break
under load. Mitigations: a disk cache including negative results, a pre-warmed
cache baked into the image, per caller rate limiting on the map endpoint, and a
pluggable backend so a keyed provider can be swapped in by setting two
environment variables.

### Privacy as a design constraint

A GEDCOM contains names, dates of birth and addresses for people who are often
still living and who never agreed to be on anyone's server. Holding nothing was
both the safer position and the simpler one: no database, no retention policy,
no deletion endpoint to get wrong.

The committed geocode cache holds town names and coordinates only. Address
level entries are stripped before it ships.

## Running locally

```powershell
.\run.bat
```

Creates a `.venv`, installs into it, and serves on http://localhost:8000.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
```

115 tests covering parsing against a real Ancestry export, export correctness,
session isolation, rate limiting, place name normalisation and what the shipped
geocode cache is allowed to contain. The suite stubs the geocoder and never
touches the network.

Test dependencies live in `requirements-dev.txt`, which includes
`requirements.txt`. The deployed image installs only the runtime file.

## Deploying

Push to GitHub, then on Render choose **New > Blueprint** and point it at this
repo. `render.yaml` configures a free tier Docker service. `fly.toml` is
included for Fly.io, which needs a paid plan.

Free tier trade-offs: the instance sleeps after 15 minutes idle and takes
around a minute to wake, and sessions do not survive that sleep. The UI handles
it, expired sessions get a prompt to re-upload rather than a broken page.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SESSION_SECRET` | random per process | Signs the session cookie |
| `SESSION_TTL_SECONDS` | `1800` | How long an idle tree is held |
| `MAX_SESSIONS` | `50` | Concurrent trees before evicting the oldest |
| `MAX_UPLOAD_MB` | `20` | Upload size ceiling |
| `COOKIE_SECURE` | off | Set true behind HTTPS |
| `GEOCODER` | `nominatim` | Or `locationiq` / `mapbox` |
| `GEOCODER_API_KEY` | none | Required by the keyed backends |
| `GEOCODE_CACHE_PATH` | in `app/services/` | Point at a volume to persist |
| `RATE_MAPS` | `5` per hour | Map exports per caller |

## Known limits

- Single process. Sessions live in memory, so scaling out needs Redis behind
  `app/sessions.py`.
- The root person is whichever individual appears first in the file. Relationship
  labels are stated relative to that person by name rather than assuming the
  reader is in the tree.
- The map covers Great Britain and Ireland only.
