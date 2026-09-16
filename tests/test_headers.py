"""Content-Disposition built from GEDCOM names.

Names are file data, so they can carry accents, quotes, semicolons or a stray
newline. A raw f-string either truncated the filename at the first special
character or produced a header Starlette refuses to send.
"""
import pytest

from app.main import content_disposition


def test_plain_name_round_trips():
    header = content_disposition('Joan Trotter_A2_Poster.pdf')
    assert header.startswith('attachment; filename="Joan_Trotter_A2_Poster.pdf"')
    assert "filename*=UTF-8''" in header


def test_accented_name_keeps_a_utf8_spelling_and_an_ascii_fallback():
    header = content_disposition('Siobhán Ó Briain_A2_Poster.pdf')
    # NFKD strips the marks rather than dropping the letters entirely.
    assert 'filename="Siobhan_O_Briain_A2_Poster.pdf"' in header
    assert 'Siobh%C3%A1n' in header


@pytest.mark.parametrize('nasty', [
    'Bad"Quote"Name.pdf',
    'Semi;colon.pdf',
    'Line\r\nBreak.pdf',
    'Comma, Name.pdf',
    'back\\slash.pdf',
    '../../etc/passwd',
])
def test_header_stays_well_formed_for_hostile_names(nasty):
    header = content_disposition(nasty)
    # A header with a bare newline, quote or semicolon in the quoted segment
    # would be split or rejected downstream.
    quoted = header.split('"')[1]
    assert '\r' not in header and '\n' not in header
    assert '"' not in quoted and ';' not in quoted
    assert '/' not in quoted and '\\' not in quoted
    assert header.count('"') == 2


def test_name_that_strips_to_nothing_keeps_a_usable_extension():
    header = content_disposition('日本語.pdf')
    # A bare ".pdf" would download as a hidden, extensionless file.
    assert 'filename="download.pdf"' in header
    assert '%E6%97%A5%E6%9C%AC%E8%AA%9E.pdf' in header


def test_extension_survives_a_hostile_stem():
    header = content_disposition('Bad"Name";.zip')
    assert header.split('"')[1].endswith('.zip')


def test_absurdly_long_name_is_capped():
    header = content_disposition('A' * 500 + '.pdf')
    assert len(header.split('"')[1]) <= 120


def test_headers_are_latin1_encodable():
    """What Starlette actually requires of an outgoing header value."""
    for name in ['Siobhán Ó Briain.pdf', '日本語.pdf', 'Line\nBreak.pdf', 'q"uote.pdf']:
        content_disposition(name).encode('latin-1')
