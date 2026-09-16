from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from .gedcom_parser import GedcomParser
import json
import re
import unicodedata
from urllib.parse import quote
from app.services.pdf_generator import generate_a2_pdf, generate_generation_zip, generate_map_pdf

app = FastAPI()

# Serve static files (HTML, CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# State persistence.
#
# One parser for the whole process, so the server holds exactly one tree at a
# time and a second upload replaces the first for everybody. That is fine for a
# tool you run locally for yourself; it is not safe to expose to more than one
# user at once. Making it per-session means keying this off a cookie.
parser = GedcomParser()


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


@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.post("/upload")
async def upload_gedcom(file: UploadFile = File(...)):
    global parser
    content = await file.read()
    content_str = content.decode('utf-8', errors='ignore')
    
    # Re-initialize or reuse parser
    parser = GedcomParser()
    data = parser.parse(content_str)
    
    return data

@app.get("/api/export/pdf/{person_id}")
async def export_pdf(person_id: str):
    global parser
    
    # 1. Get Person Data
    person = parser.get_person(person_id)
    if not person:
        return {"error": "Person not found"}
        
    # 2. Get Family Data
    family_data = parser.get_family_context(person_id)
    
    # Enrich with Ancestors for Tree
    ancestors = parser.get_ancestors(person_id, generations=2) # 
    family_data['ancestors_tree'] = ancestors
    
    # Calculate Relationship to Root
    root_id = parser.root_id
    relationship_text = parser.calculate_relationship(root_id, person_id)
    root = parser.get_person(root_id) if root_id else None
    root_name = root.get('name', '') if root else ''

    # 3. Generate PDF
    pdf_buffer = generate_a2_pdf(person, family_data,
                                 relationship=relationship_text,
                                 root_name=root_name)
    
    # 4. Return
    safe_name = person.get('name', 'Unknown').replace('/', '').strip()
    filename = f"{safe_name}_A2_Poster.pdf"
    
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename)}
    )

@app.get("/api/export/generations/{person_id}")
async def export_generations(person_id: str):
    global parser
    
    # 1. Get Person & Data
    person = parser.get_person(person_id)
    if not person: return {"error": "Person not found"}
    
    # 2. Get Ancestors Grouped by Generation
    grouped_ancestors = parser.get_ancestors_by_generation(person_id)
    
    # Enrich with Family Context for detailed PDF
    for gen_idx, people in grouped_ancestors.items():
        for i, p in enumerate(people):
            # Fetch context (partners, children, parents full objects)
            ctx = parser.get_family_context(p['id'])
            # Attach to person object (safe to mutate dict here for export)
            people[i]['_family_context'] = ctx

    # 3. Generate ZIP
    zip_buffer = generate_generation_zip(grouped_ancestors)
    
    safe_name = person.get('name', 'Unknown').replace('/', '').strip()
    filename = f"{safe_name}_Generational_Series.zip"
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(filename)}
    )


@app.get("/api/export/map")
def export_map():
    """Deliberately a sync endpoint, not async.

    Geocoding is blocking network work that runs at one request per second, so
    it takes minutes on a large tree. Declared with `def`, FastAPI runs it in a
    threadpool and the server keeps serving; declared `async def` it would hold
    the event loop for the whole run.
    """
    print("Received map export request")
    global parser

    all_people = list(parser.individuals.values())

    print(f"Exporting map for {len(all_people)} people")

    if not all_people:
        print("No people found to export!")
        raise HTTPException(status_code=400, detail="No data loaded. Please upload a GEDCOM file first.")

    try:
        pdf_buffer = generate_map_pdf(all_people)
        size = pdf_buffer.getbuffer().nbytes
        print(f"Generated PDF Size: {size} bytes")

        pdf_buffer.seek(0)
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition("Family_Map.pdf")}
        )
    except Exception as e:
        print(f"Error generating map: {e}")
        raise HTTPException(status_code=500, detail=f"Could not generate map: {e}")
