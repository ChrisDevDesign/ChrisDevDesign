"""Generate contained, animated GitHub contribution SVGs using only the stdlib.

Live: GITHUB_TOKEN=... GH_USERNAME=ChrisDevDesign python scripts/generate_snake.py
Offline: python scripts/generate_snake.py --calendar-json calendar.json --output-dir dist
Offline input must be a direct GitHub contributionsCalendar object. Synthetic
fixtures are for local tests only; never use their output for publication.
"""

import argparse
from collections import deque
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr


LEVELS = ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
MONTHS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
PITCH = 18
STEP_SECONDS = 0.09
ORIGIN = (78, 194)


def fetch_calendar(username):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is required unless --calendar-json is supplied")
    query = """query($login:String!){user(login:$login){contributionsCollection{
      contributionCalendar{totalContributions weeks{contributionDays{
        date weekday contributionCount contributionLevel
      }}}}}}"""
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"login": username}}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "User-Agent": "contribution-snake-stdlib"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"GitHub API returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise ValueError("Cannot reach GitHub API") from None
    if result.get("errors"):
        raise ValueError("GitHub GraphQL reported an error; check token access and username")
    user = result.get("data", {}).get("user")
    if not user:
        raise ValueError("GitHub user was not found")
    return user["contributionsCollection"]["contributionCalendar"]


def validate_calendar(calendar):
    if not isinstance(calendar, dict):
        raise ValueError("Calendar must be a JSON object")
    total = calendar.get("totalContributions")
    if type(total) is not int or total < 0:
        raise ValueError("totalContributions must be a nonnegative integer")
    weeks = calendar.get("weeks")
    if not isinstance(weeks, list) or not 1 <= len(weeks) <= 54:
        raise ValueError("Expected 1 to 54 calendar weeks")
    days, seen = [], set()
    for col, week in enumerate(weeks):
        entries = week.get("contributionDays") if isinstance(week, dict) else None
        if not isinstance(entries, list) or not 1 <= len(entries) <= 7:
            raise ValueError("Each week must contain 1 to 7 days")
        rows = set()
        for entry in entries:
            date = dt.date.fromisoformat(entry["date"])
            row, count = entry["weekday"], entry["contributionCount"]
            if type(row) is not int or row != (date.weekday() + 1) % 7:
                raise ValueError("weekday does not match date")
            if row in rows or date in seen:
                raise ValueError("Duplicate day or weekday")
            if type(count) is not int or count < 0 or entry["contributionLevel"] not in LEVELS:
                raise ValueError("Invalid contribution count or level")
            rows.add(row)
            seen.add(date)
            days.append((date, col, row, count, LEVELS.index(entry["contributionLevel"])))
    days.sort()
    for previous, current in zip(days, days[1:]):
        if current[0] - previous[0] != dt.timedelta(days=1):
            raise ValueError("Calendar dates must be consecutive")
        prev_sunday = previous[0] - dt.timedelta(days=previous[2])
        this_sunday = current[0] - dt.timedelta(days=current[2])
        if current[1] - previous[1] != (this_sunday - prev_sunday).days // 7:
            raise ValueError("Dates must occupy consecutive Sunday-based week columns")
    return days



def _neighbors(cell, columns):
    x, y = cell
    # Stable tie order: right first, then down, left, up.
    return tuple((a, b) for a, b in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1))
                 if 0 <= a < columns and 0 <= b < 7)


def _hamiltonian(columns):
    """Even-width seven-row cycle, beginning at (0,3), heading right."""
    cells = [(x, 3) for x in range(columns - 1)]
    cells += [(x, y) for x in range(columns - 2, -1, -1)
              for y in ((4, 5) if x % 2 == 0 else (5, 4))]
    cells += [(x, 6) for x in range(columns)]
    cells += [(columns - 1, y) for y in (5, 4, 3)]
    cells += [(x, y) for x in range(columns - 1, -1, -1)
              for y in ((2, 1, 0) if x % 2 else (0, 1, 2))]
    assert len(set(cells)) == columns * 7
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
               for a, b in zip(cells, cells[1:] + cells[:1]))
    return tuple(cells)


def _advance(body, cell, growing=False):
    """Legal one-cell Snake tick. The tail may vacate only when not growing."""
    head = body[0]
    if abs(head[0] - cell[0]) + abs(head[1] - cell[1]) != 1:
        return None
    if len(body) > 1 and cell == body[1]:
        return None
    if cell in (body if growing else body[:-1]):
        return None
    return (cell,) + (body if growing else body[:-1])


def _rectangle(columns, length):
    # Prefer the centered five-row loop, reserving two cells beyond the body.
    # With the real 61-cell snake this is x=12..41, y=1..5 (66 cells).
    for height in (5, 7, 3):
        width = max(min(8, columns), (length + 3) // 2 - height + 2)
        width += width % 2
        if not 2 <= width <= columns:
            continue
        left, top = (columns - width) // 2, (7 - height) // 2
        right, bottom = left + width - 1, top + height - 1
        cycle = ([(x, top) for x in range(left, right + 1)]
                 + [(right, y) for y in range(top + 1, bottom + 1)]
                 + [(x, bottom) for x in range(right - 1, left - 1, -1)]
                 + [(left, y) for y in range(bottom - 1, top, -1)])
        if len(cycle) >= length + 2:
            yield tuple(cycle)


def _canonical(body, cycle, lookup):
    phase = lookup[body[0]]
    return all(cell == cycle[(phase - i) % len(cycle)] for i, cell in enumerate(body))


def _join_loop(body, cycle, columns):
    """Find a short approach via an outside predecessor; prove two full laps.

    BFS avoids the entire current body. We then simulate the moving tail on
    each candidate approach and loop, accepting only a collision-free handoff.
    """
    loop_cells = set(cycle)
    blocked = set(body[1:])
    parents = {body[0]: None}
    queue = deque([body[0]])
    while queue:
        source = queue.popleft()
        for cell in _neighbors(source, columns):
            if cell not in parents and cell not in blocked:
                parents[cell] = source
                queue.append(cell)
    paths = {}
    def path_to(cell):
        if cell not in paths:
            route, at = [], cell
            while parents[at] is not None:
                route.append(at)
                at = parents[at]
            paths[cell] = tuple(reversed(route))
        return paths[cell]
    candidates = []
    for index, entry in enumerate(cycle):
        for outside in _neighbors(entry, columns):
            if outside not in loop_cells and outside in parents:
                path = path_to(outside) + (entry,)
                candidates.append((len(path), index, path))
        if entry == body[0]:
            candidates.append((0, index, ()))
    for _, index, path in sorted(candidates):
        for direction in (1, -1):
            oriented = cycle if direction == 1 else tuple(reversed(cycle))
            lookup = {cell: i for i, cell in enumerate(oriented)}
            current, approach, valid = body, [], True
            for cell in path:
                current = _advance(current, cell)
                if current is None:
                    valid = False
                    break
                approach.append(current)
            if not valid:
                continue
            # Explicit validation includes the entry, all body cells, turns,
            # and the wraparound, not just reachability of the head.
            handoff = list(approach) if _canonical(current, oriented, lookup) else None
            for tick in range(2 * len(oriented)):
                cell = oriented[(lookup[current[0]] + 1) % len(oriented)]
                current = _advance(current, cell)
                if current is None:
                    valid = False
                    break
                if handoff is None:
                    approach.append(current)
                    if _canonical(current, oriented, lookup):
                        handoff = list(approach)
            if valid and handoff is not None:
                return oriented, handoff
    return None


def build_game(calendar, columns=None):
    """Pure deterministic plan, with integer grid coordinates throughout.

    frames[step]: head-first tuple of body cells, starting with ((0,3),).
    events: (step, column, row, contribution_count), one per positive day.
    idle_frames: one whole repeating period PLUS its duplicate closure frame.
    frames[handoff_step] == idle_frames[0] == idle_frames[-1].
    idle_cycle: ordered head cells, rotated to the handoff phase.
    collection_step, handoff_step, step_seconds: shared rendering clock.

    At least four columns provide a cycle whose first move is right from
    (0,3); one/two-week inputs get explicit empty padding, never fake food.
    A food under the initial head is left intact until the head returns. A
    fully populated even-width fixture needs an extra pair of padding columns
    to hold 1 + active_days cells; growth is never capped or discarded.
    """
    days = validate_calendar(calendar)
    food = {(col, row): count for _, col, row, count, _ in days if count > 0}
    if columns is not None and (type(columns) is not int or columns < len(calendar['weeks'])
                                or columns < 2 or columns % 2):
        raise ValueError('Game columns must be even, >= 2, and fit every calendar week')
    columns = max(4, columns or (len(calendar['weeks']) + 1) // 2 * 2)
    if len(food) + 1 > columns * 7:
        columns += 2
    cycle = _hamiltonian(columns)
    lookup, size = {cell: i for i, cell in enumerate(cycle)}, len(cycle)
    frames, events = [((0, 3),)], []
    def tick(cell):
        count = food.get(cell, 0)
        body = _advance(frames[-1], cell, bool(count))
        assert body is not None, 'Planner produced an illegal move'
        assert 0 <= cell[0] < columns and 0 <= cell[1] < 7
        frames.append(body)
        if count:
            events.append((len(frames) - 1, *cell, food.pop(cell)))
    # Always visibly begin by heading right, even with an empty calendar.
    tick((1, 3))
    while food:
        body = frames[-1]
        head = lookup[body[0]]
        # Body remains ordered around the cycle. Never pass the tail or the
        # next food in that order. The ordinary cycle successor always works.
        free_arc = (lookup[body[-1]] - head) % size if len(body) > 1 else size
        food_arc = min((lookup[cell] - head) % size or size for cell in food)
        choices = []
        for cell in _neighbors(body[0], columns):
            advance = (lookup[cell] - head) % size
            growing = cell in food
            # Leave enough cycle arc for ALL outstanding growth. In
            # particular a positive spawn square cannot trap its own tail.
            if (0 < advance <= min(food_arc, free_arc - len(food)) and
                    (advance < free_arc or (advance == free_arc and not growing)) and
                    _advance(body, cell, growing) is not None):
                choices.append((advance, cell))
        assert choices, 'Hamiltonian safety invariant was lost'
        tick(max(choices)[1])
        assert len(frames) <= (len(days) + 2) * size + 2, 'Collection failed to progress'
    collection_step = events[-1][0] if events else 0
    idle_kind, chosen = 'hamiltonian', None
    rectangles = tuple(_rectangle(columns, len(frames[-1])))
    # Small bounded retries preserve the cycle-order invariant. Usually the
    # first BFS succeeds; nearly full boards fall back to their large cycle.
    for retry in range(25 if rectangles else 1):
        for rectangle in rectangles:
            chosen = _join_loop(frames[-1], rectangle, columns)
            if chosen:
                idle_kind = 'rectangle'
                break
        if chosen:
            break
        if rectangles and retry < 24:
            tick(cycle[(lookup[frames[-1][0]] + 1) % size])
    if chosen:
        idle_cycle, joining = chosen
        frames.extend(joining)
    else:
        idle_cycle = cycle
        # Flush shortcuts into contiguous cycle cells, retaining the full body.
        while not _canonical(frames[-1], cycle, lookup):
            tick(cycle[(lookup[frames[-1][0]] + 1) % size])
    phase = idle_cycle.index(frames[-1][0])
    idle_cycle = idle_cycle[phase:] + idle_cycle[:phase]
    length, period = len(frames[-1]), len(idle_cycle)
    idle_frames = tuple(tuple(idle_cycle[(step - segment) % period]
                              for segment in range(length))
                        for step in range(period + 1))
    assert idle_frames[0] == frames[-1] == idle_frames[-1]
    assert length == 1 + len(events)
    return dict(frames=tuple(frames), events=tuple(events), idle_frames=idle_frames,
                idle_cycle=idle_cycle, handoff_step=len(frames) - 1,
                collection_step=collection_step, step_seconds=STEP_SECONDS,
                columns=columns, rows=7, idle_kind=idle_kind,
                final_length=length, total_consumed=sum(event[3] for event in events))


def _clock(step):
    return f'{step * STEP_SECONDS:.9f}'.rstrip('0').rstrip('.') or '0'


def _keys(count):
    """Explicit normalized times; final value is a duplicate closure frame."""
    return ';'.join('0' if i == 0 else '1' if i == count - 1 else
                    f'{i / (count - 1):.10f}'.rstrip('0') for i in range(count))


def _angle(a, b):
    return {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}[(b[0] - a[0], b[1] - a[1])]


def _snake_svg(game, eye):
    frames, idle = game['frames'], game['idle_frames']
    end, period = game['handoff_step'], len(idle) - 1
    start_time, duration = _clock(end), _clock(period)
    # Collection occupies steps [0,end); idle takes over at EXACTLY step end.
    # The final finite value is duplicated to make every discrete tick uniform.
    finite = frames + (frames[-1],)
    out = ['<g class="snake-layer" clip-path="url(#grid-clip)">']
    for gathering, timeline in ((True, finite), (False, idle)):
        out.append('<g class="game-layer gathering-layer">' if gathering else
                   '<g class="idle-layer orbit-layer" display="none">')
        out.append(f'<set attributeName="display" to="{"none" if gathering else "inline"}" begin="{start_time}s" fill="freeze"/>')
        begin = '0' if gathering else start_time
        dur = _clock(end + 1) if gathering else duration
        keys = _keys(len(timeline))
        repeat = 'fill="freeze"' if gathering else 'repeatCount="indefinite"'
        headings = []
        for i, body in enumerate(timeline):
            if gathering:
                next_head = frames[min(i + 1, end)][0] if i < end else idle[1][0]
            else:
                next_head = idle[(i + 1) % period][0]
            headings.append(str(_angle(body[0], next_head)))
        # The finite duplicate must also duplicate the final head direction.
        if gathering:
            headings[-1] = headings[-2]
        births = [0] + [event[0] for event in game['events']]
        for segment in range(game['final_length'] - 1, -1, -1):
            positions = []
            for body in timeline:
                # Hidden unborn segments are parked at the initial head, never
                # exposed as prehistory. At birth the old tail is retained.
                x, y = body[segment] if segment < len(body) else (0, 3)
                positions.append(f'{ORIGIN[0] + x * PITCH} {ORIGIN[1] + y * PITCH}')
            visibility = ' visibility="hidden"' if gathering and segment else ''
            out.append(f'<g class="snake s{segment}" data-segment="{segment}" transform="translate({positions[0]})"{visibility}>')
            out.append(f'<animateTransform attributeName="transform" type="translate" values="{";".join(positions)}" keyTimes="{keys}" calcMode="discrete" begin="{begin}s" dur="{dur}s" {repeat}/>')
            if gathering and segment:
                out.append(f'<set attributeName="visibility" to="visible" begin="{_clock(births[segment])}s" fill="freeze"/>')
            if segment == 0:
                out.append(f'<g class="head" transform="rotate({headings[0]})"><animateTransform attributeName="transform" type="rotate" values="{";".join(headings)}" keyTimes="{keys}" calcMode="discrete" begin="{begin}s" dur="{dur}s" {repeat}/><circle r="6.2"/><circle cx="3.4" cy="-2.5" r="1.3" fill="{eye}"/><circle cx="3.4" cy="2.5" r="1.3" fill="{eye}"/></g>')
            else:
                out.append('<rect x="-6.2" y="-6.2" width="12.4" height="12.4" rx="4" opacity=".92"/>')
            out.append('</g>')
        out.append('</g>')
    out.append('</g>')
    return ''.join(out)


def render_svg(calendar, username, dark=False):
    days = validate_calendar(calendar)
    game = build_game(calendar)
    columns = game['columns']
    width, height = max(720, columns * PITCH + 124), 448
    origin_x, origin_y = ORIGIN
    duration = game['handoff_step'] * STEP_SECONDS
    collection_duration = game['collection_step'] * STEP_SECONDS
    idle_period = len(game['idle_cycle']) * STEP_SECONDS
    bounds = (origin_x - 6.5, origin_y - 6.5,
              origin_x + (columns - 1) * PITCH + 6.5, origin_y + 6 * PITCH + 6.5)
    active = sum(day[3] > 0 for day in days)
    streak = longest = 0
    for day in days:
        streak = streak + 1 if day[3] else 0
        longest = max(longest, streak)
    if dark:
        page, surface, border, text, muted = "#0d1117", "#161b22", "#30363d", "#f0f6fc", "#9da7b3"
        palette = ("#252d38", "#0e4429", "#006d32", "#26a641", "#39d353")
        snake, eye, eaten = "#b5f46c", "#102015", "#1c2330"
    else:
        page, surface, border, text, muted = "#f8f2e4", "#ffffff", "#dedfd7", "#171c17", "#3b4537"
        palette = ("#e5eaf0", "#b5e8c3", "#69cb89", "#279c56", "#126e3c")
        snake, eye, eaten = "#176c46", "#ffffff", "#edf1f5"
    label = escape(username)
    title = f"{label} · Cobra de contribuições"
    description = (f"{calendar['totalContributions']} contribuições; {active} dias ativos; "
                   f"maior sequência de {longest} dias. De {days[0][0]} a {days[-1][0]}. "
                   "A cobra busca cada dia ativo uma vez e cresce um segmento por dia e segue um circuito ortogonal sem reiniciar. "
                   "Movimento reduzido mostra o calendário completo.")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc" data-step-seconds="{STEP_SECONDS:g}" data-handoff-step="{game["handoff_step"]}" data-collection-step="{game["collection_step"]}" data-columns="{columns}" data-final-length="{game["final_length"]}" data-idle-kind="{game["idle_kind"]}" data-collection-duration="{collection_duration:.9f}" data-handoff-time="{duration:.9f}" data-idle-period="{idle_period:g}" data-orbit-period="{idle_period:g}">',
           f'<title id="title">{title}</title><desc id="desc">{escape(description)}</desc>',
           f'<defs><clipPath id="board-clip"><rect x="20" y="128" width="{width - 40}" height="230" rx="16"/></clipPath>'
           f'<clipPath id="grid-clip"><rect x="{bounds[0]}" y="{bounds[1]}" width="{bounds[2] - bounds[0]}" height="121"/></clipPath></defs>']
    css = [f'text{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;fill:{text}}}',
           f'.muted{{fill:{muted}}}.snake{{fill:{snake}}}',
           '@media(prefers-reduced-motion:reduce){.snake-layer,.consumption,.progress{display:none!important}}']
    out.append('<style>' + ''.join(css) + '</style>')
    out.extend((f'<rect width="{width}" height="{height}" rx="20" fill="{page}"/>',
                f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="20" fill="none" stroke="{border}"/>',
                f'<circle cx="27" cy="29" r="5" fill="{snake}"/>',
                f'<text x="42" y="35" font-size="18" font-weight="650">{label}</text>',
                f'<text x="{width - 24}" y="33" text-anchor="end" class="muted" font-size="11">COBRA DE CONTRIBUIÇÕES</text>'))
    stats = ((f"{calendar['totalContributions']:,}".replace(',', '.'), "CONTRIBUIÇÕES"), (str(active), "DIAS ATIVOS"), (str(longest), "MAIOR SEQUÊNCIA · DIAS"))
    for index, (value, name) in enumerate(stats):
        x = 24 + index * ((width - 48) / 3)
        out.append(f'<text x="{x:g}" y="80" font-size="26" font-weight="700">{value}</text><text x="{x:g}" y="101" class="muted" font-size="10" letter-spacing="1">{name}</text>')
    out.append(f'<rect x="20" y="128" width="{width - 40}" height="230" rx="16" fill="{surface}" stroke="{border}"/>')
    month_seen = set()
    last_month_x = -100
    for date, col, row, contributions, level in days:
        month = (date.year, date.month)
        if month not in month_seen:
            month_seen.add(month)
            x = origin_x + col * PITCH - 6
            if x - last_month_x >= 34:
                out.append(f'<text x="{x}" y="152" class="muted" font-size="11">{MONTHS[date.month - 1]}</text>')
                last_month_x = x
    for row, name in enumerate(("Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")):
        out.append(f'<text x="48" y="{origin_y + row * PITCH + 3}" text-anchor="end" class="muted" font-size="9">{name}</text>')
    out.append('<g clip-path="url(#board-clip)">')
    real_cells = {(day[1], day[2]) for day in days}
    for col in range(columns):
        for row in range(7):
            if (col, row) not in real_cells:
                x, y = origin_x + col * PITCH - 6.5, origin_y + row * PITCH - 6.5
                out.append(f'<rect class="padding-cell" x="{x}" y="{y}" width="13" height="13" rx="3" fill="{palette[0]}" opacity=".55"><title>Sem data · preenchimento da grade</title></rect>')
    for date, col, row, contributions, level in days:
        x, y = origin_x + col * PITCH - 6.5, origin_y + row * PITCH - 6.5
        tip = quoteattr(f"{date.isoformat()}: {contributions} contribuições")
        out.append(f'<rect class="cell c{col}_{row}" x="{x}" y="{y}" width="13" height="13" rx="3" fill="{palette[level]}" aria-label={tip}><title>{escape(f"{date}: {contributions} contribuições")}</title></rect>')
    # Original calendar cells never animate; removable overlays consume them.
    out.append('<g class="consumption" clip-path="url(#grid-clip)">')
    for step, col, row, count in game['events']:
        x, y = origin_x + col * PITCH - 6.5, origin_y + row * PITCH - 6.5
        out.append(f'<rect class="consumed c{col}_{row}" data-step="{step}" data-count="{count}" x="{x}" y="{y}" width="13" height="13" rx="3" fill="{eaten}" visibility="hidden"><set attributeName="visibility" to="visible" begin="{_clock(step)}s" fill="freeze"/></rect>')
    out.append('</g>')
    out.append(_snake_svg(game, eye))
    out.append('</g>')
    out.append(f'<text x="24" y="390" class="muted" font-size="11">{days[0][0].isoformat()} — {days[-1][0].isoformat()}</text>')
    legend_x = width - 174
    out.append(f'<text x="{legend_x - 10}" y="390" text-anchor="end" class="muted" font-size="10">Menos</text>')
    for index, color in enumerate(palette):
        out.append(f'<rect x="{legend_x + index * 18}" y="380" width="12" height="12" rx="3" fill="{color}"/>')
    out.append(f'<text x="{legend_x + 94}" y="390" class="muted" font-size="10">Mais</text>')
    out.append(f'<rect x="24" y="412" width="{width - 48}" height="4" rx="2" fill="{palette[0]}"/>')
    out.append(f'<g transform="translate(24 412)"><rect class="progress" width="0" height="4" rx="2" fill="{snake}">')
    total = sum(day[3] for day in days)
    consumed = 0
    for step, col, row, count in game['events']:
        consumed += count
        out.append(f'<set attributeName="width" to="{(width - 48) * consumed / total:.6f}" begin="{_clock(step)}s" fill="freeze" data-progress="{consumed / total:.9f}"/>')
    out.append('</rect></g>')
    out.append(f'<text x="24" y="436" class="muted" font-size="9">CONTRIBUIÇÕES DO GITHUB · COLETA ÚNICA</text><text x="{width - 24}" y="436" text-anchor="end" class="muted" font-size="9">{collection_duration:.0f}s / coleta · {idle_period:g}s / circuito</text></svg>')
    return '\n'.join(out) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-json", type=Path, help="Direct calendar object; synthetic inputs are LOCAL TEST ONLY")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    username = os.environ.get("GH_USERNAME", "ChrisDevDesign")
    try:
        calendar = json.loads(args.calendar_json.read_text(encoding="utf-8-sig")) if args.calendar_json else fetch_calendar(username)
        validate_calendar(calendar)
        light, dark = render_svg(calendar, username), render_svg(calendar, username, dark=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "calendar.json").write_text(json.dumps(calendar, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")
        for filename, content in (("github-contribution-grid-snake.svg", light), ("github-contribution-grid-snake-dark.svg", dark)):
            (args.output_dir / filename).write_text(content, encoding="utf-8")
        print(f"Wrote calendar.json and both SVGs to {args.output_dir.resolve()}")
        if args.calendar_json:
            print("Offline input used: verify real-data provenance before publishing; never publish fixture output.")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Do not include request headers, environment values, or token-bearing URLs.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
