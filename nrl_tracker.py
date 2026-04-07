"""
NRL Bet Tracker — persistent history for all recommended bets.
Reads/writes history_db.json, provides helpers for dashboard generation.
No external dependencies — pure stdlib.
"""

import json
import os
import math
from datetime import datetime, timezone

DB_PATH = "history_db.json"
DEFAULT_BANKROLL = 80.0
SCHEMA_VERSION = 1

CALIBRATION_BUCKETS = [
    ("0-10%", 0.0, 0.10), ("10-20%", 0.10, 0.20), ("20-30%", 0.20, 0.30),
    ("30-40%", 0.30, 0.40), ("40-50%", 0.40, 0.50), ("50-60%", 0.50, 0.60),
    ("60-70%", 0.60, 0.70), ("70-80%", 0.70, 0.80), ("80-90%", 0.80, 0.90),
    ("90-100%", 0.90, 1.01),
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _empty_db():
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now_iso(),
            "last_updated": _now_iso(),
        },
        "rounds": [],
        "lifetime": _empty_lifetime(),
    }


def _empty_lifetime():
    return {
        "total_rounds": 0,
        "total_bets": 0,
        "total_wins": 0,
        "total_staked": 0.0,
        "total_returned": 0.0,
        "net_profit": 0.0,
        "roi_pct": 0.0,
        "win_rate": 0.0,
        "by_label": {
            "STRONG VALUE": {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
            "VALUE":        {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
            "MARGINAL":     {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
        },
        "by_bet_type": {
            "H2H":  {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
            "Line": {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
            "ATS":  {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
            "FTS":  {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
        },
        "model_calibration": [
            {"bucket": b, "bets": 0, "wins": 0} for b, _, _ in CALIBRATION_BUCKETS
        ],
        "bankroll_history": [],
    }


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_round_id(season, round_number):
    return f"{season}-R{round_number:02d}"


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_db():
    """Load history_db.json. Returns empty schema if file doesn't exist."""
    if not os.path.exists(DB_PATH):
        return _empty_db()
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"WARNING: Could not load {DB_PATH}: {e}. Starting fresh.")
        return _empty_db()


def save_db(db):
    """Atomically write db to history_db.json (write .tmp then rename)."""
    db["meta"]["last_updated"] = _now_iso()
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DB_PATH)


# ---------------------------------------------------------------------------
# Bankroll helpers
# ---------------------------------------------------------------------------

def get_current_bankroll(db):
    """Return the most recent bankroll_end, or DEFAULT_BANKROLL if no history."""
    for rnd in reversed(db.get("rounds", [])):
        end = rnd.get("round_result", {}).get("bankroll_end")
        if end is not None:
            return float(end)
    return DEFAULT_BANKROLL


def _kelly_stake(model_prob, odds, bankroll):
    """Quarter-Kelly stake. Returns dollar amount capped at 25% of bankroll."""
    if odds <= 1 or model_prob <= 0:
        return 0.0
    edge = model_prob * odds - 1
    if edge <= 0:
        return 0.0
    full_kelly = edge / (odds - 1)
    quarter_kelly = full_kelly * 0.25
    return round(min(quarter_kelly * bankroll, bankroll * 0.25), 2)


# ---------------------------------------------------------------------------
# Save round predictions
# ---------------------------------------------------------------------------

def save_round_predictions(db, season, round_number, games_analysis, bankroll):
    """
    Build and insert/update a round record from analyse_game() outputs.
    Idempotent: re-running the same round preserves existing WIN/LOSS results.
    Stores best_bet as recommended_bet, plus full h2h/spread/ats/fts bet lists.
    """
    round_id = _make_round_id(season, round_number)
    n_games = len(games_analysis)
    stake = round(bankroll / n_games, 2) if n_games > 0 else 0.0

    # Find existing round if any
    existing = None
    for r in db["rounds"]:
        if r["round_id"] == round_id:
            existing = r
            break

    # Build game entries
    game_entries = []
    for g in games_analysis:
        game_id = g.get("id") or f"{g['home_team']}-{g['away_team']}"

        # Find existing game entry to preserve any already-recorded results
        existing_game = None
        if existing:
            for eg in existing.get("games", []):
                if eg["game_id"] == game_id:
                    existing_game = eg
                    break

        # Snapshot of recommended bet (best_bet = highest value_score)
        best = g.get("best_bet")
        rec_bet = None
        if best:
            rec_bet = {
                "bet_type": best.get("bet_type"),
                "team": best.get("team") or best.get("player"),
                "description": best.get("description"),
                "odds": best.get("odds"),
                "model_prob": best.get("model_prob"),
                "implied_prob": best.get("implied_prob"),
                "edge": best.get("edge"),
                "label": best.get("label"),
                "value_score": best.get("value_score"),
            }

        # Store all recommended bets for strategy comparison
        def _compact_bet(b):
            keys = ["bet_type", "team", "player", "description", "odds",
                    "model_prob", "implied_prob", "edge", "label", "value_score", "exp_return"]
            return {k: b[k] for k in keys if k in b}

        entry = {
            "game_id": game_id,
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "kickoff": g.get("kickoff", ""),
            "recommended_bet": rec_bet,
            "h2h_bets": [_compact_bet(b) for b in g.get("h2h_bets", [])],
            "spread_bets": [_compact_bet(b) for b in g.get("spread_bets", [])],
            "ats_picks": [_compact_bet(b) for b in g.get("ats_picks", [])[:5]],
            "fts_picks": [_compact_bet(b) for b in g.get("fts_picks", [])[:5]],
            "stake": stake,
            # Preserve any existing result fields
            "result": None,
            "won": None,
            "payout": None,
        }

        if existing_game:
            entry["result"] = existing_game.get("result")
            entry["won"] = existing_game.get("won")
            entry["payout"] = existing_game.get("payout")

        game_entries.append(entry)

    # Build or update round record
    round_record = {
        "round_id": round_id,
        "season": season,
        "round_number": round_number,
        "generated_at": _now_iso(),
        "bankroll_start": bankroll,
        "stake_per_game": stake,
        "strategy": "flat_compounding",
        "games": game_entries,
        "round_result": {
            "resolved_at": None,
            "wins": None,
            "losses": None,
            "total_staked": None,
            "total_returned": None,
            "net_profit": None,
            "bankroll_end": None,
        },
    }

    # Preserve round_result if round already has resolved games
    if existing and existing.get("round_result", {}).get("resolved_at"):
        round_record["round_result"] = existing["round_result"]

    if existing:
        idx = db["rounds"].index(existing)
        db["rounds"][idx] = round_record
        print(f"  Updated round {round_id} in history_db.json")
    else:
        db["rounds"].append(round_record)
        print(f"  Saved round {round_id} to history_db.json")

    return round_record


# ---------------------------------------------------------------------------
# Record results
# ---------------------------------------------------------------------------

def record_results(db, round_id, results_str):
    """
    Parse a results string and update game outcomes for the given round.

    results_str format: comma-separated "TeamName:W" or "TeamName:L" entries.
    TeamName is matched case-insensitively against game teams (substring match).

    Example: "Broncos:W,Raiders:L,Storm:W,Panthers:W,Cowboys:W,Bulldogs:L,Sea Eagles:W,Titans:L"

    Returns (updated_db, summary_message).
    """
    # Find the round
    target = None
    for r in db["rounds"]:
        if r["round_id"] == round_id:
            target = r
            break

    if not target:
        msg = f"ERROR: Round {round_id} not found in history_db.json. Run the tipsheet generator first."
        print(msg)
        return db, msg

    # Parse results string
    entries = [e.strip() for e in results_str.split(",") if e.strip()]
    parsed = {}  # team_fragment_lower -> "WIN" or "LOSS"
    for entry in entries:
        if ":" not in entry:
            print(f"  WARNING: Skipping malformed entry '{entry}' (expected 'Team:W' or 'Team:L')")
            continue
        team_part, outcome_part = entry.rsplit(":", 1)
        team_part = team_part.strip()
        outcome_char = outcome_part.strip().upper()
        if outcome_char not in ("W", "L"):
            print(f"  WARNING: Unknown outcome '{outcome_char}' for '{team_part}' — use W or L")
            continue
        parsed[team_part.lower()] = "WIN" if outcome_char == "W" else "LOSS"

    # Match results to games
    matched = 0
    for game in target["games"]:
        home = game["home_team"].lower()
        away = game["away_team"].lower()

        winning_team = None
        for fragment, outcome in parsed.items():
            if fragment in home or fragment in away:
                # Determine winning team based on outcome
                if outcome == "WIN":
                    # Fragment matches a team, and that team WON
                    if fragment in home:
                        winning_team = game["home_team"]
                    else:
                        winning_team = game["away_team"]
                else:
                    # Fragment matches a team, and that team LOST → other team won
                    if fragment in home:
                        winning_team = game["away_team"]
                    else:
                        winning_team = game["home_team"]
                break

        if winning_team is None:
            print(f"  WARNING: No result found for {game['home_team']} vs {game['away_team']}")
            continue

        # Resolve recommended_bet win/loss
        rec = game.get("recommended_bet")
        if rec:
            bet_team = (rec.get("team") or "").lower()
            bet_type = rec.get("bet_type", "H2H")

            if bet_type == "H2H":
                won = winning_team.lower() in bet_team or bet_team in winning_team.lower()
                game["result"] = "WIN" if won else "LOSS"
                game["won"] = won
                game["payout"] = round(game["stake"] * rec["odds"], 2) if won else 0.0
                matched += 1
            else:
                # Non-H2H bets can't be auto-resolved from match result alone
                # Mark game result but leave bet result pending
                game["result"] = "GAME_RESOLVED"
                game["won"] = None
                game["payout"] = None
                matched += 1
        else:
            matched += 1

    if matched == 0:
        msg = f"WARNING: No games matched in round {round_id}. Check team name spelling."
        print(msg)
        return db, msg

    # Compute round aggregates (only for H2H bets where we can auto-resolve)
    resolved_games = [g for g in target["games"] if g.get("won") is not None]
    wins = sum(1 for g in resolved_games if g.get("won"))
    losses = len(resolved_games) - wins
    total_staked = round(sum(g["stake"] for g in resolved_games), 2)
    total_returned = round(sum(g.get("payout") or 0 for g in resolved_games), 2)
    net_profit = round(total_returned - total_staked, 2)
    bankroll_start = target.get("bankroll_start", DEFAULT_BANKROLL)
    # For unresolved games (non-H2H), treat as returned 0 for now
    all_games_resolved = all(g.get("result") is not None for g in target["games"])
    bankroll_end = round(bankroll_start - total_staked + total_returned, 2)

    target["round_result"] = {
        "resolved_at": _now_iso(),
        "wins": wins,
        "losses": losses,
        "total_staked": total_staked,
        "total_returned": total_returned,
        "net_profit": net_profit,
        "bankroll_end": bankroll_end if all_games_resolved else None,
    }

    _recompute_lifetime(db)

    roi = round((net_profit / total_staked * 100), 1) if total_staked else 0
    summary = (
        f"Round {round_id}: {wins}W/{losses}L | "
        f"Staked ${total_staked:.2f} | Returned ${total_returned:.2f} | "
        f"Profit ${net_profit:+.2f} | ROI {roi:+.1f}% | "
        f"Bankroll: ${bankroll_start:.2f} → ${bankroll_end:.2f}"
    )
    print(f"  {summary}")
    return db, summary


# ---------------------------------------------------------------------------
# Lifetime recompute
# ---------------------------------------------------------------------------

def _recompute_lifetime(db):
    """Recompute the lifetime block from scratch using all resolved rounds."""
    lt = _empty_lifetime()

    for rnd in db["rounds"]:
        rr = rnd.get("round_result", {})

        # If round has no per-game detail (e.g. seeded historic round), use aggregate
        games = rnd.get("games", [])
        if not games and rr.get("wins") is not None:
            # Summary-only round (seeded)
            wins = rr["wins"]
            losses = rr.get("losses", 0)
            staked = rr.get("total_staked", 0.0) or 0.0
            returned = rr.get("total_returned", 0.0) or 0.0
            lt["total_rounds"] += 1
            lt["total_bets"] += wins + losses
            lt["total_wins"] += wins
            lt["total_staked"] += staked
            lt["total_returned"] += returned
            # Add to bankroll history
            bank = rr.get("bankroll_end")
            if bank is not None:
                lt["bankroll_history"].append({
                    "round_id": rnd["round_id"],
                    "bankroll": bank,
                    "net_profit": rr.get("net_profit", 0.0),
                })
            continue

        # Process per-game results
        resolved = [g for g in games if g.get("won") is not None]
        if not resolved:
            continue

        lt["total_rounds"] += 1
        for game in resolved:
            won = game["won"]
            stake = game.get("stake", 0.0)
            payout = game.get("payout", 0.0) or 0.0
            rec = game.get("recommended_bet") or {}
            label = rec.get("label") or "MARGINAL"
            bet_type = rec.get("bet_type", "H2H")

            lt["total_bets"] += 1
            lt["total_staked"] += stake
            lt["total_returned"] += payout
            if won:
                lt["total_wins"] += 1

            # By label
            if label in lt["by_label"]:
                lt["by_label"][label]["bets"] += 1
                lt["by_label"][label]["staked"] += stake
                lt["by_label"][label]["returned"] += payout
                if won:
                    lt["by_label"][label]["wins"] += 1

            # By bet type
            if bet_type in lt["by_bet_type"]:
                lt["by_bet_type"][bet_type]["bets"] += 1
                lt["by_bet_type"][bet_type]["staked"] += stake
                lt["by_bet_type"][bet_type]["returned"] += payout
                if won:
                    lt["by_bet_type"][bet_type]["wins"] += 1

            # Model calibration (use recommended_bet model_prob)
            mp = rec.get("model_prob", 0.5)
            for i, (_, lo, hi) in enumerate(CALIBRATION_BUCKETS):
                if lo <= mp < hi:
                    lt["model_calibration"][i]["bets"] += 1
                    if won:
                        lt["model_calibration"][i]["wins"] += 1
                    break

        # Bankroll history
        bank = rr.get("bankroll_end")
        if bank is not None:
            lt["bankroll_history"].append({
                "round_id": rnd["round_id"],
                "bankroll": bank,
                "net_profit": rr.get("net_profit", 0.0),
            })

    # Derived totals
    lt["net_profit"] = round(lt["total_returned"] - lt["total_staked"], 2)
    lt["roi_pct"] = round(lt["net_profit"] / lt["total_staked"] * 100, 2) if lt["total_staked"] else 0.0
    lt["win_rate"] = round(lt["total_wins"] / lt["total_bets"] * 100, 1) if lt["total_bets"] else 0.0

    # Round label/type dicts for cleanliness
    for label_data in lt["by_label"].values():
        label_data["staked"] = round(label_data["staked"], 2)
        label_data["returned"] = round(label_data["returned"], 2)
    for type_data in lt["by_bet_type"].values():
        type_data["staked"] = round(type_data["staked"], 2)
        type_data["returned"] = round(type_data["returned"], 2)

    db["lifetime"] = lt


# ---------------------------------------------------------------------------
# Strategy comparison
# ---------------------------------------------------------------------------

def get_strategy_comparison(db):
    """
    Compute what bankroll would look like under three strategies across resolved rounds.
    Returns dict with lists for Chart.js: round_ids, flat, compounding, kelly.
    """
    FLAT_STAKE = 10.0
    flat_bank = DEFAULT_BANKROLL
    comp_bank = DEFAULT_BANKROLL
    kelly_bank = DEFAULT_BANKROLL

    round_ids = []
    flat_series = []
    comp_series = []
    kelly_series = []

    # Start point
    round_ids.append("Start")
    flat_series.append(flat_bank)
    comp_series.append(comp_bank)
    kelly_series.append(kelly_bank)

    for rnd in sorted(db.get("rounds", []), key=lambda r: r["round_id"]):
        games = rnd.get("games", [])
        rr = rnd.get("round_result", {})

        # Summary-only round (seeded historic)
        if not games and rr.get("wins") is not None:
            wins = rr["wins"]
            losses = rr.get("losses", 0)
            total_games = wins + losses
            # Approximate: use aggregate for flat/comp, skip kelly
            flat_staked = total_games * FLAT_STAKE
            flat_returned = rr.get("total_returned", 0.0) or 0.0
            flat_bank = round(flat_bank - flat_staked + flat_returned, 2)

            comp_stake = round(comp_bank / total_games, 2) if total_games > 0 else 0
            comp_returned = rr.get("total_returned", 0.0) or 0.0
            comp_bank = round(comp_bank - (comp_stake * total_games) + comp_returned, 2)

            # Kelly for seeded rounds: approximate same as comp
            kelly_bank = comp_bank

            round_ids.append(rnd["round_id"])
            flat_series.append(round(flat_bank, 2))
            comp_series.append(round(comp_bank, 2))
            kelly_series.append(round(kelly_bank, 2))
            continue

        resolved = [g for g in games if g.get("won") is not None]
        if not resolved:
            continue

        # Flat $10 strategy
        flat_round_pl = 0.0
        for g in resolved:
            won = g["won"]
            odds = (g.get("recommended_bet") or {}).get("odds", 1.0) or 1.0
            flat_round_pl += (FLAT_STAKE * odds - FLAT_STAKE) if won else -FLAT_STAKE
        flat_bank = round(flat_bank + flat_round_pl, 2)

        # Compounding strategy (divide bankroll by n games)
        n = len(resolved)
        comp_stake = round(comp_bank / n, 2) if n > 0 else 0
        comp_pl = 0.0
        for g in resolved:
            won = g["won"]
            odds = (g.get("recommended_bet") or {}).get("odds", 1.0) or 1.0
            comp_pl += (comp_stake * odds - comp_stake) if won else -comp_stake
        comp_bank = round(comp_bank + comp_pl, 2)

        # Quarter-Kelly strategy
        kelly_pl = 0.0
        for g in resolved:
            rec = g.get("recommended_bet") or {}
            mp = rec.get("model_prob", 0.5) or 0.5
            odds = rec.get("odds", 1.0) or 1.0
            stake = _kelly_stake(mp, odds, kelly_bank)
            won = g["won"]
            kelly_pl += (stake * odds - stake) if won else -stake
        kelly_bank = round(kelly_bank + kelly_pl, 2)

        round_ids.append(rnd["round_id"])
        flat_series.append(round(flat_bank, 2))
        comp_series.append(round(comp_bank, 2))
        kelly_series.append(round(kelly_bank, 2))

    return {
        "round_ids": round_ids,
        "flat": flat_series,
        "compounding": comp_series,
        "kelly": kelly_series,
    }


# ---------------------------------------------------------------------------
# Strategy filter recommendations
# ---------------------------------------------------------------------------

def get_filter_recommendations(db):
    """
    Analyse performance by label and bet_type. Return filter suggestions.
    Only includes filters with sample_size >= 10 bets.
    """
    MIN_SAMPLE = 10
    recommendations = []

    lt = db.get("lifetime", {})
    total_bets = lt.get("total_bets", 0)
    total_wins = lt.get("total_wins", 0)
    total_staked = lt.get("total_staked", 0.0)
    total_returned = lt.get("total_returned", 0.0)

    # Baseline
    if total_bets >= MIN_SAMPLE and total_staked > 0:
        roi = round((total_returned - total_staked) / total_staked * 100, 1)
        recommendations.append({
            "filter": "All bets (current strategy)",
            "bets": total_bets,
            "wins": total_wins,
            "win_rate_pct": round(total_wins / total_bets * 100, 1) if total_bets else 0,
            "roi_pct": roi,
            "verdict": "Baseline",
        })

    # By label
    for label, data in lt.get("by_label", {}).items():
        bets = data["bets"]
        wins = data["wins"]
        staked = data["staked"]
        returned = data["returned"]
        if bets >= MIN_SAMPLE and staked > 0:
            roi = round((returned - staked) / staked * 100, 1)
            win_rate = round(wins / bets * 100, 1)
            verdict = "Recommended" if roi > 5 else ("Caution" if roi > 0 else "Avoid")
            recommendations.append({
                "filter": f"{label} only",
                "bets": bets,
                "wins": wins,
                "win_rate_pct": win_rate,
                "roi_pct": roi,
                "verdict": verdict,
            })

    # By bet type
    for bet_type, data in lt.get("by_bet_type", {}).items():
        bets = data["bets"]
        wins = data["wins"]
        staked = data["staked"]
        returned = data["returned"]
        if bets >= MIN_SAMPLE and staked > 0:
            roi = round((returned - staked) / staked * 100, 1)
            win_rate = round(wins / bets * 100, 1)
            verdict = "Recommended" if roi > 5 else ("Caution" if roi > 0 else "Avoid")
            recommendations.append({
                "filter": f"{bet_type} bets only",
                "bets": bets,
                "wins": wins,
                "win_rate_pct": win_rate,
                "roi_pct": roi,
                "verdict": verdict,
            })

    # Sort by ROI descending
    recommendations.sort(key=lambda x: x["roi_pct"], reverse=True)
    return recommendations
