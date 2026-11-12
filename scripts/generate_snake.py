"""Generate contained, animated GitHub contribution SVGs using only the stdlib.

Live: GITHUB_TOKEN=... GH_USERNAME=ChrisDevDesign python generate_snake.py
Offline: python generate_snake.py --calendar-json calendar.json --output-dir dist
Offline input must be a direct GitHub contributionsCalendar object. Synthetic
fixtures are for local tests only; never use their output for publication.
"""

import argparse
from bisect import bisect_right
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr


LEVELS = ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
MONTHS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
PITCH = 18
SEGMENTS = 14
SEGMENT_SPACING = 0.35
SPEED = 110.0
ORBIT_PERIOD = 5.0
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


def build_motion(calendar, columns=None):
    """Pure, pixel-space route and timing; positive real cells are the only food.

    Visits are (column, row, count, arrival_seconds). Positions can be sampled
    independently with motion_position(). No calendar values are synthesized.
    """
    days = validate_calendar(calendar)
    columns = columns or (len(calendar["weeks"]) + 1) // 2 * 2
    if columns < len(calendar["weeks"]) or columns < 2:
        raise ValueError("Motion requires at least two columns and room for all weeks")
    x, y = ORIGIN
    bounds = (x - 6.5, y - 6.5, x + (columns - 1) * PITCH + 6.5, y + 6 * PITCH + 6.5)
    center = (x + (columns - 1) * PITCH / 2, y + 3 * PITCH)
    radius = min(24.0, (bounds[2] - bounds[0]) / 2 - 6.5,
                 (bounds[3] - bounds[1]) / 2 - 6.5)
    points, distances, visits = [center], [0.0], []
    remaining = [(day[1], day[2], day[3]) for day in days if day[3] > 0]
    # At most 378 cells: a deterministic nearest-neighbour scan needs no solver.
    while remaining:
        px, py = points[-1]
        food = min(remaining, key=lambda cell: ((x + cell[0] * PITCH - px) ** 2
                                               + (y + cell[1] * PITCH - py) ** 2,
                                               cell[0], cell[1]))
        remaining.remove(food)
        point = (x + food[0] * PITCH, y + food[1] * PITCH)
        distance = math.dist(points[-1], point)
        if distance:
            points.append(point)
            distances.append(distances[-1] + distance)
        visits.append((*food, distances[-1] / SPEED))
    collection_duration = distances[-1] / SPEED
    if visits:
        join = (center[0] + radius, center[1])
        distance = math.dist(points[-1], join)
        if distance:
            points.append(join)
            distances.append(distances[-1] + distance)
    join_time = distances[-1] / SPEED
    # A whole orbit flushes the approach from every segment's history. Very
    # narrow calendars need two revolutions to accommodate the full tail.
    laps = max(1, math.ceil((SEGMENTS - 1) * SEGMENT_SPACING * PITCH / (math.tau * radius)))
    handoff = join_time + laps * ORBIT_PERIOD if visits else 0.0
    return dict(points=points, distances=distances, visits=visits, center=center,
                radius=radius, bounds=bounds, collection_duration=collection_duration,
                join_time=join_time, handoff_time=handoff, orbit_period=ORBIT_PERIOD)


def motion_position(motion, seconds, segment=0):
    """Return continuous (x, y, tangent_degrees), including the initial tail."""
    cx, cy = motion["center"]
    radius, join = motion["radius"], motion["distances"][-1]
    orbit_speed = math.tau * radius / motion["orbit_period"]
    distance = (seconds * SPEED if seconds <= motion["join_time"] else
                join + (seconds - motion["join_time"]) * orbit_speed)
    distance -= segment * SEGMENT_SPACING * PITCH
    if not motion["visits"] or distance >= join:
        angle = (distance - join) / radius
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle), math.degrees(angle) + 90
    if distance < 0:
        # The small prehistory circle ends at the centered head at t=0 and is
        # itself wholly inside the grid. No initial segment is parked offscreen.
        pre_radius = radius / 2
        angle = distance / pre_radius
        return cx - pre_radius + pre_radius * math.cos(angle), cy + pre_radius * math.sin(angle), math.degrees(angle) + 90
    index = min(bisect_right(motion["distances"], distance) - 1, len(motion["points"]) - 2)
    a, b = motion["points"][index:index + 2]
    fraction = (distance - motion["distances"][index]) / math.dist(a, b)
    return (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]),
            math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))


def segment_frames(motion, segment):
    """Only path vertices and bounded circle samples, shifted by tail distance."""
    lag = segment * SEGMENT_SPACING * PITCH
    join = motion["distances"][-1]
    orbit_speed = math.tau * motion["radius"] / motion["orbit_period"]
    end = join + (motion["handoff_time"] - motion["join_time"]) * orbit_speed
    keys = {0.0, join, end}
    keys.update(distance + lag for distance in motion["distances"])
    # Circle chords subtend at most 5.625 degrees (under .03px error at r=24).
    step = math.tau * motion["radius"] / 64
    keys.update(join + lag + index * step for index in range(math.ceil((end - join) / step) + 1))
    step = math.tau * (motion["radius"] / 2) / 64
    keys.update(lag - index * step for index in range(math.ceil(lag / step) + 1))
    for distance in sorted(key for key in keys if 0 <= key <= end):
        seconds = (distance / SPEED if distance <= join else
                   motion["join_time"] + (distance - join) / orbit_speed)
        yield seconds, motion_position(motion, seconds, segment)


def turn_frames(motion):
    """Short, shortest-angle turns; tangent eyes throughout the final orbit."""
    points, distances = motion["points"], motion["distances"]
    angles = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
              for a, b in zip(points, points[1:])] + [90.0]
    angle = angles[0]
    yield 0.0, angle
    for index in range(1, len(points)):
        seconds = distances[index] / SPEED
        window = min(.045, (distances[index] - distances[index - 1]) / SPEED / 4)
        yield seconds - window, angle
        angle += (angles[index] - angle + 180) % 360 - 180
        yield seconds, angle
    yield motion["handoff_time"], angle + 360 * (motion["handoff_time"] - motion["join_time"]) / motion["orbit_period"]


def render_svg(calendar, username, dark=False):
    days = validate_calendar(calendar)
    columns = (len(calendar["weeks"]) + 1) // 2 * 2
    motion = build_motion(calendar, columns)
    width, height = max(720, columns * PITCH + 124), 448
    origin_x, origin_y = ORIGIN
    radii = [6.2 - segment * 3 / (SEGMENTS - 1) for segment in range(SEGMENTS)]
    assert all(a + b >= SEGMENT_SPACING * PITCH for a, b in zip(radii, radii[1:]))
    duration = motion["handoff_time"]
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
                   "A cobra busca cada dia ativo uma vez e depois orbita o centro da grade. "
                   "Movimento reduzido mostra o calendário completo.")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc" data-collection-duration="{motion["collection_duration"]:.9f}" data-handoff-time="{duration:.9f}" data-orbit-period="{motion["orbit_period"]:g}">',
           f'<title id="title">{title}</title><desc id="desc">{escape(description)}</desc>',
           f'<defs><clipPath id="board-clip"><rect x="20" y="128" width="{width - 40}" height="230" rx="16"/></clipPath>'
           f'<clipPath id="grid-clip"><rect x="{motion["bounds"][0]}" y="{motion["bounds"][1]}" width="{motion["bounds"][2] - motion["bounds"][0]}" height="121"/></clipPath></defs>']
    css = [f'text{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;fill:{text}}}',
           f'.muted{{fill:{muted}}}.snake{{fill:{snake}}}',
           '.progress{transform:scaleX(0)}',
           f'@keyframes eat{{to{{fill:{eaten}}}}}',
           '@keyframes hide{to{visibility:hidden}}@keyframes show{to{visibility:visible}}',
           '@keyframes orbit{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}',
           f'.gathering-layer{{animation:hide .001s steps(1,start) {duration:.9f}s forwards}}',
           f'.orbit-layer{{visibility:hidden;transform-origin:0 0;animation:show .001s steps(1,start) {duration:.9f}s forwards,orbit {ORBIT_PERIOD:g}s linear {duration:.9f}s infinite}}']
    if duration:
        for segment in range(SEGMENTS):
            frames = [f'{100 * seconds / duration:.6f}%{{transform:translate({round(x, 2):g}px,{round(y, 2):g}px)}}'
                      for seconds, (x, y, _) in segment_frames(motion, segment)]
            css.append(f'@keyframes move{segment}{{{"".join(frames)}}}')
            css.append(f'.gathering-layer .s{segment}{{animation:move{segment} {duration:.9f}s linear forwards}}')
        frames = [f'{100 * seconds / duration:.6f}%{{transform:rotate({angle:.4f}deg)}}'
                  for seconds, angle in turn_frames(motion)]
        css.extend((f'@keyframes turn{{{"".join(frames)}}}',
                    f'.gathering-layer .head{{animation:turn {duration:.9f}s linear forwards}}'))
    real_total = sum(day[3] for day in days)
    consumed = 0
    progress_frames = ['0%{transform:scaleX(0)}']
    for col, row, value, arrival in motion["visits"]:
        consumed += value
        # Use handoff as the timeline so even a zero-distance first meal is
        # defined, and the final 100% is retained throughout the orbit.
        progress_frames.append(f'{100 * arrival / duration:.6f}%{{transform:scaleX({consumed / real_total:.9f})}}')
        css.append(f'.c{col}_{row}{{animation:eat .001s steps(1,start) {arrival:.9f}s forwards}}')
    progress_frames.append(f'100%{{transform:scaleX({1 if real_total else 0})}}')
    css.append('@keyframes progress{' + ''.join(progress_frames) + '}')
    if duration:
        css.append(f'.progress{{animation:progress {duration:.9f}s steps(1,end) forwards}}')
    css.append('@media(prefers-reduced-motion:reduce){.snake-layer{display:none}.snake-layer *,.cell,.progress{animation:none!important}.progress{transform:scaleX(0)}}')
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
    out.append('<g class="snake-layer" clip-path="url(#grid-clip)">')
    cx, cy = motion["center"]
    for gathering in (True, False):
        if gathering:
            out.append('<g class="gathering-layer">')
        else:
            out.append(f'<g transform="translate({cx:g} {cy:g})"><g class="orbit-layer">')
        for segment in range(SEGMENTS - 1, -1, -1):
            x, y, angle = motion_position(motion, 0 if gathering else duration, segment)
            if not gathering:
                x, y, angle = x - cx, y - cy, 90
            out.append(f'<g class="snake s{segment}" style="transform:translate({x:.2f}px,{y:.2f}px)">')
            if segment == 0:
                out.append(f'<g class="head" style="transform:rotate({angle:.4f}deg)"><ellipse rx="6.2" ry="5.8"/><circle cx="3.4" cy="-2.5" r="1.3" fill="{eye}"/><circle cx="3.4" cy="2.5" r="1.3" fill="{eye}"/></g>')
            else:
                out.append(f'<circle r="{radii[segment]:.2f}" opacity="{1 - segment * 0.055:.3f}"/>')
            out.append('</g>')
        out.append('</g>' if gathering else '</g></g>')
    out.append('</g></g>')
    out.append(f'<text x="24" y="390" class="muted" font-size="11">{days[0][0].isoformat()} — {days[-1][0].isoformat()}</text>')
    legend_x = width - 174
    out.append(f'<text x="{legend_x - 10}" y="390" text-anchor="end" class="muted" font-size="10">Menos</text>')
    for index, color in enumerate(palette):
        out.append(f'<rect x="{legend_x + index * 18}" y="380" width="12" height="12" rx="3" fill="{color}"/>')
    out.append(f'<text x="{legend_x + 94}" y="390" class="muted" font-size="10">Mais</text>')
    out.append(f'<rect x="24" y="412" width="{width - 48}" height="4" rx="2" fill="{palette[0]}"/>')
    out.append(f'<g transform="translate(24 412)"><rect class="progress" width="{width - 48}" height="4" rx="2" fill="{snake}"/></g>')
    out.append(f'<text x="24" y="436" class="muted" font-size="9">CONTRIBUIÇÕES DO GITHUB · COLETA ÚNICA</text><text x="{width - 24}" y="436" text-anchor="end" class="muted" font-size="9">{motion["collection_duration"]:.0f}s / coleta · {ORBIT_PERIOD:g}s / órbita</text></svg>')
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
