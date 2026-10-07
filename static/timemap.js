// ===========================================================================
// Map through time
//
// The static map export draws every place anyone ever lived on one sheet. This
// view asks the narrower and more interesting question: in a given year, who
// was alive and where were they living? Census returns are the bulk of a
// typical tree, so the data already answers it, year by year.
//
// The server hands over the whole series in one response (about 60 KB for 144
// people), so dragging the slider is local work and never waits on a request.
//
// Depends on showError, describeFailure and sessionExpired from script.js,
// which is loaded first.
// ===========================================================================

const timeMap = {
    data: null,         // the /api/map/timeline payload
    land: null,         // basemap GeoJSON
    projection: null,
    path: null,
    year: null,
    playing: false,
    timer: null,
    loaded: false,
};

// One step per year, fast enough to feel like motion and slow enough to read.
const TIMEMAP_FRAME_MS = 170;

// How far back a trail reaches. A full lifetime of lines for 134 people turns
// the map into a ball of wool, and the interesting thing is recent movement
// anyway, so only the last few decades are drawn.
const TIMEMAP_TRAIL_YEARS = 30;

function timeMapEl(id) {
    return document.getElementById(id);
}

/** Where a person was in a given year: the last place recorded at or before it.
 *
 * Returns null when the file has not caught up with them yet, which is the
 * honest answer for someone alive but with no located record until later.
 */
function placeAt(person, year) {
    let found = null;
    for (const move of person.moves) {
        if (move.year > year) break;
        found = move;
    }
    return found;
}

function isAlive(person, year) {
    return person.birth <= year && year <= person.end;
}

async function openTimeMap() {
    const overlay = timeMapEl('timemap-overlay');
    const btn = timeMapEl('btn-timemap');

    if (!timeMap.loaded) {
        const oldText = btn.textContent;
        btn.textContent = 'Loading...';
        btn.disabled = true;
        try {
            const [timelineRes, landRes] = await Promise.all([
                fetch('/api/map/timeline'),
                fetch('/api/map/basemap'),
            ]);
            if (!timelineRes.ok) {
                showError(await describeFailure(timelineRes));
                if (timelineRes.status === 409) sessionExpired();
                return;
            }
            if (!landRes.ok) {
                showError('Could not load the map outline.');
                return;
            }
            timeMap.data = await timelineRes.json();
            timeMap.land = await landRes.json();
            timeMap.loaded = true;
        } catch (e) {
            showError('Could not reach the server. Is it still running?');
            return;
        } finally {
            btn.textContent = oldText;
            btn.disabled = false;
        }
    }

    overlay.style.display = 'flex';
    setupTimeMapChrome();
    // The SVG has no size until the overlay is displayed, so fit afterwards.
    fitTimeMapProjection();
    setTimeMapYear(timeMap.year === null ? timeMap.data.span.from : timeMap.year);
}

function closeTimeMap() {
    stopTimeMapPlayback();
    timeMapEl('timemap-overlay').style.display = 'none';
}

function setupTimeMapChrome() {
    const span = timeMap.data.span;
    const stats = timeMap.data.stats;
    const slider = timeMapEl('timemap-slider');
    slider.min = span.from;
    slider.max = span.to;
    if (timeMap.year === null) slider.value = span.from;

    timeMapEl('timemap-scale').textContent = span.from + ' to ' + span.to;

    // Say plainly what is not on the map. A view that silently drops a tenth of
    // the family looks authoritative and is not.
    const notes = [stats.placed + ' of ' + stats.people + ' people placed'];
    if (stats.undated) notes.push(stats.undated + ' with no usable dates');
    if (stats.unresolvedPlaces) {
        notes.push(stats.unresolvedPlaces + ' place'
            + (stats.unresolvedPlaces === 1 ? '' : 's') + ' could not be located');
    }
    timeMapEl('timemap-note').textContent = notes.join(' · ');
}

// Never zoom tighter than this many degrees. A family that never left one town
// would otherwise fill the screen with a single parish and no context, which
// tells you nothing about where in the country they were.
const TIMEMAP_MIN_SPAN_LAT = 1.8;
const TIMEMAP_MIN_SPAN_LON = 2.2;

/** A box around the places this family actually touched.
 *
 * Framing the whole of Britain and Ireland wastes most of the screen on sea
 * when, as here, everyone lived within forty miles of each other. Fitting to
 * the data instead means the coastline runs off the edges, which is fine: the
 * pins are the subject and the shape around them is only orientation.
 */
function timeMapDataBounds() {
    const places = timeMap.data.places;
    if (!places.length) return null;

    let minLon = Infinity, maxLon = -Infinity;
    let minLat = Infinity, maxLat = -Infinity;
    for (const place of places) {
        if (place.lon < minLon) minLon = place.lon;
        if (place.lon > maxLon) maxLon = place.lon;
        if (place.lat < minLat) minLat = place.lat;
        if (place.lat > maxLat) maxLat = place.lat;
    }

    const grow = (lo, hi, minSpan) => {
        const pad = Math.max((hi - lo) * 0.18, 0.15);
        lo -= pad;
        hi += pad;
        const short = minSpan - (hi - lo);
        if (short > 0) {
            lo -= short / 2;
            hi += short / 2;
        }
        return [lo, hi];
    };

    [minLon, maxLon] = grow(minLon, maxLon, TIMEMAP_MIN_SPAN_LON);
    [minLat, maxLat] = grow(minLat, maxLat, TIMEMAP_MIN_SPAN_LAT);

    // A MultiPoint of the corners, not a Polygon. d3 reads a polygon ring's
    // winding order as meaning which side is inside, and a box wound the wrong
    // way is taken to be the whole sphere minus the box, which collapses the
    // scale to nothing. Points carry no winding, so this cannot go wrong.
    return {
        type: 'MultiPoint',
        coordinates: [
            [minLon, minLat], [maxLon, minLat],
            [maxLon, maxLat], [minLon, maxLat],
        ],
    };
}

function fitTimeMapProjection() {
    const svg = d3.select('#timemap-svg');
    const node = svg.node();
    const width = node.clientWidth || 900;
    const height = node.clientHeight || 600;

    timeMap.projection = d3.geoMercator()
        .fitExtent([[24, 24], [width - 24, height - 24]],
                   timeMapDataBounds() || timeMap.land);
    timeMap.path = d3.geoPath(timeMap.projection);

    svg.selectAll('*').remove();
    svg.append('rect')
        .attr('class', 'timemap-sea')
        .attr('width', width)
        .attr('height', height);
    svg.append('g').attr('class', 'timemap-land-layer')
        .selectAll('path')
        .data(timeMap.land.features)
        .join('path')
        .attr('class', 'timemap-land')
        .attr('d', timeMap.path);

    // Drawn in this order so pins sit above trails and labels above pins.
    svg.append('g').attr('class', 'timemap-trails-layer');
    svg.append('g').attr('class', 'timemap-pins-layer');
    svg.append('g').attr('class', 'timemap-labels-layer');
}

function projectPlace(place) {
    return timeMap.projection([place.lon, place.lat]);
}

/** Everything the view needs for one year, computed from the payload. */
function timeMapFrame(year) {
    const people = timeMap.data.people;
    const places = timeMap.data.places;

    const byPin = new Map();   // pin index -> tallies for that point
    const happenings = [];
    const trails = [];
    let aliveCount = 0;
    let unplacedCount = 0;

    for (const person of people) {
        if (!isAlive(person, year)) continue;
        aliveCount++;

        const move = placeAt(person, year);
        if (!move) { unplacedCount++; continue; }

        let bucket = byPin.get(move.place);
        if (!bucket) {
            bucket = { living: 0, births: 0, deaths: 0, approx: 0, names: [] };
            byPin.set(move.place, bucket);
        }
        bucket.living++;
        if (move.approx) bucket.approx++;
        if (bucket.names.length < 12) bucket.names.push(person.name);

        const bornHere = person.birth === year && move.type === 'BIRT';
        const diedHere = person.end === year && person.deathKnown;
        if (bornHere) {
            bucket.births++;
            happenings.push({
                kind: 'birth',
                text: person.name + ' born at',
                place: places[move.place].name,
            });
        } else if (diedHere) {
            bucket.deaths++;
            happenings.push({
                kind: 'death',
                text: person.name + ' died at',
                place: places[move.place].name,
            });
        } else if (move.year === year) {
            happenings.push({
                kind: 'move',
                text: person.name + ' recorded at',
                place: places[move.place].name,
            });
        }

        // Trail: the legs this person travelled in the recent past.
        const recent = person.moves.filter(
            m => m.year <= year && m.year >= year - TIMEMAP_TRAIL_YEARS);
        for (let i = 1; i < recent.length; i++) {
            if (recent[i].place === recent[i - 1].place) continue;
            trails.push([places[recent[i - 1].place], places[recent[i].place]]);
        }
    }

    return { byPin, happenings, trails, aliveCount, unplacedCount };
}

function timeMapRadius(n) {
    return 5 + Math.sqrt(n) * 3.2;
}

function renderTimeMap(year) {
    if (!timeMap.projection) return;
    const places = timeMap.data.places;
    const frame = timeMapFrame(year);
    const svg = d3.select('#timemap-svg');
    const showTrails = timeMapEl('timemap-trails').checked;

    // --- trails -----------------------------------------------------------
    svg.select('.timemap-trails-layer')
        .selectAll('line')
        .data(showTrails ? frame.trails : [])
        .join('line')
        .attr('class', 'timemap-trail')
        .attr('x1', d => projectPlace(d[0])[0])
        .attr('y1', d => projectPlace(d[0])[1])
        .attr('x2', d => projectPlace(d[1])[0])
        .attr('y2', d => projectPlace(d[1])[1]);

    // --- pins -------------------------------------------------------------
    const pinData = Array.from(frame.byPin.entries()).map(entry => ({
        index: entry[0],
        bucket: entry[1],
        place: places[entry[0]],
    }));
    // Biggest first so a crowded city does not hide a single person beside it.
    pinData.sort((a, b) => b.bucket.living - a.bucket.living);

    svg.select('.timemap-pins-layer')
        .selectAll('circle')
        .data(pinData, d => d.index)
        .join('circle')
        .attr('class', d => {
            const b = d.bucket;
            const tone = b.births ? 'birth' : (b.deaths ? 'death' : 'living');
            // A pin is marked approximate only when every person on it is.
            const approx = b.approx === b.living ? ' approx' : '';
            return 'timemap-pin ' + tone + approx;
        })
        .attr('cx', d => projectPlace(d.place)[0])
        .attr('cy', d => projectPlace(d.place)[1])
        .attr('r', d => timeMapRadius(d.bucket.living))
        .each(function (d) {
            const more = d.bucket.living - d.bucket.names.length;
            const sel = d3.select(this);
            sel.selectAll('title').remove();
            // A title element takes text, never markup, so GEDCOM names are
            // safe here without further escaping.
            sel.append('title').text(
                d.place.name + '\n'
                + d.bucket.living + ' here in ' + year + '\n'
                + d.bucket.names.join(', ')
                + (more > 0 ? ', and ' + more + ' more' : ''));
        });

    // --- counts and labels ------------------------------------------------
    // Only the busier pins get a number, and only the busiest get a name, so
    // the map stays readable when a whole family is in one town.
    svg.select('.timemap-labels-layer')
        .selectAll('text.timemap-pin-count')
        .data(pinData.filter(d => d.bucket.living > 1), d => d.index)
        .join('text')
        .attr('class', 'timemap-pin-count')
        .attr('x', d => projectPlace(d.place)[0])
        .attr('y', d => projectPlace(d.place)[1])
        .text(d => d.bucket.living);

    svg.select('.timemap-labels-layer')
        .selectAll('text.timemap-pin-label')
        .data(pinData.slice(0, 7), d => d.index)
        .join('text')
        .attr('class', 'timemap-pin-label')
        .attr('x', d => projectPlace(d.place)[0] + timeMapRadius(d.bucket.living) + 4)
        .attr('y', d => projectPlace(d.place)[1] + 3)
        .text(d => d.place.name.split(',')[0]);

    // --- readout ----------------------------------------------------------
    timeMapEl('timemap-year').textContent = year;
    const counts = [frame.aliveCount + ' living'];
    if (frame.unplacedCount) counts.push(frame.unplacedCount + ' not yet located');
    counts.push(frame.byPin.size + ' place' + (frame.byPin.size === 1 ? '' : 's'));
    timeMapEl('timemap-counts').textContent = counts.join(' · ');

    const list = timeMapEl('timemap-happenings');
    list.innerHTML = '';
    const order = { birth: 0, death: 1, move: 2 };
    frame.happenings
        .sort((a, b) => order[a.kind] - order[b.kind])
        .slice(0, 9)
        .forEach(h => {
            const li = document.createElement('li');
            li.className = 'ev-' + h.kind;
            // Names and places are file data, so build this from a text node
            // rather than innerHTML: a crafted GEDCOM cannot inject markup
            // through textContent.
            li.textContent = h.text + ' ' + h.place;
            list.appendChild(li);
        });
}

function setTimeMapYear(year) {
    const span = timeMap.data.span;
    year = Math.max(span.from, Math.min(span.to, Math.round(year)));
    timeMap.year = year;
    timeMapEl('timemap-slider').value = year;
    renderTimeMap(year);
}

function stepTimeMap() {
    const span = timeMap.data.span;
    const next = timeMap.year >= span.to ? span.from : timeMap.year + 1;
    setTimeMapYear(next);
}

function startTimeMapPlayback() {
    if (timeMap.playing) return;
    timeMap.playing = true;
    timeMapEl('btn-timemap-play').innerHTML = '&#10073;&#10073; Pause';
    timeMap.timer = setInterval(stepTimeMap, TIMEMAP_FRAME_MS);
}

function stopTimeMapPlayback() {
    timeMap.playing = false;
    clearInterval(timeMap.timer);
    timeMap.timer = null;
    const btn = timeMapEl('btn-timemap-play');
    if (btn) btn.innerHTML = '&#9654; Play';
}

function timeMapIsOpen() {
    return timeMapEl('timemap-overlay').style.display !== 'none';
}

// --- wiring ----------------------------------------------------------------

timeMapEl('btn-timemap').addEventListener('click', openTimeMap);
timeMapEl('btn-close-timemap').addEventListener('click', closeTimeMap);

timeMapEl('btn-timemap-play').addEventListener('click', () => {
    if (timeMap.playing) { stopTimeMapPlayback(); } else { startTimeMapPlayback(); }
});

timeMapEl('timemap-slider').addEventListener('input', (e) => {
    // Dragging means the user is driving, so get out of the way.
    stopTimeMapPlayback();
    setTimeMapYear(Number(e.target.value));
});

timeMapEl('timemap-trails').addEventListener('change', () => {
    if (timeMap.year !== null) renderTimeMap(timeMap.year);
});

document.addEventListener('keydown', (e) => {
    if (!timeMapIsOpen()) return;
    if (e.key === 'Escape') { closeTimeMap(); return; }
    if (e.key === 'ArrowLeft') {
        stopTimeMapPlayback();
        setTimeMapYear(timeMap.year - 1);
    }
    if (e.key === 'ArrowRight') {
        stopTimeMapPlayback();
        setTimeMapYear(timeMap.year + 1);
    }
    if (e.key === ' ') {
        e.preventDefault();
        if (timeMap.playing) { stopTimeMapPlayback(); } else { startTimeMapPlayback(); }
    }
});

window.addEventListener('resize', () => {
    if (!timeMapIsOpen() || !timeMap.loaded) return;
    fitTimeMapProjection();
    renderTimeMap(timeMap.year);
});
