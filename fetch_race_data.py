"""
fetch_race_data.py
-------------------
One-time data-pull script for the F1 "what-if" race replay simulator.

What this does, step by step:
  1. Turns on FastF1's local disk cache so we only hit the network once.
  2. Downloads the full lap-by-lap timing data for one real Grand Prix.
  3. Cleans it up into a simple structure: per-driver lists of
     {lap, lap_time, position, compound, is_pit_lap}.
  4. Fits a very simple *linear* tire-degradation model per compound
     (lap time vs. laps-on-that-set-of-tires) using the real race laps,
     so the frontend can "recalculate" a driver's pace under a
     different strategy without needing any of this Python code.
  5. Writes everything to race_data.json, which is the ONLY thing
     index.html needs to load. No backend, no API calls from the browser.

Run it with:  python3 fetch_race_data.py
"""

import json
import statistics

import fastf1
import fastf1.plotting

# ---------------------------------------------------------------------------
# STEP 0: Pick the race.
#
# Set to the 2026 Miami GP: a clear, well-documented strategy story this
# season (Russell undercut Leclerc with an early pit stop, then Leclerc
# spun on the final lap and took a penalty on top). One-stop Medium->Hard
# was the dominant strategy, pit window laps 22-28, which makes the
# "what if he'd pitted on a different lap / tire" question very legible.
#
# If FastF1 doesn't yet recognize this event (new-season data can lag),
# swap in any completed 2026 round by name, or fall back to a past season.
# Just change YEAR / RACE_NAME below and rerun this script.
# ---------------------------------------------------------------------------
YEAR = 2026
RACE_NAME = "Miami Grand Prix"
SESSION_TYPE = "R"  # "R" = the race itself (as opposed to qualifying, practice, etc.)

CACHE_DIR = "./f1_cache"
OUTPUT_FILE = "race_data.json"


def main():
    # -----------------------------------------------------------------
    # STEP 1: Enable FastF1's cache.
    # FastF1 pulls raw timing data from the F1 live-timing API. That's a
    # slow, chunky download, so FastF1 lets you cache it to disk and
    # reuse it forever after the first run. This just needs a folder to
    # write into.
    # -----------------------------------------------------------------
    import os
    os.makedirs(CACHE_DIR, exist_ok=True)
    fastf1.Cache.enable_cache(CACHE_DIR)

    # -----------------------------------------------------------------
    # STEP 2: Load the session.
    # get_session() looks up the event by year + name, and session.load()
    # actually fetches (or reads from cache) lap times, tire data,
    # positions, etc.
    # -----------------------------------------------------------------
    print(f"Loading {YEAR} {RACE_NAME} ({SESSION_TYPE})... (first run downloads data, "
          f"later runs use the local cache in {CACHE_DIR}/)")
    session = fastf1.get_session(YEAR, RACE_NAME, SESSION_TYPE)
    session.load(laps=True, telemetry=False, weather=False, messages=False)

    laps = session.laps
    results = session.results

    print(f"Loaded {len(laps)} total laps across {laps['Driver'].nunique()} drivers.")

    # -----------------------------------------------------------------
    # STEP 3: Build per-driver metadata (name, team, color) from the
    # final classification, so the frontend has something nice to show
    # in the driver-picker dropdown.
    # -----------------------------------------------------------------
    driver_meta = {}
    for _, row in results.iterrows():
        code = row["Abbreviation"]
        try:
            color = fastf1.plotting.get_driver_color(code, session=session)
        except Exception:
            color = "#888888"  # fallback if fastf1 doesn't recognise the driver/team
        driver_meta[code] = {
            "abbreviation": code,
            "full_name": row["FullName"],
            "team": row["TeamName"],
            "color": color,
            "final_position": None if row["Position"] is None else int(row["Position"]),
        }

    # -----------------------------------------------------------------
    # STEP 4: Walk the lap-by-lap data and build a clean per-driver
    # timeline. FastF1 gives lap times as pandas Timedelta objects, so we
    # convert them to plain seconds (float) which is much easier to use
    # in JavaScript later.
    # -----------------------------------------------------------------
    drivers_out = {}
    # Also collect (compound, tyre_life, lap_time_seconds) for every
    # "clean" racing lap, across the whole field, to fit the tire
    # degradation model in step 5.
    degradation_samples = {}  # compound -> list of (tyre_life, lap_time_s)

    for code in driver_meta:
        driver_laps = laps[laps["Driver"] == code].sort_values("LapNumber")

        lap_entries = []
        pit_laps = []
        stints = []
        prev_stint = None

        for _, lap in driver_laps.iterrows():
            lap_number = int(lap["LapNumber"])
            lap_time = lap["LapTime"]
            lap_time_s = lap_time.total_seconds() if lap_time is not None and not lap_time is None and lap_time == lap_time else None
            # pandas NaT/NaN check: a missing lap time shows up as NaT, and
            # `NaT == NaT` is False, so comparing to itself detects it.
            if lap_time is None or lap_time != lap_time:
                lap_time_s = None

            compound = lap["Compound"] if isinstance(lap["Compound"], str) else "UNKNOWN"
            tyre_life = None if lap["TyreLife"] != lap["TyreLife"] else int(lap["TyreLife"])
            position = None if lap["Position"] != lap["Position"] else int(lap["Position"])

            is_pit_lap = bool(lap["PitInTime"] == lap["PitInTime"]) or bool(lap["PitOutTime"] == lap["PitOutTime"])
            if lap["PitInTime"] == lap["PitInTime"]:  # driver pitted in at the end of this lap
                pit_laps.append(lap_number)

            lap_entries.append({
                "lap": lap_number,
                "lap_time": round(lap_time_s, 3) if lap_time_s is not None else None,
                "position": position,
                "compound": compound,
                "tyre_life": tyre_life,
                "is_pit_lap": is_pit_lap,
            })

            # Track compound stints (contiguous laps on the same tire set).
            stint_id = lap["Stint"]
            if stint_id != prev_stint:
                stints.append({"compound": compound, "start_lap": lap_number, "end_lap": lap_number})
                prev_stint = stint_id
            else:
                stints[-1]["end_lap"] = lap_number

            # Only use "clean" laps for fitting degradation: has a lap
            # time, has a known tyre life, and wasn't a pit-in/pit-out
            # lap (those are slowed down by the pit lane itself, not by
            # tire wear).
            if (
                lap_time_s is not None
                and tyre_life is not None
                and not is_pit_lap
                and compound in ("SOFT", "MEDIUM", "HARD")
            ):
                degradation_samples.setdefault(compound, []).append((tyre_life, lap_time_s))

        drivers_out[code] = {
            **driver_meta[code],
            "laps": lap_entries,
            "pit_laps": pit_laps,
            "stints": stints,
        }

    # -----------------------------------------------------------------
    # STEP 5: Fit a simple linear tire-degradation model per compound:
    #     lap_time(tyre_life) = intercept + slope * tyre_life
    # using ordinary least squares on the real clean-lap samples. This
    # is intentionally simple (linear falloff) as requested -- the
    # frontend will reuse these exact coefficients to recompute lap
    # times for a "what if" strategy change.
    # -----------------------------------------------------------------
    def fit_linear(samples):
        """Least-squares fit of y = intercept + slope * x, plus how many
        points went into it (so we know how much to trust it)."""
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
        return {
            "intercept": round(intercept, 4),
            "slope_per_lap": round(slope, 4),
            "sample_size": n,
        }

    tire_degradation = {}
    for compound, samples in degradation_samples.items():
        model = fit_linear(samples)
        if model:
            tire_degradation[compound] = model

    # A rough, single-number "time lost doing a pit stop" (pit lane transit
    # + stationary time), estimated from the gap between a driver's lap
    # immediately before pitting and their out-lap. This is a coarse
    # average used only as a fallback constant in the frontend model.
    pit_loss_samples = []
    for code, d in drivers_out.items():
        entries = d["laps"]
        for i, entry in enumerate(entries):
            if entry["is_pit_lap"] and entry["lap_time"] and i > 0:
                prev = entries[i - 1]
                if prev["lap_time"]:
                    pit_loss_samples.append(entry["lap_time"] - prev["lap_time"])
    avg_pit_loss = round(statistics.fmean(pit_loss_samples), 2) if pit_loss_samples else 22.0

    # -----------------------------------------------------------------
    # STEP 6: Assemble the final JSON and write it to disk.
    # -----------------------------------------------------------------
    total_laps = int(laps["LapNumber"].max())
    output = {
        "meta": {
            "year": YEAR,
            "race_name": RACE_NAME,
            "total_laps": total_laps,
        },
        "tire_degradation": tire_degradation,
        "avg_pit_loss_seconds": avg_pit_loss,
        "drivers": drivers_out,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {OUTPUT_FILE}")
    print(f"Tire degradation model fitted from real laps: {tire_degradation}")
    print(f"Average pit stop time loss (estimated): {avg_pit_loss}s")


if __name__ == "__main__":
    main()
