"""Generate contained, animated GitHub contribution SVGs using only the stdlib.

Live: GITHUB_TOKEN=... GH_USERNAME=ChrisDevDesign python generate_snake.py
Offline: python generate_snake.py --calendar-json calendar.json --output-dir dist
Offline input must be a direct GitHub contributionsCalendar object. Synthetic
fixtures are for local tests only; never use their output for publication.
"""

import argparse
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


def snake_route(columns):
    assert columns >= 2 and columns % 2 == 0, "Cycle requires an even column count"
    route = [(col, 0) for col in range(columns)]
    for col in range(columns - 1, 0, -1):
        route.extend((col, row) for row in (range(1, 7) if col % 2 else range(6, 0, -1)))
    route.extend((0, row) for row in range(6, 0, -1))
    assert len(route) == columns * 7
    assert all(0 <= col < columns and 0 <= row < 7 for col, row in route)
    assert len(set(route)) == len(route), "Route overlaps itself"
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
               for a, b in zip(route, route[1:] + route[:1])), "Non-adjacent route including closure"
    return route


def render_svg(calendar, username, dark=False):
    days = validate_calendar(calendar)
    columns = (len(calendar["weeks"]) + 1) // 2 * 2
    route = snake_route(columns)
    count = len(route)
    width, height = max(720, columns * PITCH + 124), 448
    origin_x, origin_y = 78, 194
    board = (20, 128, width - 40, 230)
    points = [(origin_x + col * PITCH, origin_y + row * PITCH) for col, row in route]
    # An enclosing circle covers each rotated head, eye, and body segment.
    # Convex board bounds also contain every interpolated point on every edge.
    head_radius = max(6.2, math.hypot(3.4, 2.5) + 1.3)
    for x, y in points:
        assert board[0] + 12 <= x - head_radius and x + head_radius <= board[0] + board[2] - 12
        assert board[1] + 12 <= y - head_radius and y + head_radius <= board[1] + board[3] - 12
        assert origin_x - 6.5 <= x - head_radius and x + head_radius <= origin_x + (columns - 1) * PITCH + 6.5
        assert origin_y - 6.5 <= y - head_radius and y + head_radius <= origin_y + 6 * PITCH + 6.5
    radii = [6.2 - segment * 3 / (SEGMENTS - 1) for segment in range(SEGMENTS)]
    assert all(a + b >= SEGMENT_SPACING * PITCH for a, b in zip(radii, radii[1:]))
    duration = max(36, count * 0.135)
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
                   "A cobra percorre somente células da grade. Movimento reduzido mostra o calendário completo.")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{title}</title><desc id="desc">{escape(description)}</desc>',
           f'<defs><clipPath id="board-clip"><rect x="20" y="128" width="{width - 40}" height="230" rx="16"/></clipPath></defs>']
    css = [f'text{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;fill:{text}}}',
           f'.muted{{fill:{muted}}}.snake{{fill:{snake}}}',
           f'.progress{{transform:scaleX(0);animation:progress {duration:.3f}s steps(1,end) infinite}}',
           f'.snake{{animation:move {duration:.3f}s linear infinite}}']
    angles = []
    for index, (x, y) in enumerate(points):
        nx, ny = points[(index + 1) % count]
        angles.append(math.degrees(math.atan2(ny - y, nx - x)))
    frames = []
    for index in range(count + 1):
        x, y = points[index % count]
        frames.append(f'{100 * index / count:.6f}%{{transform:translate({x}px,{y}px)}}')
    css.append(f'@keyframes move{{{"".join(frames)}}}')
    for segment in range(1, SEGMENTS):
        delay = -duration + segment * SEGMENT_SPACING * duration / count
        css.append(f'.s{segment}{{animation-delay:{delay:.9f}s}}')
    frames = [f'{100 * index / count:.6f}%{{transform:rotate({angles[index % count]:g}deg)}}' for index in range(count + 1)]
    css.extend((f'@keyframes turn{{{"".join(frames)}}}',
                f'.head{{animation:turn {duration:.3f}s steps(1,end) infinite}}'))
    visit = {point: index for index, point in enumerate(route)}
    contributions_by_cell = {(day[1], day[2]): day[3] for day in days}
    real_total = sum(contributions_by_cell.values())
    consumed = 0
    progress_frames = ['0%{transform:scaleX(0)}']
    for index, cell in enumerate(route):
        value = contributions_by_cell.get(cell, 0)
        if value:
            consumed += value
            progress_frames.append(f'{100 * index / count:.6f}%{{transform:scaleX({consumed / real_total:.9f})}}')
    progress_frames.append(f'99.85%{{transform:scaleX({1 if real_total else 0})}}99.9%,100%{{transform:scaleX(0)}}')
    css.append('@keyframes progress{' + ''.join(progress_frames) + '}')
    for date, col, row, contributions, level in days:
        if contributions:
            percent = 100 * visit[(col, row)] / count
            # Keep the complete map readable initially; restore before loop closure.
            css.append(f'@keyframes eat{col}_{row}{{0%{{fill:{palette[level]}}}'
                       f'{percent:.6f}%{{fill:{eaten}}}99.85%{{fill:{eaten}}}99.9%,100%{{fill:{palette[level]}}}}}')
            css.append(f'.c{col}_{row}{{animation:eat{col}_{row} {duration:.3f}s steps(1,end) infinite}}')
    css.append('@media(prefers-reduced-motion:reduce){.snake-layer{display:none}.snake,.head,.cell,.progress{animation:none!important}.progress{transform:scaleX(0)}}')
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
    out.append('<g class="snake-layer">')
    for segment in range(SEGMENTS - 1, -1, -1):
        phase = (-segment * SEGMENT_SPACING) % count
        index = math.floor(phase)
        fraction = phase - index
        a, b = points[index], points[(index + 1) % count]
        x, y = (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]))
        radius = radii[segment]
        out.append(f'<g class="snake s{segment}" style="transform:translate({x}px,{y}px)">')
        if segment == 0:
            out.append(f'<g class="head"><ellipse rx="6.2" ry="5.8"/><circle cx="3.4" cy="-2.5" r="1.3" fill="{eye}"/><circle cx="3.4" cy="2.5" r="1.3" fill="{eye}"/></g>')
        else:
            out.append(f'<circle r="{radius:.2f}" opacity="{1 - segment * 0.055:.3f}"/>')
        out.append('</g>')
    out.append('</g></g>')
    out.append(f'<text x="24" y="390" class="muted" font-size="11">{days[0][0].isoformat()} — {days[-1][0].isoformat()}</text>')
    legend_x = width - 174
    out.append(f'<text x="{legend_x - 10}" y="390" text-anchor="end" class="muted" font-size="10">Menos</text>')
    for index, color in enumerate(palette):
        out.append(f'<rect x="{legend_x + index * 18}" y="380" width="12" height="12" rx="3" fill="{color}"/>')
    out.append(f'<text x="{legend_x + 94}" y="390" class="muted" font-size="10">Mais</text>')
    out.append(f'<rect x="24" y="412" width="{width - 48}" height="4" rx="2" fill="{palette[0]}"/>')
    out.append(f'<g transform="translate(24 412)"><rect class="progress" width="{width - 48}" height="4" rx="2" fill="{snake}"/></g>')
    out.append(f'<text x="24" y="436" class="muted" font-size="9">CONTRIBUIÇÕES DO GITHUB · CICLO FECHADO</text><text x="{width - 24}" y="436" text-anchor="end" class="muted" font-size="9">{duration:.0f}s / ciclo</text></svg>')
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
