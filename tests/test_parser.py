"""Parser behaviour, pinned against the real Ancestry export.

Each test here covers a defect that shipped: citations attaching to whichever
event came last, sub-record tags overwriting their parent event, continuation
lines being dropped, and marriages appearing twice.
"""
import json
import re

from app.gedcom_parser import GedcomParser


SIMPLE = """0 HEAD
0 @I1@ INDI
1 NAME John /Smith/
2 SOUR @S1@
3 PAGE Name citation
1 SEX M
1 BIRT
2 DATE 1 Jan 1900
2 PLAC Newcastle, England
2 SOUR @S2@
3 PAGE Birth citation
1 DEAT
2 DATE 1970
2 PLAC Durham, England
1 SOUR @S3@
2 PAGE Record level citation
2 DATE 1985
1 NOTE First line
2 CONT Second line
2 CONC  continued
0 @S1@ SOUR
1 TITL Name Index
0 @S2@ SOUR
1 TITL Birth Index
0 @S3@ SOUR
1 TITL General Record
"""


def parse(text):
    p = GedcomParser()
    p.parse(text)
    return p


def test_citations_land_on_their_own_event():
    p = parse(SIMPLE)
    indi = p.individuals['@I1@']
    birth = next(e for e in indi['events'] if e['type'] == 'BIRT')
    death = next(e for e in indi['events'] if e['type'] == 'DEAT')

    assert [s['id'] for s in birth['sources']] == ['@S2@']
    # The record level SOUR that follows DEAT belongs to the record, not to
    # the death event that happened to be open just before it.
    assert death['sources'] == []
    assert [s['id'] for s in indi['sources']] == ['@S1@', '@S3@']


def test_nested_date_does_not_overwrite_the_enclosing_event():
    p = parse(SIMPLE)
    indi = p.individuals['@I1@']
    # '2 DATE 1985' sits under the record level citation, not under DEAT.
    assert indi['death'] == '1970'
    assert indi['birth'] == '1 Jan 1900'
    assert indi['birthPlace'] == 'Newcastle, England'
    assert indi['deathPlace'] == 'Durham, England'


def test_cont_and_conc_are_joined():
    p = parse(SIMPLE)
    assert p.individuals['@I1@']['notes'] == ['First line\nSecond line continued']


def test_blank_and_malformed_lines_are_skipped():
    p = parse("\n\nnot a gedcom line\n0 @I1@ INDI\n1 NAME A /B/\n")
    assert p.individuals['@I1@']['name'] == 'A B'


# --- against the real export -------------------------------------------------

def test_real_file_shape(parsed):
    assert len(parsed.individuals) == 144
    assert len(parsed.families) == 45


def test_no_event_collects_impossible_numbers_of_citations(parsed):
    worst = max(len(e.get('sources', []))
                for i in parsed.individuals.values() for e in i['events'])
    # Was 13 when record level citations piled onto the last event seen.
    assert worst <= 9


def test_record_level_citations_outnumber_event_level(parsed):
    record = sum(len(i['sources']) for i in parsed.individuals.values())
    event = sum(len(e.get('sources', []))
                for i in parsed.individuals.values() for e in i['events'])
    # Ancestry files are citation heavy at the record level; if this inverts,
    # citations are being pushed down onto events again.
    assert record > event


def test_residence_notes_are_captured(parsed):
    noted = [e for i in parsed.individuals.values() for e in i['events']
             if e['type'] == 'RESI' and e.get('notes')]
    assert len(noted) > 100
    assert any('Relation to Head' in n for e in noted for n in e['notes'])


def test_multiline_note_survives(parsed):
    notes = [n for i in parsed.individuals.values() for n in i['notes'] if '\n' in n]
    assert notes, "expected at least one note assembled from CONT lines"
    assert any('Garlands' in n for n in notes)


def test_no_duplicate_marriage_in_the_same_year(parsed):
    def year(s):
        m = re.search(r'\d{4}', s or '')
        return m.group(0) if m else None

    for node in parsed.graph_data['nodes']:
        if node['type'] != 'person':
            continue
        years = [year(e['date']) for e in node['events']
                 if e['type'] == 'MARR' and year(e['date'])]
        assert len(years) == len(set(years)), f"{node['name']} married twice in a year"


def test_marriages_name_the_spouse_when_known(parsed):
    marriages = [e for n in parsed.graph_data['nodes'] if n['type'] == 'person'
                 for e in n['events'] if e['type'] == 'MARR']
    assert marriages
    assert not any('Unknown' in (e['value'] or '') for e in marriages)


def test_internal_flags_do_not_reach_the_output(parsed):
    for node in parsed.graph_data['nodes']:
        for event in node.get('events', []):
            assert '_matched' not in event
        assert '_is_birth_context' not in node
        assert '_is_death_context' not in node


def test_graph_is_json_serialisable(parsed):
    json.dumps(parsed.graph_data)


def test_deep_ancestors_get_a_real_relationship_label(parsed):
    labels = {parsed.calculate_relationship(parsed.root_id, n['id'])
              for n in parsed.graph_data['nodes'] if n['type'] == 'person'}
    # The old depth cap of 5 returned an empty string past great-great.
    assert any(l.endswith('x Great-Grandfather') for l in labels)
    assert 'Great-Great-Grandfather' not in labels, "should use the Nx wording"
