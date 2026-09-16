from reportlab.lib import colors
from reportlab.lib.pagesizes import A2, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
import os
import datetime
from app.services.geocoding import GeocodingService

# Configuration
PAGE_SIZE = landscape(A2)
WIDTH, HEIGHT = PAGE_SIZE
BG_COLOR = colors.HexColor('#f7e7ce')
TEXT_COLOR = colors.HexColor('#1a1a1a')
ACCENT_COLOR = colors.HexColor('#333333')

# Fonts
FONT_REG = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

try:
    base_font_path = "static/fonts"
    if os.path.exists(os.path.join(base_font_path, "CormorantSC-Regular.ttf")):
        pdfmetrics.registerFont(TTFont('Cormorant-Regular', os.path.join(base_font_path, "CormorantSC-Regular.ttf")))
        pdfmetrics.registerFont(TTFont('Cormorant-Bold', os.path.join(base_font_path, "CormorantSC-Bold.ttf")))
        FONT_REG = "Cormorant-Regular"
        FONT_BOLD = "Cormorant-Bold"
except Exception as e:
    print(f"Font loading error: {e}")

def draw_background(c):
    c.setFillColor(BG_COLOR)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

def draw_header(c, person, rel_text="", root_name=""):
    name = person.get('name', 'Unknown').replace('/', '').strip()
    dates = person.get('lifeSpan', '')
    
    # Margin Hug: 50
    x_pos = 50 
    y_pos = HEIGHT - 80
    
    c.setFillColor(TEXT_COLOR)
    c.setFont(FONT_BOLD, 48)
    c.drawString(x_pos, y_pos, name)
    
    if rel_text:
        text_width = c.stringWidth(name, FONT_BOLD, 48)
        c.setFont(FONT_REG, 24)
        c.setFillColor(colors.HexColor('#555555'))
        # Named relative to the root person rather than to "you": the root is
        # simply the first INDI in the file, which is not necessarily the reader.
        label = f"{rel_text} of {root_name}" if root_name else rel_text
        c.drawString(x_pos + text_width + 20, y_pos, f"({label})")

    if dates:
        c.setFont(FONT_REG, 24)
        c.setFillColor(colors.HexColor('#555555'))
        c.drawString(x_pos, y_pos - 40, dates)

def parse_year(date_str):
    if not date_str: return None
    import re
    m = re.search(r'\d{4}', date_str)
    if m: return int(m.group(0))
    return None

# shapes
def draw_star(c, x, y, size=6):
    p = c.beginPath()
    p.moveTo(x, y + size)
    p.lineTo(x + size/4, y + size/4)
    p.lineTo(x + size, y)
    p.lineTo(x + size/4, y - size/4)
    p.lineTo(x, y - size)
    p.lineTo(x - size/4, y - size/4)
    p.lineTo(x - size, y)
    p.lineTo(x - size/4, y + size/4)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

def draw_cross(c, x, y, size=5):
    c.setLineWidth(2)
    c.line(x - size, y - size, x + size, y + size)
    c.line(x - size, y + size, x + size, y - size)
    c.setLineWidth(1)

def draw_house(c, x, y, size=6):
    p = c.beginPath()
    p.moveTo(x, y + size)
    p.lineTo(x + size, y)
    p.lineTo(x + size, y - size)
    p.lineTo(x - size, y - size)
    p.lineTo(x - size, y)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

def draw_diamond(c, x, y, size=5):
    p = c.beginPath()
    p.moveTo(x, y + size)
    p.lineTo(x + size, y)
    p.lineTo(x, y - size)
    p.lineTo(x - size, y)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

def get_event_config(type_code):
    mapping = {
        'BIRT': ('Born', 'star'),
        'DEAT': ('Died', 'cross'),
        'MARR': ('Marriage', 'heart'), 
        'RESI': ('Residence', 'house'),
        'OCCU': ('Occupation', 'circle'),
        'EDUC': ('Education', 'circle'),
        'CHIL_BIRTH': ('Child Born', 'star-small'),
        'BURI': ('Burial', 'cross'),
        'BAPM': ('Baptism', 'star'),
        'PROB': ('Probate', 'circle'),
        '_MILT': ('Military', 'circle')
    }
    return mapping.get(type_code, (type_code.capitalize(), 'circle'))

def draw_timeline(c, person):
    y_main_line = HEIGHT - 450
    margin_x = 50 
    line_width = WIDTH - (2 * margin_x)
    
    events = person.get('events', [])
    valid_events = []
    
    birth_year = parse_year(person.get('birthDate'))
    death_year = parse_year(person.get('deathDate'))
    
    existing_types = [e.get('type') for e in events]
    if birth_year and 'BIRT' not in existing_types: 
        valid_events.append({'year': birth_year, 'type': 'BIRT', 'place': person.get('birthPlace')})
    if death_year and 'DEAT' not in existing_types: 
        valid_events.append({'year': death_year, 'type': 'DEAT', 'place': person.get('deathPlace')})
    
    for e in events:
        y = parse_year(e.get('date'))
        if y:
            is_dupe = False
            for ve in valid_events:
                 if ve['year'] == y and ve['type'] == e.get('type'):
                     is_dupe = True
            if not is_dupe:
                valid_events.append({
                    'year': y, 
                    'type': e.get('type'), 
                    'value': e.get('value'),
                    'place': e.get('place')
                })
            
    valid_events.sort(key=lambda x: x['year'])
    if not valid_events: return

    min_year = valid_events[0]['year'] - 5
    max_year = valid_events[-1]['year'] + 5
    year_range = max_year - min_year
    if year_range < 10: year_range = 10
    
    c.setStrokeColor(TEXT_COLOR)
    c.setLineWidth(3)
    c.line(margin_x, y_main_line, WIDTH - margin_x, y_main_line)
    
    levels = [1, -1, 2, -2, 3, -3, 4, -4]
    
    for i, event in enumerate(valid_events):
        rel_pos = (event['year'] - min_year) / year_range
        x = margin_x + (rel_pos * line_width)
        
        level = levels[i % len(levels)] 
        y_offset = level * 60
        
        label_text, shape = get_event_config(event['type'])
        
        c.setFillColor(TEXT_COLOR)
        c.setStrokeColor(TEXT_COLOR)
        
        if shape == 'star': draw_star(c, x, y_main_line)
        elif shape == 'cross': draw_cross(c, x, y_main_line)
        elif shape == 'house': draw_house(c, x, y_main_line)
        elif shape == 'heart': draw_diamond(c, x, y_main_line) 
        else: c.circle(x, y_main_line, 4, fill=1, stroke=0)
        
        c.setLineWidth(0.5)
        c.line(x, y_main_line, x, y_main_line + y_offset)
        
        text_base_y = y_main_line + y_offset + (10 if level > 0 else -10)
        
        # Increase Font Size for TimeLine
        c.setFont(FONT_BOLD, 14) # Was 12
        c.drawCentredString(x, text_base_y, str(event['year']))
        
        c.setFont(FONT_REG, 14) # Was 12
        dy = 15 if level > 0 else -15 # Increased spacing slightly for larger font
        c.drawCentredString(x, text_base_y + dy, label_text)
        
        detail_txt = ''
        evt_type = event.get('type')
        val = event.get('value')
        plc = event.get('place')
        
        if evt_type in ['CHIL_BIRTH', 'MARR'] and val:
            detail_txt = val
        elif plc:
            detail_txt = plc
        elif val:
            detail_txt = val

        if detail_txt:
            c.setFont(FONT_REG, 12) # Was 10
            c.setFillColor(colors.HexColor('#444444'))
            limit = 60
            if len(detail_txt) > limit:
                split_idx = detail_txt.rfind(' ', 0, limit)
                if split_idx == -1: split_idx = limit
                line1 = detail_txt[:split_idx]
                line2 = detail_txt[split_idx:].strip()
                if len(line2) > limit: line2 = line2[:limit-3] + "..."
                c.drawCentredString(x, text_base_y + dy*2, line1)
                c.drawCentredString(x, text_base_y + dy*3, line2)
            else:
                c.drawCentredString(x, text_base_y + dy*2, detail_txt)
            c.setFillColor(TEXT_COLOR)

def draw_section_title(c, x, y, title):
    c.setFont(FONT_BOLD, 20)
    c.setFillColor(TEXT_COLOR)
    c.drawString(x, y, title)
    c.setLineWidth(2)
    c.line(x, y - 5, x + 300, y - 5)
    return y - 40

def draw_vitals(c, x, y, person):
    y = draw_section_title(c, x, y, "Vitals")
    c.setFont(FONT_REG, 14)
    leading = 20
    
    sex = person.get('sex', '?')
    if sex == 'M': sex = "Male"
    if sex == 'F': sex = "Female"
    
    items = [
        f"Sex: {sex}",
        f"Birth: {person.get('birthDate', '?')}",
        f"Death: {person.get('deathDate', '?')}",
        f"Place of Birth: {person.get('birthPlace', '')}",
        f"Place of Death: {person.get('deathPlace', '')}"
    ]
    
    for item in items:
        val = item.split(": ")[1]
        if val and val != '?' and val != 'None' and val != '':
             c.drawString(x, y, item)
             y -= leading
    return y

def draw_family_group(c, x, y, family_data, y_floor=60):
    """Immediate family list, truncated so it cannot run off the page.

    The A2 poster anchors its visual tree to the bottom right, so this column
    has a hard floor. Groups that do not fit are summarised the same way the
    generation series columns do it, with a trailing "...and N more".
    """
    y = draw_section_title(c, x, y, "Immediate Family List")
    c.setFont(FONT_REG, 14)
    leading = 18

    groups = [
        ("Parents", family_data.get('parents') or []),
        ("Spouses", family_data.get('spouses') or []),
        ("Children", family_data.get('children') or []),
    ]

    for title, people in groups:
        if not people:
            continue
        # A heading with no room for at least one name under it is worse than
        # no heading, so stop rather than start a group we cannot fill.
        if y - (2 * leading) < y_floor:
            break

        c.setFont(FONT_BOLD, 14)
        c.drawString(x, y, title)
        y -= leading

        c.setFont(FONT_REG, 14)
        for idx, person in enumerate(people):
            remaining = len(people) - idx
            # Reserve a line for the "...and N more" summary when one is needed.
            needs_summary = remaining > 1
            if y - leading < y_floor or (needs_summary and y - (2 * leading) < y_floor):
                c.setFillColor(colors.HexColor('#777777'))
                c.drawString(x + 10, y, f"...and {remaining} more")
                c.setFillColor(TEXT_COLOR)
                y -= leading
                break
            c.drawString(x + 10, y, person.get('name', 'Unknown').replace('/', ''))
            y -= leading

        y -= 5

    return y

# --- NEW VISUAL TREE LOGIC ---

def draw_person_node(c, x, y, name, lifespan, is_ego=False):
    width = 140
    height = 50
    
    # Shadow
    c.setFillColor(colors.HexColor('#d1c7b7'))
    c.roundRect(x - width/2 + 3, y - height/2 - 3, width, height, 4, fill=1, stroke=0)
    
    # Box
    if is_ego:
        c.setFillColor(colors.HexColor('#ffffff'))
        c.setStrokeColor(colors.HexColor('#000000'))
        c.setLineWidth(2)
    else:
        c.setFillColor(colors.HexColor('#fcfcfc'))
        c.setStrokeColor(colors.HexColor('#555555'))
        c.setLineWidth(1)
        
    c.roundRect(x - width/2, y - height/2, width, height, 4, fill=1, stroke=1)
    
    # Text
    c.setFillColor(TEXT_COLOR)
    
    clean_name = name.replace('/', '').strip()
    if len(clean_name) > 18:
        parts = clean_name.split(' ')
        mid = len(parts)//2
        l1 = " ".join(parts[:mid])
        l2 = " ".join(parts[mid:])
        c.setFont(FONT_BOLD, 10 if is_ego else 9)
        c.drawCentredString(x, y + 4, l1)
        c.drawCentredString(x, y - 6, l2)
        c.setFont(FONT_REG, 8)
        c.setFillColor(colors.HexColor('#666666'))
        c.drawCentredString(x, y - 18, lifespan or "")
    else:
        c.setFont(FONT_BOLD, 11 if is_ego else 10)
        c.drawCentredString(x, y + 2, clean_name)
        c.setFont(FONT_REG, 8)
        c.setFillColor(colors.HexColor('#666666'))
        c.drawCentredString(x, y - 12, lifespan or "")

def draw_visual_tree(c, x, y, person, family_data):
    """
    Draws a Local Tree Diagram with Multi-Pass rendering (Lines then Boxes).
    Strictly anchored Bottom-Right with Wide Box.
    """
    
    # BOX SETTINGS
    margin = 50
    box_width = 1200 # WIDER box to allow left expansion
    box_height = 400 
    
    # Anchor Bottom Right
    box_x = WIDTH - margin - box_width
    box_y = margin
    
    # Draw Border
    c.setStrokeColor(colors.HexColor('#000000'))
    c.setLineWidth(1) # THINNER BORDER (Previously 3)
    c.rect(box_x, box_y, box_width, box_height, fill=0, stroke=1)
    
    # Title
    title_x = box_x + 20
    title_y = box_y + box_height - 30
    c.setFont(FONT_BOLD, 20)
    c.setFillColor(TEXT_COLOR)
    c.drawString(title_x, title_y, "Local Family Tree")
    
    # --- PHASE 1: CALCULATE COORDINATES ---
    
    box_top_y = box_y + box_height
    start_y = box_top_y - 80 
    row_height = 110 
    spacing = 160
    
    # A. PARENTS
    parents = family_data.get('parents', [])
    parent_coords = [] # (x, y, data)
    
    if parents:
        num_p = len(parents)
        total_w = (num_p - 1) * spacing
        center_x = box_x + box_width / 2 # Center of box
        p_start_x = center_x - total_w / 2
        
        for i, p in enumerate(parents):
            px = p_start_x + i * spacing
            py = start_y
            parent_coords.append({'x': px, 'y': py, 'data': p})
            
    # B. ROW 2 (Sibs, Ego, Spouse)
    siblings = family_data.get('siblings', [])
    spouses = family_data.get('spouses', [])
    
    row2_list = []
    for s in siblings: row2_list.append(('sib', s))
    row2_list.append(('ego', person))
    for s in spouses: row2_list.append(('spouse', s))
    
    num_row2 = len(row2_list)
    total_w2 = (num_row2 - 1) * spacing
    
    center_x = box_x + box_width / 2
    r2_start_x = center_x - total_w2 / 2
    ego_y = start_y - row_height
    
    row2_coords = []
    ego_coord = None
    spouse_coords = []
    
    for i, (type_, obj) in enumerate(row2_list):
        nx = r2_start_x + i * spacing
        # Soft Clamp to Box (Keep 80 padding)
        if nx < box_x + 80: nx = box_x + 80
        if nx > box_x + box_width - 80: nx = box_x + box_width - 80
        ny = ego_y
        
        item = {'x': nx, 'y': ny, 'data': obj, 'type': type_}
        row2_coords.append(item)
        
        if type_ == 'ego': ego_coord = item
        if type_ == 'spouse': spouse_coords.append(item)

    # C. CHILDREN
    children = family_data.get('children', [])
    child_coords = []
    child_bar_data = None # store info to draw bar later
    
    if children and spouse_coords and ego_coord:
        child_y = ego_y - row_height
        
        # Connect to Ego + First Spouse
        s_item = spouse_coords[0]
        sx, sy = s_item['x'], s_item['y']
        ex, ey = ego_coord['x'], ego_coord['y']
        
        cx = (ex + sx) / 2
        bar_y = ey - 40 
        
        num_c = len(children)
        total_cw = (num_c - 1) * spacing
        start_cx = cx - total_cw / 2
        
        # Clamp Children Start
        if start_cx < box_x + 80: start_cx = box_x + 80
        
        # Recalculate positions based on clamped start
        current_cx = start_cx
        for child in children:
            child_coords.append({'x': current_cx, 'y': child_y, 'data': child})
            current_cx += spacing
            
        child_bar_data = {
            'ego_x': ex, 'ego_y': ey,
            'sp_x': sx, 'sp_y': sy,
            'cx': cx, 'bar_y': bar_y,
            'start_cx': start_cx,
            'end_cx': start_cx + total_cw if num_c > 1 else start_cx
        }

    # --- PHASE 2: DRAW LINES (BEHIND) ---
    c.setLineWidth(1)
    c.setStrokeColor(colors.HexColor('#000000'))
    
    # Parents Link
    if len(parent_coords) > 1:
        p1 = parent_coords[0]
        p2 = parent_coords[-1]
        c.line(p1['x'], p1['y'], p2['x'], p2['y'])
        
    # Parents -> Ego
    if parent_coords:
        # Parents Center
        pcx = sum(p['x'] for p in parent_coords) / len(parent_coords)
        pcy = parent_coords[0]['y']
        mid_y = (pcy + ego_y) / 2
        
        # Link Ego+Sibs
        # Identify relevant X's
        rel_xs = [r['x'] for r in row2_coords if r['type'] in ['ego', 'sib']]
        if rel_xs:
            min_x = min(rel_xs)
            max_x = max(rel_xs)
            
            # Down from parents
            c.line(pcx, pcy, pcx, mid_y)
            # Horizontal
            c.line(min_x, mid_y, max_x, mid_y)
            # Down to kids
            for rx in rel_xs:
                 c.line(rx, mid_y, rx, ego_y + 25) # Top of box
                 
    # Ego -> Spouse -> Children
    if child_bar_data:
        d = child_bar_data
        # Drops from couple
        c.line(d['ego_x'], d['ego_y'] - 25, d['ego_x'], d['bar_y'])
        c.line(d['sp_x'], d['sp_y'] - 25, d['sp_x'], d['bar_y'])
        # Connector
        c.line(d['ego_x'], d['bar_y'], d['sp_x'], d['bar_y'])
        # Drop to child bar
        c.line(d['cx'], d['bar_y'], d['cx'], d['bar_y'] - 20)
        # Child Bar
        c.line(d['start_cx'], d['bar_y'] - 20, d['end_cx'], d['bar_y'] - 20)
        # Up from children
        for child in child_coords:
            c.line(child['x'], child['y'] + 25, child['x'], d['bar_y'] - 20)
            
    # --- PHASE 3: DRAW BOXES (ON TOP) ---
    
    for p in parent_coords:
        draw_person_node(c, p['x'], p['y'], p['data'].get('name', 'Unknown'), p['data'].get('lifeSpan', ''))
        
    for r in row2_coords:
        is_me = (r['type'] == 'ego')
        draw_person_node(c, r['x'], r['y'], r['data'].get('name', 'Unknown'), r['data'].get('lifeSpan', ''), is_ego=is_me)
        
    for ch in child_coords:
        draw_person_node(c, ch['x'], ch['y'], ch['data'].get('name', 'Unknown'), ch['data'].get('lifeSpan', ''))
            

def generate_a2_pdf(person, family_data, relationship="", root_name=""):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A2))
    
    draw_background(c)
    draw_header(c, person, relationship, root_name)
    draw_timeline(c, person)
    
    # Layout Strategy
    col_start_y = HEIGHT - 700 
    
    col_1_x = 50 
    col_2_x = WIDTH - 600 # Unused for tree now as it self-anchors
    
    # 1. Left: Vitals & Family Text List
    y = draw_vitals(c, col_1_x, col_start_y, person)
    draw_family_group(c, col_1_x, y - 40, family_data)
    
    # 2. Right: Visual Local Tree (Self-Anchored)
    draw_visual_tree(c, 0, 0, person, family_data)
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# --- ANCESTOR TREE (PEDIGREE) RENDERER ---
def draw_full_tree_page(c, grouped_data, highlight_gen_idx):
    """
    Draws a Horizontal Ancestor Tree (Pedigree Chart).
    Root at Left, branching to Right.
    Includes a "Center Seam" gap to prevent boxes on the fold.
    """
    draw_background(c)
    
    # Title
    c.setFillColor(TEXT_COLOR)
    c.setFont(FONT_BOLD, 32)
    c.drawString(40, HEIGHT - 50, f"Generation {highlight_gen_idx} Context")
    
    # Layout Params
    # MAXIMIZE HEIGHT: Reduce margins
    tree_margin_x = 40
    tree_margin_y = 40
    
    # Available area for the tree
    avail_w = WIDTH - (2 * tree_margin_x)
    avail_h = HEIGHT - (2 * tree_margin_y) - 50 # Account for title
    
    max_gen = max(grouped_data.keys())
    display_gens = max_gen + 1
    
    # --- X Coordinate Strategy (Center Gap) ---
    # We want to split generations into Left Group and Right Group.
    # Gap in middle (e.g., 100px).
    gap_size = 120
    center_x = WIDTH / 2
    
    # Calculate how many gens fit on left/right. 
    # Attempt to balance, but keep Gen 0 Left.
    # Example: 4 Gens -> 0,1 Left | 2,3 Right
    # Example: 5 Gens -> 0,1 Left | 2,3,4 Right (Layout preference)
    mid_gen = display_gens // 2
    
    # Allocate width segments
    # Left Width
    w_left = (avail_w - gap_size) / 2
    w_right = w_left
    
    # X Positions per generation
    gen_x_map = {}
    
    for gen in range(display_gens):
        if gen < mid_gen:
            # Left Side
            # Distribute 0..mid_gen-1 across w_left
            count = mid_gen
            step = w_left / count
            # Center of the column
            gx = tree_margin_x + (gen * step) + (step / 2)
            # Alignment adjustment: Left align Gen 0? 
            # Let's keep centered in column for now.
        else:
            # Right Side
            # Distribute mid_gen..max_gen across w_right
            count = display_gens - mid_gen
            idx_in_group = gen - mid_gen
            step = w_right / count
            gx = center_x + (gap_size/2) + (idx_in_group * step) + (step / 2)
            
        gen_x_map[gen] = gx
        
    # --- Y Coordinate Strategy ---
    # Max slots = 2^max_gen
    max_slots = 2 ** max_gen
    slot_height = avail_h / max_slots
    
    # Optimize Box Height
    # If slot is huge (Gen 0), limit height.
    # If slot is tiny, fill it.
    box_height = min(100, slot_height - 2) # Tight gap (2px)
    if box_height < 18: box_height = 18 # Hard minimum
    
    # If box is "too wide" and "thin", user complaints. 
    # Let's limit width based on available step.
    # Determine min step width
    min_col_w = min(w_left / (mid_gen or 1), w_right / ((display_gens-mid_gen) or 1))
    box_width = min(200, min_col_w - 20)
    
    # Font Sizing
    # User wants bigger dates.
    font_name = 12
    font_date = 11 
    
    if box_height < 50:
        font_name = 10
        font_date = 10 # prioritize date visibility
    if box_height < 30:
        font_name = 9
        font_date = 9
    
    node_coords = {}
    
    root_nodes = grouped_data.get(0, [])
    if not root_nodes: return
    root = root_nodes[0]
    
    
    # 1. Build Quick Lookup
    person_map = {}
    for gen_idx, peeps in grouped_data.items():
        for p in peeps:
            person_map[p['id']] = p

    # 2. Dynamic Weight Calculation (Count Leaves)
    memo_weight = {}
    
    def get_subtree_weight(pid, depth):
        if depth >= display_gens: 
            return 1
        
        key = (pid, depth)
        if key in memo_weight: return memo_weight[key]
        
        if not pid or pid not in person_map:
            # Empty branch takes 1 slot minimal
            return 1
            
        p = person_map[pid]
        w_dad = get_subtree_weight(p.get('fatherId'), depth + 1)
        w_mom = get_subtree_weight(p.get('motherId'), depth + 1)
        
        # If both parents are missing/empty, this person is a leaf -> 1
        # But wait, earlier logic: if missing they return 1.
        # So w_dad=1, w_mom=1 -> sum=2.
        # Ideally if I have NO parents, I am 1 leaf.
        # Optimization: max(1, w_dad + w_mom) where missing children return 0?
        # Let's try: Missing child returns 0.5?
        # Let's stick to the plan: Missing ID -> Returns 1 unit (a placeholder slot).
        
        # Refined Logic for Compactness:
        # If I exist, but have no parents recorded: I count as 1.
        # If I have 1 parent: I count as that parent's weight + 1 (other side).
        
        # Check existence
        fid = p.get('fatherId')
        mid = p.get('motherId')
        
        # If purely terminal node in our data set
        if not fid and not mid:
            return 1
            
        res = w_dad + w_mom
        memo_weight[key] = res
        return res

    root_weight = get_subtree_weight(root['id'], 0)
    
    # 3. Optimize Box Height based on Weight
    # Total available slots = root_weight
    slot_height = avail_h / root_weight
    
    # Cap size constraints
    box_height = min(100, slot_height - 2)
    if box_height < 18: box_height = 18 
    
    # 4. Weighted Traversal Placement
    def traverse_place_safe(pid, depth, y_low, y_high):
        # Y axis: 0 at bottom, increasing.
        # Father (Older/Male) usually 'Above' Mother?
        # In std pedigree code: Yes, Dad top, Mom bot.
        
        # Calculate my center
        my_y = (y_low + y_high) / 2
        
        if pid:
            x = gen_x_map.get(depth, 0)
            node_coords[pid] = (x, my_y)
            
        if depth + 1 >= display_gens: return
        
        if not pid or pid not in person_map: return
        
        p = person_map[pid]
        fid = p.get('fatherId')
        mid = p.get('motherId')
        
        w_dad = get_subtree_weight(fid, depth + 1)
        w_mom = get_subtree_weight(mid, depth + 1)
        total_w = w_dad + w_mom
        
        if total_w == 0: return # Should not happen with current weight logic
        
        # Allocate Height
        avail_h_local = y_high - y_low
        h_dad = avail_h_local * (w_dad / total_w)
        h_mom = avail_h_local * (w_mom / total_w)
        
        # Dad gets Upper band
        traverse_place_safe(fid, depth + 1, y_low + h_mom, y_high)
        # Mom gets Lower band
        traverse_place_safe(mid, depth + 1, y_low, y_low + h_mom)

    # Trigger Layout
    # Special Handling for Gen 0 (Siblings + Ego)
    # 1. Place Generation 1+ (Ancestors)
    # We treat the Parents of the Root (Gen 1) as the start of the recursive tree.
    # They get the full height (since all Gen 0 children share them).
    
    fid = root.get('fatherId')
    mid = root.get('motherId')
    
    w_dad = get_subtree_weight(fid, 1)
    w_mom = get_subtree_weight(mid, 1)
    total_w = w_dad + w_mom
    
    if total_w > 0:
        # Split avail height between Dad and Mom
        # Range: [tree_margin_y, tree_margin_y + avail_h]
        y_low = tree_margin_y
        y_high = tree_margin_y + avail_h
        avail_h_local = y_high - y_low
        
        h_dad = avail_h_local * (w_dad / total_w)
        h_mom = avail_h_local * (w_mom / total_w)
        
        # Dad Top, Mom Bottom
        traverse_place_safe(fid, 1, y_low + h_mom, y_high)
        traverse_place_safe(mid, 1, y_low, y_low + h_mom)
        
    # 2. Place Generation 0 (Siblings)
    # Stack them evenly in the Gen 0 column
    gen0_peeps = grouped_data.get(0, [])
    # Sort by birth date? Or keep original order (likely mostly chronological)
    # Let's sort to be safe: undefined dates last
    def parse_year_safe(p):
        d = p.get('birthDate')
        if not d: return 9999
        import re
        m = re.search(r'\d{4}', d)
        return int(m.group(0)) if m else 9999
        
    gen0_peeps = sorted(gen0_peeps, key=parse_year_safe)
    
    # Simple stack
    count = len(gen0_peeps)
    if count > 0:
        step = avail_h / count
        start_y_gen0 = tree_margin_y
        
        for i, p in enumerate(gen0_peeps):
            # Center in slot
            slot_center = start_y_gen0 + (i * step) + (step / 2)
            # But we want oldest at top?
            # PDF Y increases up.
            # If sorted by birth (oldest=small year), i=0 is oldest.
            # We want oldest at TOP? Or Bottom?
            # Usually oldest child at top.
            # So i=0 -> Top Slot?
            # slot_top -> start_y + avail - (i * step)
            
            # Let's place i=0 (Oldest) at TOP.
            # top_y = start_y + avail_h
            # slot_y = top_y - (i * step) - (step/2)
            
            y_pos = (start_y_gen0 + avail_h) - (i * step) - (step/2)
            
            x = gen_x_map.get(0, 50)
            node_coords[p['id']] = (x, y_pos)
    
    # Drawing Pass
    c.setLineWidth(1)
    
    # Draw Connections
    c.setStrokeColor(colors.HexColor('#bbbbbb'))
    for gen in range(display_gens):
        people = grouped_data.get(gen, [])
        for p in people:
            pid = p['id']
            if pid in node_coords:
                px, py = node_coords[pid]
                
                fid = p.get('fatherId')
                mid = p.get('motherId')
                
                # Check for seam crossing
                # If parent is across the seam, we just draw the line.
                
                if fid and fid in node_coords:
                    fx, fy = node_coords[fid]
                    # Line from Child Right to Parent Left
                    p_right = px + width_offset(box_width) 
                    f_left = fx - width_offset(box_width)
                    
                    # Elbow logic
                    mid_x = (p_right + f_left) / 2
                    p_path = c.beginPath()
                    p_path.moveTo(p_right, py)
                    p_path.lineTo(mid_x, py)
                    p_path.lineTo(mid_x, fy)
                    p_path.lineTo(f_left, fy)
                    c.drawPath(p_path, stroke=1, fill=0)
                    
                if mid and mid in node_coords:
                    mx, my = node_coords[mid]
                    p_right = px + width_offset(box_width)
                    m_left = mx - width_offset(box_width)
                    mid_x = (p_right + m_left) / 2
                    
                    p_path = c.beginPath()
                    p_path.moveTo(p_right, py)
                    p_path.lineTo(mid_x, py)
                    p_path.lineTo(mid_x, my)
                    p_path.lineTo(m_left, my)
                    c.drawPath(p_path, stroke=1, fill=0)

    # Draw Boxes
    for gen in range(display_gens):
        people = grouped_data.get(gen, [])
        for p in people:
            pid = p['id']
            if pid in node_coords:
                x, y = node_coords[pid]
                is_highlight = (gen == highlight_gen_idx)
                
                draw_tree_box(c, x, y, box_width, box_height, p, is_highlight, font_name, font_date)

    c.showPage()

def width_offset(bw):
    return bw / 2

def draw_tree_box(c, x, y, w, h, person, is_highlight, f_name_size, f_date_size):
    # Style
    if is_highlight:
        c.setFillColor(colors.HexColor('#fff8e1'))
        c.setStrokeColor(colors.HexColor('#d4a017'))
        c.setLineWidth(2)
    else:
        c.setFillColor(colors.HexColor('#ffffff'))
        c.setStrokeColor(colors.HexColor('#666666'))
        c.setLineWidth(1)
        
    c.roundRect(x - w/2, y - h/2, w, h, 6, fill=1, stroke=1)
    
    # Content
    nm = person.get('name', 'Unknown').replace('/', '').strip()
    life = person.get('lifeSpan', '')
    
    # Optimize layout based on height
    c.setFillColor(TEXT_COLOR)
    
    if h > 40:
        # Two lines
        c.setFont(FONT_BOLD, f_name_size)
        # Truncate
        max_chars = int(w / (f_name_size * 0.55))
        if len(nm) > max_chars: nm = nm[:max_chars-2] + ".."
        c.drawCentredString(x, y + 4, nm)
        
        c.setFont(FONT_REG, f_date_size)
        c.setFillColor(colors.HexColor('#333333'))
        c.drawCentredString(x, y - (h/4) - 2, life)
        
    else:
        # Single line tight mode
        # Name | Dates
        # OR Name top, Date bot very tight
        c.setFont(FONT_BOLD, f_name_size)
        c.drawCentredString(x, y + (h/4), nm)
        
        c.setFont(FONT_REG, f_date_size)
        c.setFillColor(colors.HexColor('#333333'))
        c.drawCentredString(x, y - (h/4), life)


def generate_generation_series_pdf(grouped_data, render_gen_ids=None):
    """
    Generates a Multi-Page PDF for a generational series using Vertical Strips Layout.
    Each person gets a full-vertical column.
    grouped_data: {0: [Ego], 1: [Dad, Mom], ...} (Full context needed for Tree)
    render_gen_ids: list of int, optional. If provided, only generate pages for these gens.
    Returns BytesIO buffer.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A2))
    
    # Determine which generations to render
    if render_gen_ids:
        gen_indices = sorted([g for g in render_gen_ids if g in grouped_data])
    else:
        gen_indices = sorted(grouped_data.keys())
    
    # Layout Constants
    MARGIN_TOP = 80
    MARGIN_BTM = 50
    MARGIN_SIDE = 50
    # Fixed 4 cols per page
    MAX_COLS = 4 
    
    # Gap in middle (visual split)
    # Col 1, 2 |GAP| 3, 4
    # Calculate widths manually
    GAP_CENTER = 80
    GAP_REG = 20
    
    for gen_idx in gen_indices:
        people = grouped_data[gen_idx]
        if not people: continue
        
        # --- 1. Draw Full Tree Page for this Gen ---
        # We pass FULL grouped_data so it can trace from Root
        draw_full_tree_page(c, grouped_data, highlight_gen_idx=gen_idx)
        
        # --- 2. Draw Detail Pages ---
        
        chunks = [people[i:i + MAX_COLS] for i in range(0, len(people), MAX_COLS)]
        
        for chunk_idx, chunk in enumerate(chunks):
            draw_background(c)
            
            # --- Page Header ---
            gen_title = f"Generation {gen_idx}"
            if gen_idx == 0: gen_title = "Your Generation"
            elif gen_idx == 1: gen_title = "Parents"
            elif gen_idx == 2: gen_title = "Grandparents"
            elif gen_idx == 3: gen_title = "Great-Grandparents"
            else: gen_title = f"{gen_idx-2}x Great-Grandparents"
            
            if len(chunks) > 1:
                gen_title += f" (Part {chunk_idx + 1})"
            
            c.setFillColor(TEXT_COLOR)
            c.setFont(FONT_BOLD, 36)
            c.drawString(MARGIN_SIDE, HEIGHT - 50, gen_title)
            
            # --- Column Layout ---
            # We want 4 columns guaranteed width.
            # Width = (WIDTH - 2*Margin - GapCenter - 2*GapReg) / 4
            
            workable_width = WIDTH - (2 * MARGIN_SIDE) - GAP_CENTER - (2 * GAP_REG)
            col_width = workable_width / 4
            
            start_y = HEIGHT - MARGIN_TOP
            col_height = HEIGHT - MARGIN_TOP - MARGIN_BTM
            
            for i, p in enumerate(chunk):
                # Calculate X
                # i=0 -> Margin
                # i=1 -> Margin + W + GapReg
                # i=2 -> Margin + 2W + GapReg + GapCenter  (Start of Right Half)
                # i=3 -> Margin + 3W + 2GapReg + GapCenter
                
                col_x = MARGIN_SIDE
                if i == 1: col_x += col_width + GAP_REG
                elif i == 2: col_x += (2 * col_width) + GAP_REG + GAP_CENTER
                elif i == 3: col_x += (3 * col_width) + (2 * GAP_REG) + GAP_CENTER
                
                # Draw Column Background
                c.setFillColor(colors.HexColor('#ffffff'))
                c.setStrokeColor(colors.HexColor('#d1c7b7'))
                c.setLineWidth(1)
                
                # Shadow
                c.setFillColor(colors.HexColor('#e0d5c1'))
                c.rect(col_x + 4, MARGIN_BTM - 4, col_width, col_height, fill=1, stroke=0)
                # Main BG
                c.setFillColor(colors.HexColor('#ffffff'))
                c.rect(col_x, MARGIN_BTM, col_width, col_height, fill=1, stroke=1)
                
                # --- CONTENT ---
                cx = col_x + 20
                cy = start_y - 40
                cw = col_width - 40 
                
                # 0. Side Badge (Maternal/Paternal)
                side = p.get('_side', '')
                if side and side != 'Direct':
                    c.setFont(FONT_BOLD, 10)
                    c.setFillColor(colors.HexColor('#ffffff'))
                    
                    badge_color = colors.HexColor('#5e81ac') if side == 'Paternal' else colors.HexColor('#bf616a')
                    bw = c.stringWidth(side.upper(), FONT_BOLD, 10) + 16
                    
                    # Draw Badge Top Right of Col
                    bx = col_x + col_width - bw - 10
                    by = start_y - 30
                    
                    pp = c.beginPath()
                    pp.roundRect(bx, by, bw, 20, 10)
                    # Background
                    c.setFillColor(badge_color)
                    c.drawPath(pp, stroke=0, fill=1)
                    
                    c.setFillColor(colors.HexColor('#ffffff'))
                    # Centered vertically in 20px box (y to y+20). 
                    # by + 6 gives approx baseline for centered text of size 10
                    c.drawCentredString(bx + bw/2, by + 6, side.upper())
                
                # 1. Identity Header
                name = p.get('name', 'Unknown').replace('/', '').strip()
                life = p.get('lifeSpan', '')
                
                c.setFillColor(TEXT_COLOR)
                c.setFont(FONT_BOLD, 20)
                # ... (Name Wrapping Logic Same) ...
                words = name.split()
                line = ""
                for w in words:
                    if c.stringWidth(line + w, FONT_BOLD, 20) < cw:
                        line += w + " "
                    else:
                        c.drawString(cx, cy, line)
                        cy -= 25
                        line = w + " "
                c.drawString(cx, cy, line)
                cy -= 25
                
                c.setFont(FONT_REG, 14)
                c.setFillColor(colors.HexColor('#555555'))
                c.drawString(cx, cy, life)
                cy -= 30
                
                c.setLineWidth(1)
                c.setStrokeColor(colors.HexColor('#eeeeee'))
                c.line(cx, cy, cx + cw, cy)
                cy -= 20
                
                # 2. Vitals (Bigger Font)
                c.setFillColor(TEXT_COLOR)
                c.setFont(FONT_BOLD, 13) # Was 12
                c.drawString(cx, cy, "VITALS")
                cy -= 18
                
                c.setFont(FONT_REG, 12) # Was 11
                vitals = [
                   f"B: {p.get('birthDate', '-')}",
                   f"   {p.get('birthPlace', '')}",
                   f"D: {p.get('deathDate', '-')}",
                   f"   {p.get('deathPlace', '')}"
                ]
                for v in vitals:
                    if v.strip() and v.strip() != 'B: -' and v.strip() != 'D: -':
                         if len(v) > 35: v = v[:32] + "..."
                         c.drawString(cx, cy, v)
                         cy -= 15 # Increased spacing
                cy -= 12
                c.setStrokeColor(colors.HexColor('#eeeeee'))
                c.line(cx, cy, cx + cw, cy)
                cy -= 22
                
                # 3. Family Context (Bigger Font)
                fam_ctx = p.get('_family_context', {})
                parents = fam_ctx.get('parents', [])
                spouses = fam_ctx.get('spouses', [])
                children = fam_ctx.get('children', [])
                
                if parents:
                    c.setFillColor(TEXT_COLOR)
                    c.setFont(FONT_BOLD, 13)
                    c.drawString(cx, cy, "PARENTS")
                    cy -= 18
                    c.setFont(FONT_REG, 12)
                    for par in parents:
                        c.drawString(cx, cy, par.get('name', 'Unknown').replace('/', ''))
                        cy -= 15
                    cy -= 12

                if spouses:
                    c.setFillColor(TEXT_COLOR)
                    c.setFont(FONT_BOLD, 13)
                    c.drawString(cx, cy, "SPOUSES")
                    cy -= 18
                    c.setFont(FONT_REG, 12)
                    for sp in spouses:
                        c.drawString(cx, cy, sp.get('name', 'Unknown').replace('/', ''))
                        cy -= 15
                    cy -= 12

                if children:
                    c.setFillColor(TEXT_COLOR)
                    c.setFont(FONT_BOLD, 13)
                    c.drawString(cx, cy, f"CHILDREN ({len(children)})")
                    cy -= 18
                    c.setFont(FONT_REG, 12)
                    limit = 6
                    for child in children[:limit]:
                        c.drawString(cx, cy, child.get('name', 'Unknown').replace('/', ''))
                        cy -= 15
                    if len(children) > limit:
                        c.setFillColor(colors.HexColor('#777777'))
                        c.drawString(cx, cy, f"...and {len(children)-limit} more")
                        cy -= 15
                    cy -= 12
                    
                c.setStrokeColor(colors.HexColor('#eeeeee'))
                c.line(cx, cy, cx + cw, cy)
                cy -= 22
                
                # 4. Timeline
                c.setFillColor(TEXT_COLOR)
                c.setFont(FONT_BOLD, 13)
                c.drawString(cx, cy, "TIMELINE")
                cy -= 22
                
                events = p.get('events', [])
                tl_events = []
                for e in events:
                    if e.get('date'):
                        year = parse_year(e.get('date'))
                        if year:
                            tl_events.append({'y': year, 'txt': e.get('type'), 'dt': e.get('date'), 'val': e.get('value') or e.get('place')})
                tl_events.sort(key=lambda x: x['y'])
                
                line_x = cx + 50 # Shift line right
                line_top = cy
                line_btm = MARGIN_BTM + 20
                
                if cy > line_btm + 50:
                    c.setStrokeColor(colors.HexColor('#cccccc'))
                    c.setLineWidth(1)
                    c.line(line_x, line_top, line_x, line_btm)
                    
                    c.setFont(FONT_REG, 11) # Bigger Timeline Font
                    curr_y = line_top
                    
                    for ev in tl_events:
                        if curr_y < line_btm + 20: break
                        
                        # Fix Overlap: Date Left, Dot On Line, Text Right
                        
                        # Date
                        c.setFillColor(colors.HexColor('#777777'))
                        c.drawRightString(line_x - 10, curr_y - 3, str(ev['y']))
                        
                        # Dot
                        c.setFillColor(colors.HexColor('#333333'))
                        c.circle(line_x, curr_y, 3, fill=1, stroke=0)
                        
                        # Text
                        c.setFillColor(TEXT_COLOR)
                        label = get_event_config(ev['txt'])[0]
                        c.drawString(line_x + 10, curr_y - 3, label)
                        
                        # Detail
                        val = ev['val']
                        if val:
                             c.setFillColor(colors.HexColor('#666666'))
                             if len(val) > 40: val = val[:37] + "..."
                             c.drawString(line_x + 10, curr_y - 13, val)
                             curr_y -= 10
                             
                        curr_y -= 25 # Spacing
            
            c.showPage()
        
    c.save()
    buffer.seek(0)
    return buffer

def generate_generation_zip(grouped_data):
    """
    Returns a ZIP file containing separate PDFs for each generation.
    """
    import zipfile
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        gen_indices = sorted(grouped_data.keys())
        
        for gen_idx in gen_indices:
            people = grouped_data[gen_idx]
            if not people: continue
            
            # Create a PDF for JUST this generation, but pass FULL data for tree context
            pdf_bytes = generate_generation_series_pdf(grouped_data, render_gen_ids=[gen_idx])
            
            # Filename
            fname = f"Generation_{gen_idx}_List.pdf"
            if gen_idx == 1: fname = "1_Your_Parents.pdf"
            elif gen_idx == 2: fname = "2_Your_Grandparents.pdf"
            elif gen_idx == 3: fname = "3_Your_Great_Grandparents.pdf"
            
            zf.writestr(fname, pdf_bytes.getvalue())
            
    zip_buffer.seek(0)
    return zip_buffer

def _located_events(person):
    """Life events carrying a place, in rough chronological order.

    Birth first and death last, with everything else in between, so the travel
    lines drawn between them read as a life rather than as file order.
    """
    events = []

    birth_place = person.get('birthPlace')
    if birth_place:
        events.append({'type': 'BIRT', 'place': birth_place})

    for e in person.get('events', []):
        if e.get('place') and e.get('type') not in ('BIRT', 'DEAT'):
            events.append(e)

    death_place = person.get('deathPlace')
    if death_place:
        events.append({'type': 'DEAT', 'place': death_place})

    return events


def _places_of(person):
    return [e['place'] for e in _located_events(person) if e.get('place')]


def generate_map_pdf(all_people):
    """
    Generates an A2 Landscape Heatmap/Travel Map of valid locations in Britain.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A2))
    
    geo_service = GeocodingService()
    print("Geocoding service initialized")
    
    # Draw Background (Ocean)
    print("Drawing background...")
    c.setFillColor(colors.HexColor('#e6f2ff')) # Light Blue
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    
    # Title
    c.setFillColor(TEXT_COLOR)
    c.setFont(FONT_BOLD, 48)
    c.drawString(50, HEIGHT - 80, "Family History Map")
    c.setFont(FONT_REG, 24)
    c.drawString(50, HEIGHT - 110, "Movements within Great Britain")

    # Map Bounds (UK + Ireland)
    MIN_LAT = 49.5
    MAX_LAT = 59.5
    MIN_LON = -11.0 # Extended West for Ireland
    MAX_LON = 2.0
    
    
    MAP_MARGIN = 100
    
    # Calculate Aspect Ratio
    # Mid-latitude for approximation (~55 degrees)
    import math
    mid_lat_rad = math.radians((MIN_LAT + MAX_LAT) / 2)
    
    # Degrees to arbitrary units (approx km)
    # Lat degree ~ 111km
    # Lon degree ~ 111km * cos(lat)
    lat_height = (MAX_LAT - MIN_LAT) * 111
    lon_width = (MAX_LON - MIN_LON) * 111 * math.cos(mid_lat_rad)
    
    geo_aspect = lon_width / lat_height
    
    # Available Canvas Dimensions
    avail_w = WIDTH - (2 * MAP_MARGIN)
    avail_h = HEIGHT - (2 * MAP_MARGIN)
    canvas_aspect = avail_w / avail_h
    
    if geo_aspect > canvas_aspect:
        # Map is wider than canvas relative to height (unlikely for UK)
        draw_w = avail_w
        draw_h = avail_w / geo_aspect
    else:
        # Map is taller than canvas (Likely for UK)
        draw_h = avail_h
        draw_w = avail_h * geo_aspect
        
    # Center the map
    off_x = MAP_MARGIN + (avail_w - draw_w) / 2
    off_y = MAP_MARGIN + (avail_h - draw_h) / 2

    def project(lat, lon):
        # Normalize 0..1
        y_rel = (lat - MIN_LAT) / (MAX_LAT - MIN_LAT)
        x_rel = (lon - MIN_LON) / (MAX_LON - MIN_LON)
        
        # Map to Canvas with correct aspect ratio
        x = off_x + (x_rel * draw_w)
        y = off_y + (y_rel * draw_h)
        return x, y


    import json
    import os
    
    def draw_polygon(c, coords):
        path = c.beginPath()
        first = True
        for lon, lat in coords: # GeoJSON is [lon, lat]
            x, y = project(lat, lon)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        path.close()
        c.drawPath(path, fill=1, stroke=1)
        
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor('#cccccc'))
    c.setLineWidth(1)
    
    # Load GeoJSON
    try:
        json_path = os.path.join(os.path.dirname(__file__), 'uk.json')
        with open(json_path, 'r') as f:
            data = json.load(f)
            
        for feature in data['features']:
            geom = feature['geometry']
            if geom['type'] == 'MultiPolygon':
                for poly in geom['coordinates']:
                    # MultiPolygon is list of Polygon, Polygon is list of Ring, Ring is list of coords
                    # Typically [[ [x,y], [x,y] ]]
                    for ring in poly:
                         draw_polygon(c, ring)
            elif geom['type'] == 'Polygon':
                for ring in geom['coordinates']:
                    draw_polygon(c, ring)
                    
    except Exception as e:
        print(f"Failed to load map background: {e}")
        # Fallback to white rectangle if fails
        pass
    
    # Resolve every distinct place first. Doing it up front means the one
    # second courtesy delay Nominatim requires is paid once per place rather
    # than once per person mentioning it, and the run can be reported honestly.
    distinct_places = []
    seen = set()
    for p in all_people:
        for place in _places_of(p):
            if place not in seen:
                seen.add(place)
                distinct_places.append(place)

    print(f"Resolving {len(distinct_places)} distinct places...")
    for place in distinct_places:
        geo_service.get_coords(place)
    geo_service.flush()

    # Process People
    for p in all_people:
        points = []

        # 1. Gather Life Events with Places
        events = _located_events(p)

        # 2. Geocode
        valid_points = []
        for e in events:
            coords = geo_service.get_coords(e['place'])
            if coords:
                lat, lon = coords
                # Filter to Bounds
                if MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON:
                    px, py = project(lat, lon)
                    valid_points.append({'x': px, 'y': py, 'type': e['type'], 'place': e['place']})
        
        if not valid_points:
            continue
            
        # 3. Draw Travel Lines
        if len(valid_points) > 1:
            c.setStrokeColor(colors.HexColor('#555555'))
            c.setLineWidth(0.5)
            # Dashed line
            c.setDash(2, 2)
            
            p_iter = iter(valid_points)
            prev = next(p_iter)
            for curr in p_iter:
                c.line(prev['x'], prev['y'], curr['x'], curr['y'])
                prev = curr
            
            c.setDash([], 0) # Reset
            
        # 4. Draw Points
        for vp in valid_points:
            x, y = vp['x'], vp['y']
            
            # Color
            if vp['type'] == 'BIRT': color = colors.HexColor('#4c9a2a') #  Green
            elif vp['type'] == 'DEAT': color = colors.HexColor('#bf616a') # Red
            else: color = colors.HexColor('#ebcb8b') # Yellow/Orange
            
            # Marker
            c.setFillColor(color)
            c.setStrokeColor(colors.HexColor('#ffffff'))
            c.setLineWidth(1)
            c.circle(x, y, 6, fill=1, stroke=1)
            
            # Label (Tiny)
            c.setFillColor(colors.HexColor('#333333'))
            c.setFont(FONT_REG, 8)
            # Only label if it's not too crowded? 
            # Or just label the city name slightly offset
            c.drawString(x + 8, y - 2, vp['place'].split(',')[0])

    # Legend
    lx = 50
    ly = 100
    c.setFont(FONT_BOLD, 12)
    c.drawString(lx, ly, "Legend:")
    
    items = [('Birth', '#4c9a2a'), ('Residence/Other', '#ebcb8b'), ('Death', '#bf616a')]
    for i, (label, col) in enumerate(items):
        cy = ly - 20 - (i*20)
        c.setFillColor(colors.HexColor(col))
        c.circle(lx + 10, cy + 4, 5, fill=1, stroke=0)
        c.setFillColor(TEXT_COLOR)
        c.setFont(FONT_REG, 12)
        c.drawString(lx + 25, cy, label)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
