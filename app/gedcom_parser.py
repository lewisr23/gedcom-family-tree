from typing import Dict, List, Optional
import re

class GedcomParser:
    # Level 1 tags inside an INDI record that open a dated event. MARR appears
    # here as well as in FAM because Ancestry exports put it in both places.
    INDI_EVENT_TAGS = (
        'BIRT', 'DEAT', 'RESI', 'OCCU', 'EDUC', 'BAPM', 'BURI', 'PROB',
        'CHR', 'EVEN', 'MARR', 'CENS', 'IMMI', 'NATU', '_MILT'
    )
    FAM_EVENT_TAGS = ('MARR', 'DIV', 'ENGA')

    def __init__(self):
        self.individuals = {}
        self.families = {}
        self.sources_repo = {}
        self.root_id = None # Store the first parsed individual
        self._nodes_by_id = None # lazily built id -> graph node lookup

    def parse(self, content: str) -> Dict:
        lines = content.split('\n')
        current_record = None
        current_event = None
        event_level = None      # level the open event was declared at
        vital_context = None    # 'birth' | 'death', scoped to the open event
        last_sources = None     # the list the most recent SOUR was appended to
        text_target = None      # (container, key) the most recent text value went to

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            parts = line.split(' ', 2)
            try:
                level = int(parts[0])
            except ValueError:
                continue

            if len(parts) > 1:
                if parts[1].startswith('@') and parts[1].endswith('@'):
                    xref_id = parts[1]
                    tag = parts[2] if len(parts) > 2 else ""
                    value = ""
                else:
                    xref_id = None
                    tag = parts[1]
                    value = parts[2] if len(parts) > 2 else ""
            else:
                continue

            # CONC/CONT continue the last text value written, so they run before
            # any of the scoping rules below.
            if tag in ('CONC', 'CONT'):
                if text_target is not None:
                    container, key = text_target
                    container[key] = container[key] + ('' if tag == 'CONC' else '\n') + value
                continue

            # An event declared at level L owns only the tags nested below it.
            # Anything at or above L closes it. This is what stops record level
            # citations from piling onto whichever event happened to come last.
            if current_event is not None and level <= event_level:
                current_event = None
                event_level = None
                vital_context = None
                last_sources = None

            if level == 0:
                text_target = None
                last_sources = None
                if xref_id:
                    if tag == 'INDI':
                        if not self.root_id: self.root_id = xref_id # First INDI is assumed Root

                        current_record = {
                            'id': xref_id,
                            'type': 'INDI',
                            'name': 'Unknown',
                            'sex': 'U',
                            'birth': '', 'death': '',
                            'birthPlace': '', 'deathPlace': '',
                            'events': [],
                            'notes': [],
                            'sources': []
                        }
                        self.individuals[xref_id] = current_record
                    elif tag == 'FAM':
                        current_record = {'id': xref_id, 'type': 'FAM', 'husb': None, 'wife': None, 'children': [], 'events': []}
                        self.families[xref_id] = current_record
                    elif tag == 'SOUR':
                        current_record = {'id': xref_id, 'type': 'SOUR', 'title': '', 'author': '', 'pub': ''}
                        self.sources_repo[xref_id] = current_record
                    else:
                        current_record = None
                else:
                    current_record = None

            elif current_record:
                in_event = current_event is not None and level == event_level + 1

                # Handle INDI
                if current_record['type'] == 'INDI':
                    if tag == 'NAME' and level == 1:
                        current_record['name'] = value.replace('/', '').strip()
                    elif tag == 'SEX' and level == 1:
                        current_record['sex'] = value.strip()

                    # --- Event Handling ---
                    elif tag in self.INDI_EVENT_TAGS and level == 1:
                        current_event = {
                            'type': tag,
                            'value': value.strip(),
                            'date': '',
                            'place': '',
                            'notes': [],
                            'sources': []
                        }
                        current_record['events'].append(current_event)
                        event_level = level
                        last_sources = None
                        text_target = (current_event, 'value')

                        if tag == 'BIRT':
                            vital_context = 'birth'
                        elif tag == 'DEAT':
                            vital_context = 'death'
                        else:
                            vital_context = None

                    elif tag == 'DATE' and in_event:
                        current_event['date'] = value.strip()
                        text_target = (current_event, 'date')
                        if vital_context == 'birth': current_record['birth'] = value.strip()
                        if vital_context == 'death': current_record['death'] = value.strip()

                    elif tag == 'PLAC' and in_event:
                        current_event['place'] = value.strip()
                        text_target = (current_event, 'place')
                        if vital_context == 'birth': current_record['birthPlace'] = value.strip()
                        if vital_context == 'death': current_record['deathPlace'] = value.strip()

                    elif tag == 'NOTE':
                        target = current_event['notes'] if in_event else (current_record['notes'] if level == 1 else None)
                        if target is not None:
                            target.append(value.strip())
                            text_target = (target, len(target) - 1)

                    elif tag == 'SOUR':
                        # A citation nested under the open event belongs to it,
                        # anything else is a record level citation.
                        target_list = current_event['sources'] if in_event else current_record['sources']
                        target_list.append({'id': value.strip(), 'page': ''})
                        last_sources = target_list

                    elif tag == 'PAGE':
                        if last_sources:
                            last_sources[-1]['page'] = value.strip()
                            text_target = (last_sources[-1], 'page')

                # Handle SOUR (Repo)
                elif current_record['type'] == 'SOUR':
                    if tag == 'TITL':
                        current_record['title'] = value.strip()
                        text_target = (current_record, 'title')
                    elif tag == 'AUTH':
                        current_record['author'] = value.strip()
                        text_target = (current_record, 'author')
                    elif tag == 'PUBL':
                        current_record['pub'] = value.strip()
                        text_target = (current_record, 'pub')

                elif current_record['type'] == 'FAM':
                    if tag == 'HUSB' and level == 1: current_record['husb'] = value.strip()
                    elif tag == 'WIFE' and level == 1: current_record['wife'] = value.strip()
                    elif tag == 'CHIL' and level == 1: current_record['children'].append(value.strip())
                    elif tag in self.FAM_EVENT_TAGS and level == 1:
                        current_event = {
                            'type': tag,
                            'value': 'Marriage' if tag == 'MARR' else tag,
                            'date': '',
                            'place': '',
                            'sources': []
                        }
                        current_record['events'].append(current_event)
                        event_level = level
                        last_sources = None
                    elif tag == 'DATE' and in_event:
                        current_event['date'] = value.strip()
                        text_target = (current_event, 'date')
                    elif tag == 'PLAC' and in_event:
                        current_event['place'] = value.strip()
                        text_target = (current_event, 'place')
                    elif tag == 'SOUR' and in_event:
                        current_event['sources'].append({'id': value.strip(), 'page': ''})
                        last_sources = current_event['sources']
                    elif tag == 'PAGE' and last_sources:
                        last_sources[-1]['page'] = value.strip()
                        text_target = (last_sources[-1], 'page')

        self._nodes_by_id = None
        self.graph_data = self._build_graph()
        return self.graph_data

    @staticmethod
    def _year_of(date_str):
        m = re.search(r'\d{4}', date_str or '')
        return m.group(0) if m else None

    def _find_matching_marriage(self, individual, fam_event):
        """An INDI level MARR describing the same union as a FAM level one.

        Matched on year, which is as precise as these records get: the two
        entries routinely disagree on day and month ('January 1911' vs
        'Jan 1911'), so anything stricter would miss the overlap.
        """
        year = self._year_of(fam_event.get('date'))
        if not year:
            return None
        for ev in individual['events']:
            if ev.get('_matched'):
                continue  # already claimed by another family in the same year
            if ev['type'] == 'MARR' and self._year_of(ev.get('date')) == year:
                ev['_matched'] = True
                return ev
        return None

    def _build_graph(self):
        nodes = []
        links = []

        child_to_parents = {}
        pending_marriages = []  # (person_id, spouse_record_or_None, fam_event)

        # 1. First Pass: Map relationships
        for fam_id, fam in self.families.items():
            father_id = fam.get('husb')
            mother_id = fam.get('wife')
            fam_events = fam.get('events', [])
            
            # Collect marriage events; they are applied after this loop so that
            # unions naming both spouses are matched up first.
            for ev in fam_events:
                if ev['type'] == 'MARR':
                    for this_id, other_id in ((father_id, mother_id), (mother_id, father_id)):
                        if not this_id or this_id not in self.individuals:
                            continue
                        pending_marriages.append((this_id, self.individuals.get(other_id) if other_id else None, ev))

            children = fam.get('children', [])
            for child_id in children:
                if child_id not in child_to_parents:
                    child_to_parents[child_id] = {'f': None, 'm': None, 'fam': fam_id}
                if father_id: child_to_parents[child_id]['f'] = father_id
                if mother_id: child_to_parents[child_id]['m'] = mother_id
                
                # Add Child Birth Event to Parents (Important for timeline)
                if child_id in self.individuals:
                    child = self.individuals[child_id]
                    child_name = child.get('name', 'Unknown')
                    child_birth = child.get('birth', '')
                    
                    birth_event = {
                        'type': 'CHIL_BIRTH',
                        'value': child_name, 
                        'date': child_birth,
                        'place': child.get('birthPlace', ''),
                        'sources': []
                    }
                    
                    if father_id and father_id in self.individuals:
                        self.individuals[father_id]['events'].append(birth_event)
                    if mother_id and mother_id in self.individuals:
                        self.individuals[mother_id]['events'].append(birth_event)

        # Apply marriages, naming the spouse where we can. Unions with a known
        # spouse go first so they claim the matching INDI level MARR rather than
        # losing it to a one sided family record for the same year.
        pending_marriages.sort(key=lambda m: m[1] is None)
        for person_id, spouse, ev in pending_marriages:
            label = f"Marriage to {spouse['name']}" if spouse else "Marriage"
            person = self.individuals[person_id]

            # The INDI record often carries its own MARR for the same union.
            # Enrich that one instead of adding a second entry for one event.
            existing = self._find_matching_marriage(person, ev)
            if existing:
                existing['value'] = label
                if not existing.get('date'): existing['date'] = ev.get('date', '')
                if not existing.get('place'): existing['place'] = ev.get('place', '')
                continue

            # A family record with only one spouse in it, for a year this person
            # is already married in, is the same union recorded twice. Adding a
            # bare "Marriage" beside the named one would just be noise.
            if spouse is None and self._year_of(ev.get('date')):
                year = self._year_of(ev.get('date'))
                if any(e['type'] == 'MARR' and self._year_of(e.get('date')) == year
                       for e in person['events']):
                    continue

            marr_event = ev.copy()
            marr_event['value'] = label
            marr_event['_matched'] = True
            person['events'].append(marr_event)

        def get_year(date_str):
            if not date_str: return ""
            import re
            m = re.search(r'\d{4}', date_str)
            return m.group(0) if m else ""

        for indi_id, indi in self.individuals.items():
            parents = child_to_parents.get(indi_id, {})
            b_year = get_year(indi.get('birth'))
            d_year = get_year(indi.get('death'))
            life_span = f"{b_year} - {d_year}" if (b_year or d_year) else ""
            
            siblings = []
            if parents.get('fam'):
                fam = self.families[parents['fam']]
                for sib_id in fam.get('children', []):
                    if sib_id != indi_id and sib_id in self.individuals:
                        siblings.append(sib_id)

            # Enrich Sources
            enriched_sources = []
            for s in indi.get('sources', []):
                repo = self.sources_repo.get(s['id'])
                title = repo.get('title', 'Unknown Source') if repo else 'Unknown Source'
                enriched_sources.append({'id': s['id'], 'title': title, 'page': s.get('page', '')})
            
            # Enrich Event Sources
            enriched_events = []
            for ev in indi.get('events', []):
                new_ev = ev.copy()
                new_ev.pop('_matched', None)  # internal bookkeeping, not output
                ev_sources = []
                for s in ev.get('sources', []):
                    repo = self.sources_repo.get(s['id'])
                    title = repo.get('title', 'Unknown Source') if repo else 'Unknown Source'
                    ev_sources.append({'id': s['id'], 'title': title, 'page': s.get('page', '')})
                new_ev['sources'] = ev_sources
                enriched_events.append(new_ev)

            nodes.append({
                'id': indi_id,
                'name': indi.get('name', 'Unknown'),
                'sex': indi.get('sex', 'U'),
                'lifeSpan': life_span,
                'type': 'person',
                'fatherId': parents.get('f'),
                'motherId': parents.get('m'),
                'siblingIds': siblings,
                'birthDate': indi.get('birth'),
                'birthPlace': indi.get('birthPlace'),
                'deathDate': indi.get('death'),
                'deathPlace': indi.get('deathPlace'),
                'events': enriched_events,
                'sources': enriched_sources
            })

        for fam_id, fam in self.families.items():
            fam_node_id = f"FAM_{fam_id}"
            nodes.append({'id': fam_node_id, 'name': '', 'type': 'family'})
            if fam.get('husb') and fam['husb'] in self.individuals: 
                links.append({'source': fam['husb'], 'target': fam_node_id, 'type': 'spouse'})
            if fam.get('wife') and fam['wife'] in self.individuals: 
                links.append({'source': fam['wife'], 'target': fam_node_id, 'type': 'spouse'})
            for child_id in fam.get('children', []):
                if child_id in self.individuals:
                    links.append({'source': fam_node_id, 'target': child_id, 'type': 'child'})

        return {'nodes': nodes, 'links': links}

    def _node_index(self):
        if getattr(self, '_nodes_by_id', None) is None:
            graph = getattr(self, 'graph_data', None)
            self._nodes_by_id = {n['id']: n for n in graph['nodes']} if graph else {}
        return self._nodes_by_id

    def get_person(self, person_id):
        """A copy of the person's graph node.

        Callers (the export paths in particular) decorate what they get back
        with presentation fields such as _side and _family_context. Handing out
        the live node let those writes accumulate on parser state and, because
        the contexts point at each other, made the graph unserialisable.
        """
        node = self._node_index().get(person_id)
        if node is not None:
            return dict(node)
        indi = self.individuals.get(person_id)
        return dict(indi) if indi is not None else None

    def get_family_context(self, person_id):
        context = {
            'parents': [],
            'spouses': [],
            'children': [],
            'siblings': [] 
        }
        
        get_p = self.get_person

        for fam in self.families.values():
            if person_id in fam.get('children', []):
                if fam.get('husb'): 
                    p = get_p(fam['husb'])
                    if p: context['parents'].append(p)
                if fam.get('wife'):
                    p = get_p(fam['wife'])
                    if p: context['parents'].append(p)
                for child_id in fam.get('children', []):
                    if child_id != person_id:
                        p = get_p(child_id)
                        if p: context['siblings'].append(p)
        
        for fam in self.families.values():
            is_spouse = False
            spouse_id = None
            if fam.get('husb') == person_id:
                is_spouse = True
                spouse_id = fam.get('wife')
            elif fam.get('wife') == person_id:
                is_spouse = True
                spouse_id = fam.get('husb')
            
            if is_spouse:
                if spouse_id:
                    p = get_p(spouse_id)
                    if p: context['spouses'].append(p)
                for child_id in fam.get('children', []):
                    p = get_p(child_id)
                    if p: context['children'].append(p)
                    
        return context

    def get_ancestors(self, person_id, generations=3):
        """Returns a nested dict of ancestors up to N generations."""
        if generations <= 0: return None
        
        person = self.get_person(person_id)
        if not person: return None
        
        # Get Parents via family where person is a child
        matched_fam = None
        for fam in self.families.values():
            if person_id in fam.get('children', []):
                matched_fam = fam
                break
        
        father = None
        mother = None
        
        if matched_fam:
            if matched_fam.get('husb'):
                father = self.get_ancestors(matched_fam['husb'], generations - 1)
            if matched_fam.get('wife'):
                mother = self.get_ancestors(matched_fam['wife'], generations - 1)
                
        return {
            'id': person_id,
            'name': person.get('name', 'Unknown'),
            'birth': person.get('birthDate', ''),
            'death': person.get('deathDate', ''),
            'father': father,
            'mother': mother
        }

    def calculate_relationship(self, root_id, target_id):
        """
        Calculates relationship string (e.g. 'Mother', 'Son')
        """
        if not root_id or not target_id: return ""
        if root_id == target_id: return "Self"
        
        # Build Adjacency List for BFS
        adj = {}
        for fam in self.families.values():
            f = fam.get('husb')
            m = fam.get('wife')
            kids = fam.get('children', [])
            
            # Spouses
            if f and m:
                if f not in adj: adj[f] = []
                if m not in adj: adj[m] = []
                adj[f].append((m, 'Spouse'))
                adj[m].append((f, 'Spouse'))
            
            # Parents -> Children
            for k in kids:
                if f:
                    if f not in adj: adj[f] = []
                    if k not in adj: adj[k] = []
                    adj[f].append((k, 'Child'))
                    adj[k].append((f, 'Father'))
                if m:
                    if m not in adj: adj[m] = []
                    if k not in adj: adj[k] = []
                    adj[m].append((k, 'Child'))
                    adj[k].append((m, 'Mother'))
                    
        # BFS
        queue = [(root_id, [])]
        visited = {root_id}
        
        while queue:
            curr, path = queue.pop(0)
            if curr == target_id:
                return self._describe_path(path)
            
            # Deep enough for the whole of a typical tree. Ancestor lines here
            # already run eight generations, which the old cap of 5 cut off.
            if len(path) > 12: continue
            
            if curr in adj:
                for neighbor, rel_type in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, path + [rel_type]))
                        
        return "" # No logical relationship found within depth

    def _describe_path(self, path):
        if not path: return ""
        
        # Direct Ancestors
        if all(p in ['Father', 'Mother'] for p in path):
            gens = len(path)
            last = path[-1]
            if gens == 1: return last
            if gens == 2: return "Grand" + last.lower()
            if gens == 3: return "Great-Grand" + last.lower()
            # Matches the wording the generation series pages use for their
            # titles, rather than stopping at Great-Great for everything above.
            return f"{gens - 2}x Great-Grand{last.lower()}"

        # Direct Descendants
        if all(p == 'Child' for p in path):
            gens = len(path)
            if gens == 1: return "Child" # Could refine to Son/Daughter if sex known
            if gens == 2: return "Grandchild"
            if gens == 3: return "Great-Grandchild"
            return f"{gens - 2}x Great-Grandchild"
            
        # Siblings
        if len(path) == 2 and path[0] in ['Father', 'Mother'] and path[1] == 'Child':
            return "Sibling"
            
        if path == ['Spouse']: return "Spouse"
        
        return "Relative"

    def get_ancestors_by_generation(self, root_id):
        """
        Returns {generation_index: [list_of_person_objects]}
        Person objects will be enriched with '_side': 'Maternal' | 'Paternal' | 'Direct'
        """
        generations = {}
        
        # Queue: (person_id, depth, side)
        # side: 'Direct' (for root), 'Paternal', 'Maternal'
        queue = [(root_id, 0, 'Direct')]
        visited = {} # Use dict to store side for consistency? Or just set logic.
        visited[root_id] = 'Direct'
        
        while queue:
            pid, depth, side = queue.pop(0)
            person = self.get_person(pid)
            if not person: continue
            
            # Create a shallow copy or modify? ideally copy to avoid polluting global state
            # But the parser currently returns refs. We'll attach transient attribute.
            person['_side'] = side
            
            if depth not in generations: generations[depth] = []
            
            # Deduplication in list?
            # If same person is both maternal and paternal (cousin marriage), first win or list both?
            # Simple approach: simple visited check avoids dupes in traversal, but we might want them in tree?
            # Existing logic had simple visited. We'll stick to that.
            generations[depth].append(person)
            
            # Find parents
            # Look up family where this person is a child
            father_id = None
            mother_id = None
            
            found_fam = False
            for fam in self.families.values():
                if pid in fam.get('children', []):
                    father_id = fam.get('husb')
                    mother_id = fam.get('wife')
                    found_fam = True
                    
                    # --- NEW: Add Siblings to Gen 0 if this is Root ---
                    if depth == 0:
                        siblings = fam.get('children', [])
                        for sib_id in siblings:
                            if sib_id != pid and sib_id not in visited:
                                sib = self.get_person(sib_id)
                                if sib:
                                    sib['_side'] = 'Sibling'
                                    generations[0].append(sib)
                                    visited[sib_id] = 'Sibling'
                    
                    break
            
            if found_fam:
                # If current is Root, split sides.
                # If current is Paternal, parents are Paternal.
                # If current is Maternal, parents are Maternal.
                
                f_side = side
                m_side = side
                
                if depth == 0:
                    f_side = 'Paternal'
                    m_side = 'Maternal'
                    
                if father_id and father_id not in visited:
                    visited[father_id] = f_side
                    queue.append((father_id, depth + 1, f_side))
                
                if mother_id and mother_id not in visited:
                    visited[mother_id] = m_side
                    queue.append((mother_id, depth + 1, m_side))
                    
        return generations
