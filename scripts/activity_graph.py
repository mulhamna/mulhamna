#!/usr/bin/env python3
"""Render a GitHub contributions area chart (line + area) as a static SVG.

Style-matched to the classic github-readme-activity-graph dark theme:
bg #0d1117, line/points #40c463, title #c9d1d9, muted axis text.

Data source: GitHub GraphQL contributionCalendar (last 26 weeks).
Auth: `gh api graphql` (GH_TOKEN on Actions, gh auth locally).

Env:
  GH_LOGIN  username whose contributions to chart (default: mulhamna)
  OUT       output SVG path (default: activity-graph.svg)
"""

import json
import math
import os
import subprocess
import sys
from datetime import date, datetime

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
OUT = os.environ.get("OUT", "activity-graph.svg")
WEEKS = 26  # half a year

QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ contributionCount date } }
      }
    }
  }
}"""


def fetch_days(login: str) -> list[tuple[date, int]]:
    raw = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"login={login}"],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    calendar = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        (datetime.strptime(d["date"], "%Y-%m-%d").date(), int(d["contributionCount"]))
        for w in calendar["weeks"]
        for d in w["contributionDays"]
    ]
    return days[-(WEEKS * 7):]


def aggregate_weekly(days: list[tuple[date, int]]) -> list[tuple[date, int]]:
    """Sum contributions per calendar week (7-day chunks, anchored at first day)."""
    return [
        (days[i][0], sum(c for _, c in days[i:i + 7]))
        for i in range(0, len(days), 7)
    ]


def tick_step(v: int) -> int:
    """Pick a round tick step (1/2/5 x 10^k) giving roughly 4-6 gridlines."""
    if v <= 0:
        return 10
    for mult in (1, 2, 5):
        for k in range(1, 7):
            step = mult * 10 ** k
            if 3 <= v / step <= 6:
                return step
    return 10 ** max(1, len(str(v)) - 1)


def catmull_rom_path(pts: list[tuple[float, float]]) -> str:
    """Smooth cubic-bezier path through pts (Catmull-Rom -> Bezier)."""
    if len(pts) < 2:
        return ""
    d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else p2
        c1x, c1y = p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0
        c2x, c2y = p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0
        d += (f" C {c1x:.1f},{max(p1[1], c1y):.1f}"
              f" {c2x:.1f},{max(p2[1], c2y):.1f}"
              f" {p2[0]:.1f},{p2[1]:.1f}")
    return d


def render(points: list[tuple[date, int]], out: str) -> None:
    W, H = 850, 320
    LEFT, RIGHT, TOP, BOTTOM = 62, 30, 58, 44
    PLOT_W, PLOT_H = W - LEFT - RIGHT, H - TOP - BOTTOM

    values = [c for _, c in points]
    step = tick_step(max(values))
    y_max = int(math.ceil(max(values) / step) * step)
    n = len(points)

    def px(i: int) -> float:
        return LEFT + (PLOT_W * i / (n - 1)) if n > 1 else LEFT + PLOT_W / 2

    def py(c: int) -> float:
        return TOP + PLOT_H * (1 - (min(c, y_max) / y_max))

    pts = [(px(i), py(c)) for i, (d, c) in enumerate(points)]
    line_d = catmull_rom_path(pts)
    base_y = TOP + PLOT_H
    area_d = line_d + f" L {pts[-1][0]:.1f},{base_y:.1f} L {pts[0][0]:.1f},{base_y:.1f} Z"

    total = sum(values)
    half_year_ago = points[0][0].strftime("%b %Y")

    # gridlines + y labels at round steps
    grid, ylabels = [], []
    n_ticks = y_max // step
    for t in range(n_ticks + 1):
        y = TOP + PLOT_H * t / n_ticks
        val = y_max - t * step
        grid.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" '
            f'stroke="#21262d" stroke-width="1"/>'
        )
        ylabels.append(
            f'<text x="{LEFT - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'fill="#8b949e" font-size="12">{val}</text>'
        )

    # month labels on x axis (first week of each month change)
    xlabels, last_month = [], None
    for i, (d, _) in enumerate(points):
        if d.month != last_month:
            last_month = d.month
            xlabels.append(
                f'<text x="{px(i):.1f}" y="{H - 20}" text-anchor="middle" '
                f'fill="#8b949e" font-size="12">{d.strftime("%b")}</text>'
            )

    dots = "\n".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#40c463" stroke="#0d1117" stroke-width="1.5"/>'
        for x, y in pts
    )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub contributions activity graph for {LOGIN}">
  <title>{total} contributions in the last half year</title>
  <defs>
    <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#40c463" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#40c463" stop-opacity="0.03"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="#0d1117" rx="6"/>
  {"".join(grid)}
  {"".join(ylabels)}
  {"".join(xlabels)}
  <text x="{LEFT}" y="30" fill="#c9d1d9" font-size="16" font-weight="600" font-family="Segoe UI, Ubuntu, Sans-serif">{total} contributions in the last half year</text>
  <text x="{W - RIGHT}" y="30" text-anchor="end" fill="#8b949e" font-size="12" font-family="Segoe UI, Ubuntu, Sans-serif">since {half_year_ago}</text>
  <path d="{area_d}" fill="url(#areaFill)"/>
  <path id="activity-line" d="{line_d}" fill="none" stroke="#40c463" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <animate attributeName="stroke-dasharray" from="0,4000" to="4000,0" dur="1.4s" fill="freeze"/>
  </path>
  {dots}
</svg>
'''
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out}: {total} contributions over {n} weeks "
          f"({points[0][0]} .. {points[-1][0]}), peak/week {max(values)}")


def main() -> None:
    days = fetch_days(LOGIN)
    if not days:
        sys.exit("no contribution data returned")
    render(aggregate_weekly(days), OUT)


if __name__ == "__main__":
    main()
