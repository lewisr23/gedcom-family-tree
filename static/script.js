const container = document.getElementById('viz-container');
// No cached width/height here on purpose: they were read once at load and went
// stale the moment the window was resized. Anything needing the size reads the
// container at the point of use.

// Constants for Node Design (Vertical, Upwards)
const cardWidth = 200;
const cardHeight = 90;
const nodeSpacingX = 220; // Increased to prevent overlap with neighbor branches
const nodeSpacingY = 150;

// Hoisted rather than created inline, so Reset View can drive the same
// behaviour the mouse wheel does instead of a throwaway second one.
const zoomBehavior = d3.zoom().scaleExtent([0.1, 3]).on("zoom", (event) => {
    g.attr("transform", event.transform);
});

const svg = d3.select("#viz-container").append("svg")
    .attr("width", "100%")
    .attr("height", "100%")
    .call(zoomBehavior);

const INITIAL_SCALE = 0.85;

function initialTransform() {
    // Read the container live rather than trusting the width and height
    // captured at load: the chart grows upwards from the root, so after a
    // window resize the stale numbers park the root off screen.
    return d3.zoomIdentity
        .translate(container.clientWidth / 2, container.clientHeight - 100)
        .scale(INITIAL_SCALE);
}

function resetView() {
    svg.transition().duration(400).call(zoomBehavior.transform, initialTransform());
}

const g = svg.append("g");

let allNodes = {};
let rootData = null;
// Who the chart is currently drawn from. Starts as the first person in the
// file and changes when someone re-roots it.
let currentRootId = null;

// Readable names for GEDCOM event tags. Kept in step with get_event_config in
// app/services/pdf_generator.py so the exported PNG and the exported PDF label
// the same event the same way.
const EVENT_LABELS = {
    BIRT: 'Born',
    DEAT: 'Died',
    MARR: 'Marriage',
    RESI: 'Residence',
    OCCU: 'Occupation',
    EDUC: 'Education',
    CHIL_BIRTH: 'Child Born',
    BURI: 'Burial',
    BAPM: 'Baptism',
    CHR: 'Christening',
    PROB: 'Probate',
    CENS: 'Census',
    IMMI: 'Immigration',
    NATU: 'Naturalisation',
    EVEN: 'Event',
    _MILT: 'Military'
};

// Everything a GEDCOM contains is untrusted text. Names, places and notes all
// end up inside innerHTML templates below, and a file emailed round the family
// is exactly the kind of thing nobody inspects first, so escape on the way in.
const HTML_ESCAPES = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
};

function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, c => HTML_ESCAPES[c]);
}


function eventLabel(type) {
    if (EVENT_LABELS[type]) return EVENT_LABELS[type];
    // Unknown tag: title case it rather than showing a bare code.
    return String(type || '').replace(/^_/, '').toLowerCase()
        .replace(/\b\w/g, c => c.toUpperCase());
}

// Hierarchy Builder (Standard Ancestors)
function buildAncestorHierarchy(personId, depth = 0) {
    if (depth > 20) return null;
    const person = allNodes[personId];
    if (!person) return null;

    // Standard recursive build
    const node = {
        ...person,
        children: [],
        _children: null,
        // We'll track siblings in data, but NOT in d3 'children' array initially
        // They will be "injected" by the renderer if expanded
        hasSiblings: (person.siblingIds && person.siblingIds.length > 0),
        showSiblings: false
    };

    if (person.fatherId) {
        const dad = buildAncestorHierarchy(person.fatherId, depth + 1);
        if (dad) node.children.push(dad);
    }
    if (person.motherId) {
        const mom = buildAncestorHierarchy(person.motherId, depth + 1);
        if (mom) node.children.push(mom);
    }
    return node;
}


function update(source) {
    g.selectAll("*").remove();

    if (!rootData) return;

    // 1. Prepare Hierarchy
    // We need to clone the data because we are going to mutate the hierarchy structure for the layout
    // but we don't want to break the recursive generator for future updates.
    // Actually, d3.hierarchy creates a wrapper, so we can mutate that.

    // We need to inject siblings into the *Parent's* children array.
    // But since the tree is inverted (Root is Child, Children are Parents),
    // "Parent's Children" means "The Node below us".

    const hierarchy = d3.hierarchy(rootData);

    // Map to quickly find nodes by ID in the hierarchy
    const nodeMap = new Map();
    hierarchy.descendants().forEach(d => nodeMap.set(d.data.id, d));

    // INJECTION PASS
    // Iterate and inject siblings as PEERS in the D3 tree.
    // If 'Dad' expands siblings, we find 'Me' (Dad.parent) and add Uncles to 'Me.children'.

    // We must collect injections first, then apply, to avoid concurrent modification issues during traversal
    const injections = [];

    hierarchy.descendants().forEach(d => {
        if (d.data.hasSiblings && d.data.showSiblings) {
            const parent = d.parent; // This is the child in logical tree (e.g. 'Me')

            if (parent) {
                // Determine insertion index to keep 'd' grouped with its siblings
                // We just append for now, D3 sort can handle visual order if needed
                d.data.siblingIds.forEach(sid => {
                    // Check if already present in parent's children
                    if (!parent.children.find(c => c.data.id === sid)) {
                        const sibData = allNodes[sid];
                        if (sibData) {
                            const sibNode = d3.hierarchy({ ...sibData, type: 'sibling', children: [] });
                            sibNode.parent = parent;
                            // Mark this node as a "injected sibling" of 'd'
                            sibNode._isMjSiblingOf = d.data.id;
                            injections.push({ parent: parent, node: sibNode });
                        }
                    }
                });
            } else {
                // Root node (Me) siblings?
                // We can't add to parent.
                // We'd have to create a fake root, but that messes up spacing.
                // For now, only ancestors' siblings supported or complex logic needed.
                // User requirement implies "Ancestors" usually.
                // Let's skip root siblings for strict tree safety unless we make a dummy root.
            }
        }
    });

    // Apply injections
    injections.forEach(inj => {
        inj.parent.children.push(inj.node);
    });

    const treeMap = d3.tree()
        .nodeSize([nodeSpacingX, nodeSpacingY])
        .separation((a, b) => {
            return a.parent == b.parent ? 1.2 : 1.4;
        });

    const treeData = treeMap(hierarchy);

    // 2. SHIFT PASS (Centering Ancestors)
    // D3 layout places the Main Ancestor (e.g. Dad) centered under his parents.
    // But now Dad has Siblings next to him.
    // We want the Parents (Grandpa/Ma) to be centered over the GROUP [Dad, Sibs].

    // Identify Groups
    const shiftGroups = new Map(); // Key: MainPersonID, Value: [MainNode, SibNode1, SibNode2...]

    treeData.descendants().forEach(d => {
        if (d._isMjSiblingOf) {
            const mainId = d._isMjSiblingOf;
            if (!shiftGroups.has(mainId)) {
                shiftGroups.set(mainId, [nodeMap.get(mainId)]);
            }
            shiftGroups.get(mainId).push(d);
        }
    });

    shiftGroups.forEach((groupNodes, mainId) => {
        const mainNode = nodeMap.get(mainId);
        // Only proceed if Main Node has children (Ancestors) to shift
        if (mainNode && mainNode.children && mainNode.children.length > 0) {
            // Calculate center of the Sibling Group
            let minX = Infinity, maxX = -Infinity;
            groupNodes.forEach(n => {
                if (n.x < minX) minX = n.x;
                if (n.x > maxX) maxX = n.x;
            });
            const groupCenterX = (minX + maxX) / 2;

            // Calculate current center of the Main Node (where D3 put it)
            // Actually D3 puts parents relative to Main Node.
            // Main Node is at mainNode.x.
            // Ancestors are centered on mainNode.x usually.

            // We want Ancestors to be centered on groupCenterX.
            const deltaX = groupCenterX - mainNode.x;

            // Apply this Delta to ALL Ancestors (children of mainNode) and their subtrees
            const shiftSubtree = (node, dx) => {
                node.x += dx;
                if (node.children) node.children.forEach(c => shiftSubtree(c, dx));
            };

            // Note: Main Node itself does NOT move (it's anchored by its child 'Me').
            // Siblings do NOT move (they are peers).
            // Only the Ancestors (children of Main Node) move.
            mainNode.children.forEach(c => {
                // Don't shift the injected siblings! They are technically "children" of My Parent?
                // Wait. 'Me' is parent of 'Dad'. 'Uncles' are children of 'Me'.
                // 'Grandpa' is child of 'Dad'.
                // We want to shift 'Grandpa'.
                shiftSubtree(c, deltaX);
            });
        }
    });


    // Links
    // Standard links for Me->Dad are fine.
    // Standard links for Me->Uncle we want to HIDE.
    // We want CUSTOM links for Dad->Grandpa and Uncle->Grandpa (Fork).

    const links = treeData.links().filter(l => {
        // Filter out links from Parent(Me) to InjectedSiblings(Uncle)
        return !l.target._isMjSiblingOf;
    });

    const linkGen = (d) => {
        const sourceX = d.source.x;
        const sourceY = -d.source.y;
        const targetX = d.target.x;
        const targetY = -d.target.y;
        return `M${sourceX},${sourceY} V${(sourceY + targetY) / 2} H${targetX} V${targetY}`;
    };

    g.selectAll(".link")
        .data(links)
        .enter().append("path")
        .attr("class", "link")
        .attr("d", linkGen)
        .attr("stroke", "black");

    // Custom Fork Paths
    // Draw lines from [Dad, Uncle...] up to [Grandpa/Grandma]
    shiftGroups.forEach((groupNodes, mainId) => {
        const mainNode = nodeMap.get(mainId);
        // Find Ancestors (Grandparents)
        // mainNode.children contains Ancestors AND injected siblings of mainNode (if any? no, injected are peers)
        // mainNode.children are Granpa/Grandma.

        const ancestors = mainNode.children || [];
        if (ancestors.length === 0) return;

        // Target Y for the fork bar
        const startY = -mainNode.y; // Level of Dad
        const endY = -ancestors[0].y; // Level of Grandpa
        const midY = (startY + endY) / 2;

        // Draw vertical lines up from each Sibling (and Dad) to MidY
        groupNodes.forEach(n => {
            g.append("path")
                .attr("class", "link-fork")
                .attr("fill", "none")
                .attr("stroke", "black")
                .attr("d", `M${n.x},${startY - cardHeight / 2} V${midY}`);
        });

        // Draw horizontal bar spanning the group
        let minX = Math.min(...groupNodes.map(n => n.x));
        let maxX = Math.max(...groupNodes.map(n => n.x));
        g.append("path")
            .attr("class", "link-fork")
            .attr("fill", "none")
            .attr("stroke", "black")
            .attr("d", `M${minX},${midY} H${maxX}`);

        // Draw vertical lines from MidY up to each Ancestor
        ancestors.forEach(anc => {
            g.append("path")
                .attr("class", "link-fork")
                .attr("fill", "none")
                .attr("stroke", "black")
                .attr("d", `M${anc.x},${midY} V${endY + cardHeight / 2}`);
        });
    });


    const nodes = g.selectAll(".node")
        .data(treeData.descendants())
        .enter().append("g")
        .attr("class", "node-group")
        .attr("transform", d => `translate(${d.x},${-d.y})`);

    // 1. Card Background (Uniform styling)
    nodes.append("rect")
        .attr("class", d => d.data.id === currentRootId ? "node-card is-root" : "node-card")
        .attr("x", -cardWidth / 2)
        .attr("y", -cardHeight / 2)
        .attr("width", cardWidth)
        .attr("height", cardHeight)
        .attr("rx", 10)
        .attr("ry", 10)
        .on("click", (e, d) => {
            e.stopPropagation();
            showLifeSummary(d.data);
        });

    // 2. Text
    nodes.append("foreignObject")
        .attr("x", -cardWidth / 2)
        .attr("y", -cardHeight / 2)
        .attr("width", cardWidth)
        .attr("height", cardHeight)
        .style("overflow", "visible")
        .style("pointer-events", "none")
        .append("xhtml:div")
        .style("width", "100%")
        .style("height", "100%")
        .style("display", "flex")
        .style("flex-direction", "column")
        .style("justify-content", "center")
        .style("align-items", "center")
        .style("text-align", "center")
        .html(d => `
            <div class="node-name-div" style="font-weight: 700; font-size: 16px; color: black">${esc(d.data.name.replace(/\//g, '').trim())}</div>
            <div class="node-details-div" style="font-weight: 600; font-size: 14px; color: black">${esc(d.data.lifeSpan || "")}</div>
        `);

    // 3. Ancestor Toggle (Top)
    nodes.each(function (d) {
        // Ancestors are in 'children' in inverted tree
        // Exclude injected siblings from this check
        const realChildren = d.children ? d.children.filter(c => !c._isMjSiblingOf) : [];
        const hasHiddenChildren = d._children; // We can't easily distinguish hidden types without data check

        // Simple check: if Father or Mother ID exists, we can have ancestors
        if ((d.data.fatherId || d.data.motherId) && d.data.type !== 'sibling') {
            const gBtn = d3.select(this).append("g")
                .attr("class", "node-toggle")
                .attr("transform", `translate(0, ${-cardHeight / 2})`)
                .on("click", (e) => {
                    e.stopPropagation();
                    if (d.children && d.children.some(c => !c._isMjSiblingOf)) {
                        // Collapse
                        d._children = d.children;
                        d.children = null;
                        // Restore visible siblings if any?
                        // To simplify: Collapse Hides ALL (Ancestors + Siblings if they were children? No siblings are peers of Me)
                        // Wait, if I am 'Dad'. Ancestors are 'Grandpa'.
                        // If I toggle Dad, I hide Grandpa.
                    } else if (d._children) {
                        d.children = d._children;
                        d._children = null;
                    }
                    update(d);
                });

            gBtn.append("circle").attr("r", 8);
            gBtn.append("text").attr("dy", "4px").attr("text-anchor", "middle").text((d.children && d.children.some(c => !c._isMjSiblingOf)) ? "-" : "+");
        }

        // 4. Sibling Toggle (Side)
        if (d.data.hasSiblings && d.data.type !== 'sibling') {
            const gSide = d3.select(this).append("g")
                .attr("class", "sibling-toggle")
                .attr("transform", `translate(${cardWidth / 2}, 0)`)
                .style("cursor", "pointer")
                .on("click", (e) => {
                    e.stopPropagation();
                    d.data.showSiblings = !d.data.showSiblings;
                    update(d);
                });

            gSide.append("circle")
                .attr("r", 10)
                .attr("fill", "#333")
                .attr("stroke", "none");

            gSide.append("text")
                .attr("dy", "3px")
                .attr("text-anchor", "middle")
                .attr("fill", "white")
                .style("font-size", "10px")
                .style("font-weight", "bold")
                .text(d.data.showSiblings ? "<" : ">");
        }
    });

    if (!source) {
        svg.call(zoomBehavior.transform, initialTransform());
    }
}

function toggleChildren(d) {
    if (d.data.children) {
        d.data._children = d.data.children;
        d.data.children = null;
    } else {
        d.data.children = d.data._children;
        d.data._children = null;
    }
    update(d);
}

// Sophisticated life summary
function showLifeSummary(person) {
    currentSummaryPerson = person; // Capture globally for export
    const modal = document.getElementById('summary-modal');
    const content = document.getElementById('summary-content');

    const name = person.name.replace(/\//g, '').trim();

    // Sort events safely by year approximation
    const getYear = (dStr) => {
        if (!dStr) return 9999;
        const m = dStr.match(/\d{4}/);
        return m ? parseInt(m[0]) : 9999;
    };

    const events = person.events || [];

    // Build a unified list of things to show
    let timeline = [...events];

    // Lineage isn't in events, add it as a manual item at start
    // (We could keep it separate, but let's put it in flow)

    timeline.sort((a, b) => {
        const ya = getYear(a.date);
        const yb = getYear(b.date);

        // Put Birth first if same year
        if (ya === yb) {
            if (a.type === 'BIRT') return -1;
            if (b.type === 'BIRT') return 1;
            if (a.type === 'DEAT') return 1;
            if (b.type === 'DEAT') return -1;
        }
        return ya - yb;
    });

    // Calculate Age
    let ageStr = "";
    if (person.birthDate && person.deathDate) {
        const by = parseInt(person.birthDate.match(/\d{4}/));
        const dy = parseInt(person.deathDate.match(/\d{4}/));
        if (by && dy) ageStr = `(Aged ${dy - by})`;
    }

    let html = `<div class="story-header">
        <h2>${esc(name)}</h2>
        <div class="story-subtitle">${esc(person.lifeSpan || "")} ${ageStr}</div>
    </div>`;

    html += `<div class="story-timeline">`;

    // SVG Icons
    const icons = {
        star: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>`,
        cross: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>`, // Simple cross
        house: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>`,
        gear: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>`,
        cap: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"></path><path d="M6 12v5c3 3 9 3 12 0v-5"></path></svg>`,
        heart: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path></svg>`,
        baby: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="5"></circle><path d="M3 21v-3a6 6 0 0 1 12 0v3"></path></svg>`, // Approximation (Person)
        flag: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"></path><line x1="4" y1="22" x2="4" y2="15"></line></svg>`,
        tree: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 22 2 22 12 2"></polygon><line x1="12" y1="22" x2="12" y2="16"></line></svg>` // Pine tree approx
    };

    // Lineage Block (Always first)
    if (person.fatherId || person.motherId) {
        const dad = allNodes[person.fatherId];
        const mom = allNodes[person.motherId];
        const dadName = dad ? dad.name.replace(/\//g, '') : null;
        const momName = mom ? mom.name.replace(/\//g, '') : null;
        if (dadName || momName) {
            html += `<div class="story-event">
                <div class="event-icon">${icons.tree}</div>
                <div class="event-content">
                    <h4>Legacy & Lineage</h4>
                    ${dadName ? `<p>Father: <strong>${esc(dadName)}</strong></p>` : ''}
                    ${momName ? `<p>Mother: <strong>${esc(momName)}</strong></p>` : ''}
                </div>
            </div>`;
        }
    }

    // Process Timeline
    timeline.forEach(evt => {
        let icon = icons.flag;
        let title = evt.type;
        let details = [];

        if (evt.date) details.push(`<strong>Date:</strong> ${esc(evt.date)}`);
        if (evt.place) details.push(`<strong>Place:</strong> ${esc(evt.place)}`);
        if (evt.value && evt.type !== 'MARR' && evt.type !== 'CHIL_BIRTH') details.push(`<strong>Details:</strong> ${esc(evt.value)}`);
        // For Marriage/Child, value is the title summary usually, render it cleanly
        if (evt.type === 'MARR' || evt.type === 'CHIL_BIRTH') details.push(`<p>${esc(evt.value)}</p>`);

        if (evt.notes && evt.notes.length > 0) {
            // Escape each note, then join: the <br> is ours, the notes are not.
            details.push(`<div class="event-notes"><em>Notes:</em><br> ${evt.notes.map(esc).join('<br>')}</div>`);
        }

        switch (evt.type) {
            case 'BIRT': icon = icons.star; title = "Birth"; break;
            case 'DEAT': icon = icons.cross; title = "Death"; break;
            case 'RESI': icon = icons.house; title = "Residence"; break;
            case 'OCCU': icon = icons.gear; title = "Occupation"; break;
            case 'EDUC': icon = icons.cap; title = "Education"; break;
            case 'MARR': icon = icons.heart; title = "Marriage"; break;
            case 'CHIL_BIRTH': icon = icons.baby; title = "Child Born"; break;
            // Burial, baptism, census, probate and the rest land here; show a
            // readable name rather than the raw tag.
            default: icon = icons.flag; title = eventLabel(evt.type);
        }

        html += `<div class="story-event">
            <div class="event-icon">${icon}</div>
            <div class="event-content">
                <h4>${esc(title)}</h4>
                ${details.map(d => `<p>${d}</p>`).join('')}
            </div>
        </div>`;
    });

    html += `</div>`;

    html += `<div class="story-footer">
        <p><em>"To forget one's ancestors is to be a brook without a source, a tree without a root."</em></p>
    </div>`;

    content.innerHTML = html;
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');
}

document.getElementById('btn-close-modal').addEventListener('click', () => {
    document.getElementById('summary-modal').style.display = 'none';
    document.body.classList.remove('modal-open');
});

// Print / Export
document.getElementById('btn-export').addEventListener('click', () => {
    document.body.classList.add('print-tree');
    document.body.classList.remove('print-summary');
    window.print();
});

document.getElementById('btn-print-summary').addEventListener('click', () => {
    document.body.classList.add('print-summary');
    document.body.classList.remove('print-tree');
    window.print();
});


// =========================================
// SERVER COMMUNICATION
// =========================================

// Every export used to be a plain navigation (window.location.href). That works
// until the server answers with an error, at which point the browser replaces
// the app with a page of raw JSON. Fetching the file instead lets us keep the
// page and say what went wrong.

function showError(message) {
    const banner = document.getElementById('error-banner');
    if (!banner) { alert(message); return; }
    banner.textContent = message;
    banner.style.display = 'block';
    clearTimeout(showError._timer);
    showError._timer = setTimeout(() => { banner.style.display = 'none'; }, 8000);
}

async function describeFailure(response) {
    // FastAPI sends {"detail": "..."} for HTTPException.
    try {
        const body = await response.json();
        if (body && body.detail) return body.detail;
    } catch (e) { /* not JSON, fall through */ }
    if (response.status === 429) return "Too many requests. Please wait a moment.";
    return `Something went wrong (error ${response.status}).`;
}

function filenameFrom(response, fallback) {
    const disposition = response.headers.get('Content-Disposition') || '';
    // Prefer the RFC 6266 UTF-8 form, which keeps accents intact.
    const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8) { try { return decodeURIComponent(utf8[1]); } catch (e) { /* ignore */ } }
    const plain = disposition.match(/filename="([^"]+)"/i);
    return plain ? plain[1] : fallback;
}

async function downloadFromApi(url, fallbackName, btn, busyText) {
    const oldText = btn ? btn.textContent : null;
    if (btn) { btn.textContent = busyText; btn.disabled = true; }
    try {
        const response = await fetch(url);
        if (!response.ok) {
            const message = await describeFailure(response);
            showError(message);
            if (response.status === 409) sessionExpired();
            return false;
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objectUrl;
        a.download = filenameFrom(response, fallbackName);
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(objectUrl);
        return true;
    } catch (err) {
        console.error(err);
        showError("Could not reach the server. Is it still running?");
        return false;
    } finally {
        if (btn) { btn.textContent = oldText; btn.disabled = false; }
    }
}

function sessionExpired() {
    // The tree lives in memory on the server and is dropped when a session goes
    // idle, so recovery means uploading again.
    allNodes = {};
    rootData = null;
    currentRootId = null;
    searchIndex = [];
    clearSearch();
    g.selectAll("*").remove();
    document.getElementById('upload-text').textContent = "Click to Upload GEDCOM";
    document.getElementById('search-panel').style.display = 'none';
    document.getElementById('stats-panel').style.display = 'none';
    document.getElementById('btn-export').disabled = true;
    document.getElementById('btn-map').disabled = true;
    document.getElementById('btn-timemap').disabled = true;
    document.getElementById('summary-modal').style.display = 'none';
    document.body.classList.remove('modal-open');
}

// Upload Handler
document.getElementById('gedcom-upload').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const label = document.getElementById('upload-text');
    label.textContent = "Parsing...";

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/upload', { method: 'POST', body: formData });
        if (!response.ok) {
            showError(await describeFailure(response));
            label.textContent = "Click to Upload GEDCOM";
            return;
        }
        const data = await response.json();

        allNodes = {};
        data.nodes.forEach(n => {
            if (n.type === 'person') allNodes[n.id] = n;
        });

        const firstPerson = data.nodes.find(n => n.type === 'person');
        const rootId = firstPerson ? firstPerson.id : null;

        if (rootId) {
            currentRootId = rootId;
            rootData = buildAncestorHierarchy(rootId);
            update(null); // Initial render

            label.textContent = "Upload New File";
            buildSearchIndex();
            renderStats(data);
            updateRootNote();
            document.getElementById('search-panel').style.display = 'block';
            document.getElementById('stats-panel').style.display = 'block';
            document.getElementById('btn-export').disabled = false;
            document.getElementById('btn-map').disabled = false;
            document.getElementById('btn-timemap').disabled = false;
        }
    } catch (err) {
        console.error(err);
        showError("Could not reach the server. Is it still running?");
        label.textContent = "Click to Upload GEDCOM";
    }
});

document.getElementById('btn-reset').addEventListener('click', () => {
    if (rootData) resetView();
});

// =========================================
// A3 EXPORT LOGIC
// =========================================

document.getElementById('btn-export-image').addEventListener('click', () => {
    // Current person is unfortunately not global, but we can grab from modal context
    // Wait, showLifeSummary is just a function. We need to store 'currentSummaryPerson'.
    // Let's rely on the DOM for now or better, update showLifeSummary to store state.
    if (!currentSummaryPerson) {
        alert("Please open a person's summary first.");
        return;
    }

    // Change button text
    const btn = document.getElementById('btn-export-image');
    const oldText = btn.textContent;
    btn.textContent = "Generating High-Res Image...";
    btn.disabled = true;

    // Wait a tick for UI update
    setTimeout(() => {
        generateA3Export(currentSummaryPerson).then(() => {
            btn.textContent = oldText;
            btn.disabled = false;
        });
    }, 100);
});

// (Monkey patch removed - logic moved to showLifeSummary definition)
let currentSummaryPerson = null;

async function generateA3Export(person) {
    if (!person) return;

    // 1. Populate the A2 Template
    // Use Safe Name
    const safeName = person.name.replace(/\//g, '').trim();
    document.getElementById('export-name').textContent = safeName;
    document.getElementById('export-dates').textContent = person.lifeSpan || "";

    // ---------------------------------------------------------
    // HORIZONTAL D3 TIMELINE
    // ---------------------------------------------------------
    const timelineContainer = document.getElementById('export-timeline-container');
    timelineContainer.innerHTML = '';

    const events = person.events || [];
    const parseYear = (d) => {
        if (!d) return null;
        const m = d.match(/(\d{4})/);
        return m ? new Date(parseInt(m[0]), 0, 1) : null;
    };

    const validEvents = events
        .map(e => ({ ...e, dt: parseYear(e.date) }))
        .filter(e => e.dt !== null)
        .sort((a, b) => a.dt - b.dt);

    if (validEvents.length > 0) {
        // Dimensions matches CSS .a3-timeline-section height approx 450px
        // Width is full container width. A2 page width ~4961px minus padding.
        // Let's grab computed width or assume safe width of ~4500px
        const width = 4500;
        const height = 450;
        const margin = { top: 50, right: 100, bottom: 100, left: 100 };

        const svg = d3.select(timelineContainer).append("svg")
            .attr("width", width)
            .attr("height", height)
            .style("font-family", "'Cormorant SC', serif"); // Enforce Font

        // Scale
        const minDate = validEvents[0].dt;
        const maxDate = validEvents[validEvents.length - 1].dt;
        // Add buffer
        const startDomain = new Date(minDate); startDomain.setFullYear(startDomain.getFullYear() - 5);
        const endDomain = new Date(maxDate); endDomain.setFullYear(endDomain.getFullYear() + 5);

        const x = d3.scaleTime()
            .domain([startDomain, endDomain])
            .range([margin.left, width - margin.right]);

        // Axis
        const xAxis = d3.axisBottom(x)
            .ticks(d3.timeYear.every(10))
            .tickFormat(d3.timeFormat("%Y"))
            .tickSize(20)
            .tickPadding(15);

        svg.append("g")
            .attr("class", "timeline-axis")
            .attr("transform", `translate(0, ${height / 2})`)
            .call(xAxis)
            .selectAll("text")
            .style("font-size", "48px") // Big text for A2
            .style("font-family", "'Cormorant SC', serif")
            .style("font-weight", "700");

        // Style the axis line
        svg.selectAll(".domain").attr("stroke-width", "6px").attr("stroke", "black");
        svg.selectAll(".tick line").attr("stroke-width", "4px").attr("stroke", "black");

        // Circles & Labels
        const g = svg.append("g");

        validEvents.forEach((d, i) => {
            const cx = x(d.dt);
            const cy = height / 2;
            const isUp = i % 2 === 0; // Alternate labels
            const yOffset = isUp ? -60 : 60;

            // Marker
            g.append("circle")
                .attr("cx", cx)
                .attr("cy", cy)
                .attr("r", 15)
                .attr("fill", "black");

            // Line to label
            g.append("line")
                .attr("x1", cx)
                .attr("y1", cy + (isUp ? -20 : 20))
                .attr("x2", cx)
                .attr("y2", cy + yOffset)
                .attr("stroke", "black")
                .attr("stroke-width", "3");

            // Label Text Group
            const labelG = g.append("g")
                .attr("transform", `translate(${cx}, ${cy + yOffset + (isUp ? -10 : 25)})`);

            // Event Type (Bold)
            labelG.append("text")
                .attr("text-anchor", "middle")
                .style("font-size", "40px")
                .style("font-weight", "bold")
                .style("font-family", "'Cormorant SC', serif")
                .attr("dy", isUp ? "0" : "0.8em")
                .text(eventLabel(d.type));

            // Details/Date (Smaller)
            // Use date string if available
            labelG.append("text")
                .attr("text-anchor", "middle")
                .style("font-size", "32px")
                .style("font-family", "'Cormorant SC', serif")
                .style("fill", "#444")
                .attr("dy", isUp ? "-1.2em" : "2.2em")
                .text(d.date || "");
        });
    }

    // ---------------------------------------------------------

    // Bio Column
    const bioContainer = document.getElementById('export-bio-content');
    bioContainer.innerHTML = `
        <div class="export-bio-row"><span class="export-bio-label">Sex:</span><span class="export-bio-value">${esc(person.sex)}</span></div>
        <div class="export-bio-row"><span class="export-bio-label">Birth:</span><span class="export-bio-value">${esc(person.birthDate || "?")}</span></div>
        <div class="export-bio-row"><span class="export-bio-label">Death:</span><span class="export-bio-value">${esc(person.deathDate || "?")}</span></div>
    `;

    // Siblings
    const sibContainer = document.getElementById('export-siblings-list');
    sibContainer.innerHTML = "";
    if (person.siblingIds && person.siblingIds.length > 0) {
        person.siblingIds.forEach(sid => {
            const sib = allNodes[sid];
            if (sib) {
                sibContainer.innerHTML += `
                <div class="export-sibling-card">
                    <div class="export-sibling-name">${esc(sib.name.replace(/\//g, ''))}</div>
                    <div class="export-sibling-dates">${esc(sib.lifeSpan || "")}</div>
                </div>`;
            }
        });
    } else {
        sibContainer.innerHTML = "<p>No recorded siblings.</p>";
    }

    // 2. Show Preview Mode
    document.body.classList.add('preview-active');

    // Wait for render
    await new Promise(r => setTimeout(r, 500));
}

// Preview Controls
document.getElementById('btn-cancel-preview').addEventListener('click', () => {
    document.body.classList.remove('preview-active');
});

document.getElementById('btn-download-image').addEventListener('click', () => {
    const node = document.getElementById('export-a3-page');

    const btn = document.getElementById('btn-download-image');
    btn.textContent = "Rendering High-Res A2...";
    btn.disabled = true;

    domtoimage.toPng(node, {
        width: 4961, // A2 Width
        height: 3508, // A2 Height
        style: {
            transform: 'scale(1)',
            transformOrigin: 'top left'
        }
    })
        .then(function (dataUrl) {
            const link = document.createElement('a');
            link.download = `${currentSummaryPerson.name.replace(/\//g, '').trim()}_A2_Poster.png`;
            link.href = dataUrl;
            link.click();

            btn.textContent = "Download High-Res PNG";
            btn.disabled = false;
            document.body.classList.remove('preview-active');
        })
        .catch(function (error) {
            console.error('oops, something went wrong!', error);
            btn.textContent = "Error!";
            // ... (previous code)
        });
});

document.getElementById('btn-export-pdf').addEventListener('click', () => {
    if (!currentSummaryPerson || !currentSummaryPerson.id) {
        showError("No person selected.");
        return;
    }
    const safe = currentSummaryPerson.name.replace(/\//g, '').trim();
    downloadFromApi(
        `/api/export/pdf/${encodeURIComponent(currentSummaryPerson.id)}`,
        `${safe}_A2_Poster.pdf`,
        document.getElementById('btn-export-pdf'),
        "Generating PDF...");
});

document.getElementById('btn-map').addEventListener('click', () => {
    // Geocoding is rate limited to one lookup per second, so a first run on a
    // large tree genuinely takes minutes. Results are cached server side.
    downloadFromApi(
        '/api/export/map',
        'Family_Map.pdf',
        document.getElementById('btn-map'),
        "Mapping... (may take a while)");
});

document.getElementById('btn-export-zip').addEventListener('click', () => {
    if (!currentSummaryPerson || !currentSummaryPerson.id) {
        showError("No person selected.");
        return;
    }
    const safe = currentSummaryPerson.name.replace(/\//g, '').trim();
    downloadFromApi(
        `/api/export/generations/${encodeURIComponent(currentSummaryPerson.id)}`,
        `${safe}_Generational_Series.zip`,
        document.getElementById('btn-export-zip'),
        "Zipping Series...");
});


// =========================================
// FIND A PERSON
// =========================================

// Without this the only way to reach anyone is to expand the chart outwards
// from whoever the file happened to list first, which on a 144 person tree
// means a lot of clicking to find a name you already know.

const MAX_RESULTS = 12;

let searchIndex = [];
let searchSelection = -1;

const searchInput = document.getElementById('person-search');
const searchResults = document.getElementById('search-results');
const searchHint = document.getElementById('search-hint');
const searchClear = document.getElementById('btn-search-clear');

// GEDCOM writes names as "John /Smith/", with the surname delimited.
function cleanName(raw) {
    return String(raw || '').replace(/\//g, ' ').replace(/\s+/g, ' ').trim();
}

function buildSearchIndex() {
    searchIndex = Object.values(allNodes).map(p => {
        const name = cleanName(p.name) || 'Unknown';
        return {
            id: p.id,
            name,
            haystack: name.toLowerCase(),
            lifeSpan: p.lifeSpan || '',
            birthPlace: p.birthPlace || ''
        };
    }).sort((a, b) => a.name.localeCompare(b.name));
}

// Escape around the matched run rather than escaping the whole string and then
// inserting tags into it, which would mean matching offsets against text that
// has changed length.
function highlight(name, at, length) {
    if (at < 0) return esc(name);
    return esc(name.slice(0, at)) +
        '<mark>' + esc(name.slice(at, at + length)) + '</mark>' +
        esc(name.slice(at + length));
}

function searchFor(query) {
    const q = query.trim().toLowerCase();
    if (!q) return { hits: [], total: 0, q };

    const hits = [];
    for (const entry of searchIndex) {
        const at = entry.haystack.indexOf(q);
        if (at === -1) continue;
        // A match at the start of the string or of any word beats one buried
        // mid-word, so typing "smi" surfaces Smith before Nasmith.
        const atWordStart = at === 0 || entry.haystack[at - 1] === ' ';
        hits.push({ entry, at, rank: at === 0 ? 0 : (atWordStart ? 1 : 2) });
    }

    hits.sort((a, b) => a.rank - b.rank || a.at - b.at ||
        a.entry.name.localeCompare(b.entry.name));
    return { hits: hits.slice(0, MAX_RESULTS), total: hits.length, q };
}

function renderSearchResults(query) {
    searchSelection = -1;
    searchClear.style.display = query ? 'block' : 'none';

    if (!query.trim()) {
        searchResults.innerHTML = '';
        searchHint.textContent = '';
        searchInput.setAttribute('aria-expanded', 'false');
        return;
    }

    const { hits, total, q } = searchFor(query);

    if (!hits.length) {
        searchResults.innerHTML = '';
        searchHint.textContent = 'No one by that name in this file.';
        searchInput.setAttribute('aria-expanded', 'false');
        return;
    }

    searchResults.innerHTML = hits.map(({ entry, at }) => {
        const meta = [entry.lifeSpan, entry.birthPlace].filter(Boolean).join(' · ');
        return '<li role="option" data-id="' + esc(entry.id) + '" aria-selected="false">' +
            '<span class="result-name">' + highlight(entry.name, at, q.length) + '</span>' +
            (meta ? '<span class="result-meta">' + esc(meta) + '</span>' : '') +
            '</li>';
    }).join('');

    searchHint.textContent = total > hits.length
        ? 'Showing ' + hits.length + ' of ' + total + ' matches. Keep typing to narrow it.'
        : 'Choosing someone redraws the chart from them.';
    searchInput.setAttribute('aria-expanded', 'true');
}

function searchOptions() {
    return Array.from(searchResults.querySelectorAll('li'));
}

function moveSearchSelection(delta) {
    const options = searchOptions();
    if (!options.length) return;

    searchSelection = (searchSelection + delta + options.length) % options.length;
    options.forEach((li, i) => {
        const on = i === searchSelection;
        li.setAttribute('aria-selected', on ? 'true' : 'false');
        if (on) li.scrollIntoView({ block: 'nearest' });
    });
}

function clearSearch() {
    if (!searchInput) return;
    searchInput.value = '';
    searchResults.innerHTML = '';
    searchHint.textContent = '';
    searchClear.style.display = 'none';
    searchSelection = -1;
    searchInput.setAttribute('aria-expanded', 'false');
}

searchInput.addEventListener('input', (e) => renderSearchResults(e.target.value));

searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveSearchSelection(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveSearchSelection(-1); }
    else if (e.key === 'Escape') { clearSearch(); }
    else if (e.key === 'Enter') {
        e.preventDefault();
        const options = searchOptions();
        // Enter with nothing highlighted takes the top match, which is what
        // type-and-return is asking for.
        const target = options[searchSelection >= 0 ? searchSelection : 0];
        if (target) focusPerson(target.dataset.id);
    }
});

searchResults.addEventListener('click', (e) => {
    const li = e.target.closest('li[data-id]');
    if (li) focusPerson(li.dataset.id);
});

searchClear.addEventListener('click', () => { clearSearch(); searchInput.focus(); });

// Ctrl/Cmd+K to jump to the box, which is where everyone's hands already go.
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        if (document.getElementById('search-panel').style.display === 'none') return;
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
    }
});


// =========================================
// RE-ROOTING THE CHART
// =========================================

// The parser takes the first INDI in the file as the root, which is whoever
// the exporting software wrote first rather than whoever is holding the file.
// Relationship labels and the generation series are both stated relative to
// that person, so the server has to be told as well as the chart, otherwise an
// export would disagree with the screen.

async function focusPerson(personId) {
    const person = allNodes[personId];
    if (!person) return;

    try {
        const response = await fetch('/api/root/' + encodeURIComponent(personId),
            { method: 'POST' });
        if (!response.ok) {
            showError(await describeFailure(response));
            if (response.status === 409) sessionExpired();
            return;
        }
    } catch (err) {
        console.error(err);
        showError("Could not reach the server. Is it still running?");
        return;
    }

    currentRootId = personId;
    rootData = buildAncestorHierarchy(personId);
    update(null);
    updateRootNote();
    clearSearch();

    // Close the summary if the re-root was triggered from inside it, so the
    // redrawn chart is actually visible.
    document.getElementById('summary-modal').style.display = 'none';
    document.body.classList.remove('modal-open');
}

function updateRootNote() {
    const note = document.getElementById('root-note');
    if (!note) return;

    const person = allNodes[currentRootId];
    if (!person) { note.innerHTML = ''; return; }

    note.innerHTML = 'Drawn from <strong>' +
        esc(cleanName(person.name) || 'Unknown') + '</strong>. ' +
        'Relationships and exports are stated relative to them.';
}

document.getElementById('btn-set-root').addEventListener('click', () => {
    if (currentSummaryPerson) focusPerson(currentSummaryPerson.id);
});


// =========================================
// TREE STATISTICS
// =========================================

function renderStats(data) {
    const people = Object.values(allNodes);

    const families = (data.nodes || []).filter(n => n.type === 'family').length;

    const surnames = new Set();
    for (const p of people) {
        // A surname needs at least one letter to count. Unknown surnames come
        // through as "?" and counting those as a distinct family name both
        // inflates the figure and reads as a mistake.
        const surname = (p.surname || '').trim();
        if (/\p{L}/u.test(surname)) surnames.add(surname.toLowerCase());
    }

    // Widest dated range in the file. Scanning every event rather than just
    // births and deaths, because a census or a residence is often the only
    // dated thing on someone.
    let earliest = null, latest = null;
    const thisYear = new Date().getFullYear();
    for (const p of people) {
        const dates = [p.birthDate, p.deathDate].concat(
            (p.events || []).map(e => e.date));
        for (const d of dates) {
            const m = String(d || '').match(/\d{4}/);
            if (!m) continue;
            const year = parseInt(m[0], 10);
            // Guard against a typo'd or placeholder year dragging the range out
            // to something silly like 0001 or 9999.
            if (year < 1000 || year > thisYear) continue;
            if (earliest === null || year < earliest) earliest = year;
            if (latest === null || year > latest) latest = year;
        }
    }

    document.getElementById('stat-indi').textContent = people.length;
    document.getElementById('stat-fam').textContent = families;
    document.getElementById('stat-surnames').textContent = surnames.size;
    document.getElementById('stat-span').textContent =
        (earliest && latest) ? earliest + '–' + latest : '—';
}


// =========================================
// KEYBOARD: CLOSING THE SUMMARY
// =========================================

// The timemap overlay has its own Escape handler that guards on being open, so
// this one guards the same way round and the two never both fire.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('summary-modal');
    if (!modal || modal.style.display === 'none' || !modal.style.display) return;
    modal.style.display = 'none';
    document.body.classList.remove('modal-open');
});
