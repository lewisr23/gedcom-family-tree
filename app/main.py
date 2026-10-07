import os
import re
import unicodedata
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .gedcom_parser import GedcomParser
from . import ratelimit
from .sessions import COOKIE_NAME, SESSION_TTL_SECONDS, store
from app.services.pdf_generator import (
    generate_a2_pdf, generate_generation_zip, generate_map_pdf)
from app.services.timeline import build_timeline

# The coastline the year map is drawn on. Small enough (7 KB) to hand to the
# browser whole rather than reaching for a tile server, which would mean an API
# key, an outbound dependency and someone else's logs of where this family came
# from.
BASEMAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'services', 'uk.json')

# Paths are resolved against this file, not the working directory, so the app
# runs the same from a container, a service manager or run.bat.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, 'static')

# A GEDCOM for a large family runs to a few megabytes; this one is 0.2 MB for
# 144 people. The cap is generous but bounded, because the upload is read into
# memory and an unbounded read is how a process gets killed.
MAX_UPLOAD_BYTES = int(os.environ.get('MAX_UPLOAD_MB', 20)) * 1024 * 1024

# Cookies go out Secure in production. Left off locally because http://localhost
# would otherwise drop them.
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    async def sweeper():
        # Expired trees are dropped on access anyway, but an idle server should
        # not sit holding someone's family data just because nobody came back.
        while True:
            await asyncio.sleep(60)
            store.sweep()

    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def content_disposition(filename):
    """Build an attachment header that cannot be broken by a person's name.

    Names come straight out of the GEDCOM, so they can carry accents, quotes,
    semicolons or even a stray newline. Interpolating one of those raw either
    truncates the filename at the first special character or makes Starlette
    reject the header outright. Per RFC 6266 we send a stripped ASCII fallback
    plus a UTF-8 encoded filename* that keeps the real spelling.
    """
    # Sanitise the stem and the extension separately, so a name written in a
    # non-latin script degrades to "download.pdf" rather than a bare ".pdf".
    stem, dot, ext = filename.rpartition('.')
    if not dot:
        stem, ext = filename, ''

    def to_ascii(part):
        part = unicodedata.normalize('NFKD', part)
        part = part.encode('ascii', 'ignore').decode('ascii')
        return re.sub(r'[^A-Za-z0-9._-]+', '_', part).strip('_')

    ascii_stem = to_ascii(stem)[:110] or 'download'
    ascii_ext = to_ascii(ext)[:10]
    ascii_name = f'{ascii_stem}.{ascii_ext}' if ascii_ext else ascii_stem

    utf8_name = re.sub(r'\s+', ' ', filename).strip()[:120]
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(utf8_name, safe='')}"
    )


def session_id_of(request):
    from .sessions import read_cookie_value
    return read_cookie_value(request.cookies.get(COOKIE_NAME))


def require_tree(request):
    """The caller's parsed tree, or a 409 telling them to upload one.

    409 rather than 404: the person exists conceptually, it is the session that
    has gone. The frontend uses it to prompt for a fresh upload after a tree has
    expired, which is the common case for a tab left open overnight.
    """
    parser = store.get(session_id_of(request))
    if parser is None:
        raise HTTPException(
            status_code=409,
            detail="Your session has expired. Please upload your GEDCOM file again.")
    return parser


def enforce(limiter, request):
    allowed, retry = limiter.check(ratelimit.client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry)})


@app.get("/")
def read_index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))


@app.get("/healthz")
def healthz():
    """Liveness probe for the host platform."""
    return {"status": "ok", "sessions": len(store)}


@app.post("/upload")
async def upload_gedcom(request: Request, response: Response,
                        file: UploadFile = File(...)):
    enforce(ratelimit.uploads, request)

    # Read in chunks so an oversized upload is rejected as it arrives rather
    # than after the whole thing is already resident.
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        chunks.append(chunk)

    if not total:
        raise HTTPException(status_code=400, detail="That file is empty.")

    content = b''.join(chunks).decode('utf-8', errors='ignore')

    parser = GedcomParser()
    data = parser.parse(content)

    if not parser.individuals:
        raise HTTPException(
            status_code=422,
            detail="No people found. Is this a GEDCOM file?")

    # Reuse the caller's session if they have a valid one, so re-uploading does
    # not leak a new entry every time.
    from .sessions import make_cookie_value
    sid = session_id_of(request) or store.new_session()
    store.put(sid, parser)

    response.set_cookie(
        COOKIE_NAME, make_cookie_value(sid),
        max_age=SESSION_TTL_SECONDS, httponly=True,
        samesite='lax', secure=COOKIE_SECURE, path='/')

    return data


@app.delete("/api/session")
def forget_session(request: Request, response: Response):
    """Discard the caller's tree now rather than waiting for it to expire."""
    sid = session_id_of(request)
    if sid:
        store.drop(sid)
    response.delete_cookie(COOKIE_NAME, path='/')
    return {"status": "cleared"}


@app.post("/api/root/{person_id}")
def set_root(request: Request, person_id: str):
    """Make someone else the person the tree is drawn from.

    The parser picks the first INDI in the file as the root, which is whoever
    the exporting software happened to write first and often not the person
    holding the file. Relationship labels and the generation series are both
    stated relative to the root, so this has to be set server side rather than
    only in the browser, otherwise an export would disagree with the screen.

    Scoped to the caller's own parser instance, so changing it affects nobody
    else's session.
    """
    parser = require_tree(request)

    person = parser.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    parser.root_id = person_id
    return {
        "root_id": person_id,
        "name": person.get('name', '').replace('/', '').strip(),
    }


# The export endpoints below are sync on purpose. ReportLab is CPU bound and
# geocoding is blocking network work, so declared `async def` they would hold
# the event loop and stall every other visitor. As plain `def`, FastAPI runs
# them in a threadpool.

@app.get("/api/export/pdf/{person_id}")
def export_pdf(request: Request, person_id: str):
    enforce(ratelimit.exports, request)
    parser = require_tree(request)

    person = parser.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    family_data = parser.get_family_context(person_id)
    family_data['ancestors_tree'] = parser.get_ancestors(person_id, generations=2)

    root_id = parser.root_id
    relationship_text = parser.calculate_relationship(root_id, person_id)
    root = parser.get_person(root_id) if root_id else None
    root_name = root.get('name', '') if root else ''

    pdf_buffer = generate_a2_pdf(person, family_data,
                                 relationship=relationship_text,
                                 root_name=root_name)

    safe_name = person.get('name', 'Unknown').replace('/', '').strip()
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 content_disposition(f"{safe_name}_A2_Poster.pdf")}
    )


@app.get("/api/export/generations/{person_id}")
def export_generations(request: Request, person_id: str):
    enforce(ratelimit.exports, request)
    parser = require_tree(request)

    person = parser.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    grouped_ancestors = parser.get_ancestors_by_generation(person_id)
    for people in grouped_ancestors.values():
        for i, p in enumerate(people):
            people[i]['_family_context'] = parser.get_family_context(p['id'])

    zip_buffer = generate_generation_zip(grouped_ancestors)

    safe_name = person.get('name', 'Unknown').replace('/', '').strip()
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition":
                 content_disposition(f"{safe_name}_Generational_Series.zip")}
    )


@app.get("/api/map/basemap")
def map_basemap():
    """The GB and Ireland coastline, as GeoJSON.

    Static and public: it carries no session data, so it is cacheable and needs
    no tree loaded.
    """
    return FileResponse(
        BASEMAP_PATH,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/map/timeline")
def map_timeline(request: Request):
    """Everyone's dated whereabouts, for the year slider.

    Cheap compared to the PDF exports: it reads the geocode cache rather than
    the geocoder, so it is limited alongside the ordinary exports rather than
    against the hourly map budget, which exists to protect Nominatim quota.
    """
    enforce(ratelimit.exports, request)
    parser = require_tree(request)

    all_people = list(parser.individuals.values())
    if not all_people:
        raise HTTPException(
            status_code=400,
            detail="No data loaded. Please upload a GEDCOM file first.")

    payload = build_timeline(all_people)
    if not payload['span']:
        raise HTTPException(
            status_code=422,
            detail="No dated places in this tree, so there is nothing to plot "
                   "over time.")
    return payload


@app.get("/api/export/map")
def export_map(request: Request):
    enforce(ratelimit.maps, request)
    parser = require_tree(request)

    all_people = list(parser.individuals.values())
    if not all_people:
        raise HTTPException(
            status_code=400,
            detail="No data loaded. Please upload a GEDCOM file first.")

    try:
        pdf_buffer = generate_map_pdf(all_people)
    except Exception as e:
        print(f"Error generating map: {e}")
        raise HTTPException(status_code=500, detail=f"Could not generate map: {e}")

    pdf_buffer.seek(0)
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition("Family_Map.pdf")}
    )
