"""
generate_placeholder_data.py
-----------------------------
STAND-IN for fetch_race_data.py.

Why this file exists: fetch_race_data.py (the real script) needs to reach
FastF1's data servers, and this sandboxed environment's network policy
blocks those hosts. Rather than block the whole project on that, this
script builds a race_data.json with the SAME shape that fetch_race_data.py
would produce.

It uses the REAL 2026 F1 grid (all 11 teams, 22 drivers, current as of
mid-2026 -- including new entrant Cadillac and the Verstappen/Hadjar Red
Bull pairing) and reconstructs the REAL story of the 2026 Miami Grand Prix
from published race reports:
  - Final classification/gaps: Antonelli won, Norris +3.264s, Piastri
    +27.092s, Russell +43.051s, Verstappen +48.949s, Hamilton +53.753s,
    Colapinto +61.871s, Leclerc +64.245s (after a 20s penalty).
  - Russell undercut Leclerc: Russell pitted lap 20 with a fast stop,
    Leclerc pitted lap 21 with a slow stop (>1.3s lost), and Russell came
    out ahead.
  - Leclerc spun into the wall at Turn 3 on the final lap (57), damaging
    his car, then got a 20s penalty for leaving the track repeatedly --
    dropping him from ~6th to 8th.
  - 57-lap race, Medium->Hard one-stop as the dominant strategy, pit
    window laps 22-28 (C3 Hard / C4 Medium / C5 Soft compounds).

What is NOT real here: the exact second-by-second lap times. FastF1's
telemetry for this specific race isn't reachable from this sandbox, so
every driver's lap-by-lap pace is simulated from a pace offset tuned to
match the real gaps above, plus a simple tire-degradation curve. See
meta.data_source in the output -- swap in fetch_race_data.py's real output
whenever you have it; index.html doesn't change either way, since both
scripts emit the exact same JSON structure.
"""

import json
import random
import statistics

OUTPUT_FILE = "race_data.json"
TOTAL_LAPS = 57
AVG_PIT_LOSS_SECONDS = 21.0

random.seed(2026)  # fixed seed so re-running this script gives the same "race" every time


def lighten(hex_color, amount=0.35):
    """Blend a hex color toward white, for a teammate's line on the chart."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * amount)
    g = round(g + (255 - g) * amount)
    b = round(b + (255 - b) * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


TEAM_COLORS = {
    "Mercedes": "#27F4D2",
    "McLaren": "#FF8000",
    "Red Bull Racing": "#3671C6",
    "Ferrari": "#E80020",
    "Alpine": "#00A1E8",
    "Williams": "#00A3E0",
    "Aston Martin": "#229971",
    "Racing Bulls": "#6C98FF",
    "Audi": "#C8102E",
    "Haas": "#B6BABD",
    "Cadillac": "#B7935A",
}

# (code, full name, team, pace_offset) -- pace_offset is seconds-per-lap
# slower than the race winner (Antonelli). The 8 with real published gaps
# use gap/TOTAL_LAPS; the rest are estimated from general 2026 midfield
# competitiveness since only the top 8's exact gaps were reported.
DRIVERS = [
    ("ANT", "Kimi Antonelli", "Mercedes", 0.0),
    ("NOR", "Lando Norris", "McLaren", 3.264 / TOTAL_LAPS),
    ("PIA", "Oscar Piastri", "McLaren", 27.092 / TOTAL_LAPS),
    ("RUS", "George Russell", "Mercedes", 43.051 / TOTAL_LAPS),
    ("VER", "Max Verstappen", "Red Bull Racing", 48.949 / TOTAL_LAPS),
    ("HAM", "Lewis Hamilton", "Ferrari", 53.753 / TOTAL_LAPS),
    ("COL", "Franco Colapinto", "Alpine", 61.871 / TOTAL_LAPS),
    # Leclerc's clean racing pace before the final-lap incident -- faster
    # than Hamilton this race (he was running ahead of Russell/Verstappen
    # before the spin), the rest of his real 64.245s gap comes from the
    # scripted incident + penalty below, not from base pace.
    ("LEC", "Charles Leclerc", "Ferrari", 0.50),
    ("HAD", "Isack Hadjar", "Red Bull Racing", 1.15),
    ("GAS", "Pierre Gasly", "Alpine", 1.10),
    ("ALB", "Alexander Albon", "Williams", 1.30),
    ("SAI", "Carlos Sainz", "Williams", 1.35),
    ("ALO", "Fernando Alonso", "Aston Martin", 1.55),
    ("STR", "Lance Stroll", "Aston Martin", 1.75),
    ("LAW", "Liam Lawson", "Racing Bulls", 1.65),
    ("LIN", "Arvid Lindblad", "Racing Bulls", 1.85),
    ("HUL", "Nico Hulkenberg", "Audi", 1.90),
    ("BOR", "Gabriel Bortoleto", "Audi", 2.05),
    ("OCO", "Esteban Ocon", "Haas", 2.00),
    ("BEA", "Oliver Bearman", "Haas", 2.15),
    ("PER", "Sergio Perez", "Cadillac", 2.30),
    ("BOT", "Valtteri Bottas", "Cadillac", 2.45),
]

COMPOUND_PARAMS = {
    "SOFT":   {"base_offset": -0.9, "deg_per_lap": 0.22},
    "MEDIUM": {"base_offset": -0.4, "deg_per_lap": 0.11},
    "HARD":   {"base_offset": 0.0,  "deg_per_lap": 0.05},
}

FASTEST_BASE_LAP = 89.0  # seconds, race winner's pace on HARD tires, fresh, lap 1
# Kept deliberately small: it's realistic, but sized so it doesn't swamp the
# tire-degradation signal above when both trend with lap number at once.
FUEL_EFFECT_PER_LAP = 0.02

# Real strategy overrides for the Russell-undercuts-Leclerc sequence.
PIT_LAP_OVERRIDES = {"RUS": 20, "LEC": 21}
PIT_LOSS_EXTRA = {"LEC": 1.3}  # Leclerc's real slow tyre change
# Leclerc's real final-lap spin at Turn 3, plus his real 20s penalty.
LECLERC_INCIDENT_LAP = TOTAL_LAPS
LECLERC_INCIDENT_SECONDS = 15.0
LECLERC_PENALTY_SECONDS = 20.0

# The 8 drivers whose real finishing gaps we're matching all run the same
# Medium->Hard one-stop on the same lap (the real dominant strategy), so
# their pace_offset above -- not random strategy variance -- is what
# decides their simulated order. Everyone else gets a randomized strategy
# since we don't have their real gaps to match anyway.
FIXED_STRATEGY_CODES = {"ANT", "NOR", "PIA", "RUS", "VER", "HAM", "COL", "LEC"}
DEFAULT_FIXED_PIT_LAP = 25


def build_strategy(code):
    """One-stop Medium->Hard is the dominant real strategy at this race
    (pit window laps 22-28); a few cars mix in Soft or Hard starts for
    variety, matching the three compounds nominated for the weekend."""
    if code in FIXED_STRATEGY_CODES:
        pit_lap = PIT_LAP_OVERRIDES.get(code, DEFAULT_FIXED_PIT_LAP)
        return ["MEDIUM", "HARD"], pit_lap

    pit_lap = random.randint(22, 28)
    roll = random.random()
    if roll < 0.65:
        compounds = ["MEDIUM", "HARD"]
    elif roll < 0.85:
        compounds = ["SOFT", "HARD"]
    else:
        compounds = ["SOFT", "MEDIUM"]
    return compounds, pit_lap


def main():
    driver_colors = {}
    team_seen = set()
    for code, _name, team, _offset in DRIVERS:
        base = TEAM_COLORS[team]
        driver_colors[code] = base if team not in team_seen else lighten(base)
        team_seen.add(team)

    drivers_out = {}
    cumulative_time = {code: 0.0 for code, *_ in DRIVERS}
    raw_laps = {code: [] for code, *_ in DRIVERS}

    for code, full_name, team, pace_offset in DRIVERS:
        base_pace = FASTEST_BASE_LAP + pace_offset + random.uniform(-0.05, 0.05)
        compounds, pit_lap = build_strategy(code)
        pit_laps = [pit_lap] if pit_lap <= TOTAL_LAPS else []
        pit_loss = AVG_PIT_LOSS_SECONDS + PIT_LOSS_EXTRA.get(code, 0.0)

        entries = []
        stints = []
        tyre_life = 0
        current_compound = compounds[0]

        for lap_number in range(1, TOTAL_LAPS + 1):
            is_pit_lap = lap_number in pit_laps
            if lap_number > 1 and (lap_number - 1) in pit_laps:
                current_compound = compounds[1]
                tyre_life = 0
            tyre_life += 1

            params = COMPOUND_PARAMS[current_compound]
            lap_time = (
                base_pace
                + params["base_offset"]
                + params["deg_per_lap"] * (tyre_life - 1)
                - FUEL_EFFECT_PER_LAP * lap_number
                + random.uniform(-0.15, 0.15)
            )
            if is_pit_lap:
                lap_time += pit_loss

            if code == "LEC" and lap_number == LECLERC_INCIDENT_LAP:
                lap_time += LECLERC_INCIDENT_SECONDS + LECLERC_PENALTY_SECONDS

            cumulative_time[code] += lap_time
            entries.append({
                "lap": lap_number,
                "lap_time": round(lap_time, 3),
                "position": None,  # filled in below, once every driver's cumulative time is known
                "compound": current_compound,
                "tyre_life": tyre_life,
                "is_pit_lap": is_pit_lap,
                "cumulative_time": cumulative_time[code],
            })

            if lap_number == 1 or (lap_number - 1) in pit_laps:
                stints.append({"compound": current_compound, "start_lap": lap_number, "end_lap": lap_number})
            else:
                stints[-1]["end_lap"] = lap_number

        raw_laps[code] = entries
        drivers_out[code] = {
            "abbreviation": code,
            "full_name": full_name,
            "team": team,
            "color": driver_colors[code],
            "final_position": None,
            "laps": entries,
            "pit_laps": pit_laps,
            "stints": stints,
        }

    # Now that every driver's cumulative race time is known lap-by-lap,
    # rank them at each lap to get realistic, self-consistent positions.
    for lap_number in range(1, TOTAL_LAPS + 1):
        ranking = sorted(
            DRIVERS,
            key=lambda d: raw_laps[d[0]][lap_number - 1]["cumulative_time"],
        )
        for position, (code, *_rest) in enumerate(ranking, start=1):
            raw_laps[code][lap_number - 1]["position"] = position

    # Set final classification from the last lap's position, and drop the
    # cumulative_time helper field (it was only needed to compute position).
    final_totals = {code: entries[-1]["cumulative_time"] for code, entries in raw_laps.items()}
    for code, entries in raw_laps.items():
        drivers_out[code]["final_position"] = entries[-1]["position"]
        for e in entries:
            del e["cumulative_time"]

    # Fit the same simple linear degradation model (lap_time vs tyre_life)
    # from these synthetic laps, exactly the way fetch_race_data.py fits it
    # from real laps -- this keeps the two scripts' output interchangeable.
    degradation_samples = {}
    for code, entries in raw_laps.items():
        for e in entries:
            if not e["is_pit_lap"] and e["compound"] in COMPOUND_PARAMS:
                degradation_samples.setdefault(e["compound"], []).append((e["tyre_life"], e["lap_time"]))

    def fit_linear(samples):
        n = len(samples)
        if n < 5:
            return None
        xs = [p[0] for p in samples]
        ys = [p[1] for p in samples]
        x_mean = statistics.fmean(xs)
        y_mean = statistics.fmean(ys)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        slope = num / den if den != 0 else 0.0
        intercept = y_mean - slope * x_mean
        return {"intercept": round(intercept, 4), "slope_per_lap": round(slope, 4), "sample_size": n}

    tire_degradation = {c: fit_linear(s) for c, s in degradation_samples.items() if fit_linear(s)}

    output = {
        "meta": {
            "year": 2026,
            "race_name": "Miami Grand Prix",
            "total_laps": TOTAL_LAPS,
            "data_source": "synthetic_placeholder",
            "note": ("Generated by generate_placeholder_data.py because this environment's "
                     "network policy blocks FastF1's data hosts. Real 2026 driver/team "
                     "lineup and the real strategic story of this race (Russell's undercut "
                     "on Leclerc, Leclerc's final-lap spin and penalty), but lap times are "
                     "simulated, not the actual recorded telemetry. Replace with "
                     "fetch_race_data.py's output for the real race."),
        },
        "tire_degradation": tire_degradation,
        "avg_pit_loss_seconds": AVG_PIT_LOSS_SECONDS,
        "drivers": drivers_out,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {OUTPUT_FILE} (synthetic placeholder data)")
    print(f"Tire degradation model: {tire_degradation}")
    winner_total = final_totals[DRIVERS[0][0] if drivers_out[DRIVERS[0][0]]["final_position"] == 1
                                 else next(c for c in final_totals if drivers_out[c]["final_position"] == 1)]
    print("Final order (by lap {}):".format(TOTAL_LAPS))
    for code, entries in sorted(raw_laps.items(), key=lambda kv: kv[1][-1]["position"]):
        gap = final_totals[code] - winner_total
        print(f"  P{entries[-1]['position']:>2}  {code}  ({drivers_out[code]['team']})  +{gap:6.3f}s")


if __name__ == "__main__":
    main()
