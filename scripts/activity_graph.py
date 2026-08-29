#!/usr/bin/env python3
"""Daily contribution activity chart as a static SVG.

Single green line (ups and downs) at DAILY resolution over the
last ~4 months, with one visible dot per day. Legend below the
chart: stack icon + name + share of commits (per language).

Data (GitHub APIs only, no third-party services):
  - contributionsCollection(from, to) -> contributionCalendar daily counts
  - commitContributionsByRepository   -> commits per repo x language byte shares

Env: GH_LOGIN, OUT (activity-graph.svg), WINDOW_DAYS (122), MIN_PCT (1.0)
Icons: assets/icons/<slug>.svg (simple-icons), inlined into the SVG.
"""

import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
OUT = os.environ.get("OUT", "activity-graph.svg")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "122"))
MIN_PCT = float(os.environ.get("MIN_PCT", "1.0"))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
ICON_DIR = os.path.join(REPO_ROOT, "assets", "icons")

BG, FG, MUTED, TRACK = "#0d1117", "#c9d1d9", "#8b949e", "#21262d"
GREEN = "#40c463"
FONT = "Segoe UI, Ubuntu, Sans-serif"

ICON_SLUGS = {
    "Python": "python", "TypeScript": "typescript", "JavaScript": "javascript",
    "Rust": "rust", "Go": "go", "Svelte": "svelte", "HTML": "html5",
    "CSS": "css3", "SCSS": "css3", "Zig": "zig", "Shell": "gnubash",
    "Dockerfile": "docker", "Nix": "nixos", "Lua": "lua", "Vue": "vuedotjs",
    "C": "c", "C++": "cplusplus", "Java": "openjdk", "Swift": "swift",
    "Kotlin": "kotlin", "Ruby": "ruby", "PHP": "php", "Dart": "dart",
    "HCL": "terraform", "Markdown": "markdown", "Jupyter Notebook": "jupyter",
    "Astro": "astro", "C#": "csharp", "Slint": "slint",
}
FALLBACK_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051", "HTML": "#e34c26",
    "CSS": "#663399", "Svelte": "#ff3e00", "Slint": "#ff8b00", "Other": MUTED,
}


def gql(query: str, **vars) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in vars.items():
        args += ["-f", f"{k}={v}"]
    for attempt in range(3):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if data.get("errors"):
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]
        if attempt == 2:
            raise RuntimeError(f"gh api failed: {proc.stderr[:400]}")
    raise RuntimeError("unreachable")


COLL_Q = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from, to:$to){
      contributionCalendar{
        weeks{ contributionDays{ date contributionCount } }
      }
      commitContributionsByRepository(maxRepositories: 100){
        contributions{ totalCount }
        repository{
          nameWithOwner
          languages(first:100, orderBy:{field:SIZE, direction:DESC}){
            edges{ size node{ name color } }
          }
        }
      }
    }
  }
}"""


def fetch_days(start: datetime, end: datetime) -> tuple[list[tuple[str, int]], list]:
    data = gql(COLL_Q, login=LOGIN, **{"from": iso(start), "to": iso(end)})
    coll = data["user"]["contributionsCollection"]
    days = []
    for w in coll["contributionCalendar"]["weeks"]:
        for d in w["contributionDays"]:
            if start.strftime("%Y-%m-%d") <= d["date"] <= end.strftime("%Y-%m-%d"):
                days.append((d["date"], d["contributionCount"]))
    days.sort()
    return days, coll["commitContributionsByRepository"]


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT00:00:00Z")


def stack_shares(entries) -> tuple[dict[str, float], dict[str, str]]:
    """Language -> share of all commits in window; Language -> color."""
    lang_commits: dict[str, float] = defaultdict(float)
    colors: dict[str, str] = {}
    for e in entries:
        count = e["contributions"]["totalCount"]
        if count <= 0:
            continue
        edges = e["repository"]["languages"]["edges"]
        total_bytes = sum(edge["size"] for edge in edges)
        if total_bytes <= 0:
            continue
        for edge in edges:
            name = edge["node"]["name"]
            if not name:
                continue
            lang_commits[name] += count * edge["size"] / total_bytes
            if edge["node"].get("color"):
                colors[name] = edge["node"]["color"]
    return dict(lang_commits), colors


USER_ID = ""


def main() -> None:
    global USER_ID
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = (now - timedelta(days=WINDOW_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    days, entries = fetch_days(start, now)
    if not days:
        sys.exit("no contribution data in window")

    cal_total = sum(c for _, c in days)
    shares, colors = stack_shares(entries)
    commit_total = sum(shares.values())
    print(f"window: {days[0][0]} .. {days[-1][0]}  "
          f"({len(days)} days, {cal_total} contributions, "
          f"{commit_total:.0f} commits)")

    layers = sorted(shares.items(), key=lambda kv: -kv[1])
    main_layers, other = [], 0.0
    for name, val in layers:
        if val / commit_total >= MIN_PCT / 100.0 or name == layers[0][0]:
            main_layers.append([name, val])
        else:
            other += val
    if other > 0:
        main_layers.append(["Other", other])

    render(days, main_layers, colors, cal_total, start)
    legend = ", ".join(f"{n} {100 * v / commit_total:.1f}%"
                       for n, v in main_layers)
    print(f"wrote {OUT}: {legend}")


def color_of(name: str, colors: dict) -> str:
    return colors.get(name) or FALLBACK_COLORS.get(name, MUTED)


def icon_embed(name: str, x: float, y: float, size: float, color: str) -> str:
    dot = (f'<circle cx="{x + size / 2}" cy="{y + size / 2}" r="{size / 2}" '
           f'fill="{color}"/>')
    slug = ICON_SLUGS.get(name)
    if not slug:
        return dot
    path_file = os.path.join(ICON_DIR, f"{slug}.svg")
    if not os.path.exists(path_file):
        return dot
    content = open(path_file, encoding="utf-8").read()
    m = re.search(r'<path[^>]*\bd="([^"]+)"', content)
    if not m:
        return dot
    s = size / 24.0
    return (f'<path d="{m.group(1)}" fill="{color}" '
            f'transform="translate({x:.1f},{y:.1f}) scale({s:.4f})"/>')


def cr_path(pts) -> str:
    d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += (f" C {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f}"
              f" {p2[0]:.1f},{p2[1]:.1f}")
    return d


def render(days, layers, colors, cal_total, start) -> None:
    n = len(days)
    W = 900
    legend_cols, row_h = 2, 30
    legend_rows = math.ceil(len(layers) / legend_cols)
    legend_h = 56 + legend_rows * row_h
    H = 330 + legend_h
    LEFT, RIGHT, TOP, BOTTOM = 62, 30, 56, 40
    PW, PH = W - LEFT - RIGHT, 330 - TOP - BOTTOM
    base_y = TOP + PH

    values = [c for _, c in days]
    v_max = max(values) or 1
    step = 1
    for mult in (1, 2, 5):
        for k in range(0, 7):
            s = mult * 10 ** k
            if 3 <= v_max / s <= 6:
                step = s
                break
        if step > 1:
            break
    y_top = max(step, math.ceil(v_max / step) * step)
    n_ticks = max(1, round(y_top / step))

    def px(i):
        return LEFT + PW * i / (n - 1)

    def py(v):
        return base_y - PH * (min(v, y_top) / y_top)

    grid, ylabels = [], []
    for t in range(n_ticks + 1):
        y = base_y - PH * t / n_ticks
        grid.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" '
                    f'stroke="{TRACK}" stroke-width="1"/>')
        ylabels.append(f'<text x="{LEFT - 10}" y="{y + 4:.1f}" text-anchor="end" '
                       f'fill="{MUTED}" font-size="12">'
                       f'{round(y_top * t / n_ticks)}</text>')

    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in days]
    xlabels = []
    ticks = list(range(0, n, 14))
    if n - 1 - ticks[-1] >= 8:
        ticks.append(n - 1)
    for i in ticks:
        xlabels.append(f'<text x="{px(i):.1f}" y="{base_y + 22}" text-anchor="middle" '
                       f'fill="{MUTED}" font-size="12">{dates[i]:%b %-d}</text>')

    pts = [(px(i), py(v)) for i, v in enumerate(values)]
    line_d = cr_path(pts)
    area_d = (line_d + f" L {pts[-1][0]:.1f},{base_y:.1f}"
              f" L {pts[0][0]:.1f},{base_y:.1f} Z")

    dots = []
    for i, ((date_s, count), (x, y)) in enumerate(zip(days, pts)):
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{GREEN}">'
                    f'<title>{datetime.strptime(date_s, "%Y-%m-%d"):%b %-d}: '
                    f'{count} contributions</title></circle>')

    legend = []
    for idx, (name, val) in enumerate(layers):
        col, row = idx % legend_cols, idx // legend_cols
        x = 40 + col * 430
        y = 330 + 56 + row * row_h
        pct = 100 * val / sum(v for _, v in layers)
        c = color_of(name, colors)
        legend.append(icon_embed(name, x, y - 15, 18, c))
        legend.append(f'<text x="{x + 26}" y="{y}" fill="{FG}" font-size="13" '
                      f'font-family="{FONT}">{name}</text>')
        legend.append(f'<text x="{x + 380}" y="{y}" text-anchor="end" fill="{FG}" '
                      f'font-size="13" font-weight="600" font-family="{FONT}">'
                      f'{pct:.1f}%</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Daily contribution activity for {LOGIN}">
  <title>{cal_total} contributions in the last 4 months</title>
  <rect width="{W}" height="{H}" fill="{BG}" rx="6"/>
  {"".join(grid)}
  {"".join(ylabels)}
  {"".join(xlabels)}
  <text x="{LEFT}" y="28" fill="{FG}" font-size="16" font-weight="600" font-family="{FONT}">{cal_total} contributions in the last 4 months</text>
  <text x="{W - RIGHT}" y="28" text-anchor="end" fill="{MUTED}" font-size="12" font-family="{FONT}">daily · since {start:%b %-d}</text>
  <path d="{area_d}" fill="{GREEN}" fill-opacity="0.15"/>
  <path d="{line_d}" fill="none" stroke="{GREEN}" stroke-width="2"/>
  {"".join(dots)}
  <text x="40" y="{330 + 36}" fill="{MUTED}" font-size="11" font-family="{FONT}">share of commits by stack</text>
  {"".join(legend)}
</svg>
'''
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)


if __name__ == "__main__":
    main()
