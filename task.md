# Tasks

- [x] Create `requirements.txt`
- [x] Create `run.bat`
- [x] Implement `app/gedcom_parser.py`
- [x] Implement `app/main.py`
- [x] Implement `static/index.html`
- [x] Implement `static/style.css`
- [x] Implement `static/script.js` (Visualizations)
- [x] Create `sample.ged` for testing
- [x] Run the application
- [ ] **Pivot: Server-Side PDF Generation**
    - [ ] Add `reportlab` to requirements.txt
    - [ ] Create `app/services/pdf_generator.py` (Layout & Drawing Logic)
    - [ ] Add `GET /api/export/{person_id}` endpoint to `main.py`
    - [ ] Update frontend to link to the new API endpoint
    - [ ] Verification: Test download and PDF quality
