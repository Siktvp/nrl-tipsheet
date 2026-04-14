"""
NRL Performance Dashboard Generator
Reads history_db.json and writes dashboard.html.
Run: python generate_dashboard.py
"""

import json
import os
from datetime import datetime
from jinja2 import Template
from nrl_tracker import load_db, get_strategy_comparison, get_filter_recommendations

USER_BETS_FILE = "user_bets.json"


def load_user_bets():
    """Load user_bets.json. Returns [] if missing or malformed."""
    if not os.path.exists(USER_BETS_FILE):
        return []
    try:
        with open(USER_BETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        bets = data.get("bets", [])
        # Compute derived fields
        for b in bets:
            odds = b.get("odds", 0)
            stake = b.get("stake", 0)
            result = b.get("result", "PENDING").upper()
            b["result"] = result
            if result == "WIN":
                b["payout"] = round(odds * stake, 2)
                b["profit"] = round(odds * stake - stake, 2)
            elif result == "LOSS":
                b["payout"] = 0.0
                b["profit"] = -round(stake, 2)
            else:
                b["payout"] = None
                b["profit"] = None
        return bets
    except Exception as e:
        print(f"WARNING: Could not load {USER_BETS_FILE}: {e}")
        return []


def user_bets_summary(bets):
    resolved = [b for b in bets if b["result"] in ("WIN", "LOSS")]
    wins = sum(1 for b in resolved if b["result"] == "WIN")
    staked = sum(b.get("stake", 0) for b in resolved)
    returned = sum(b.get("payout", 0) or 0 for b in resolved)
    profit = round(returned - staked, 2)
    roi = round(profit / staked * 100, 1) if staked else 0
    return {
        "total": len(bets),
        "resolved": len(resolved),
        "wins": wins,
        "losses": len(resolved) - wins,
        "staked": staked,
        "returned": returned,
        "profit": profit,
        "roi": roi,
    }

DASHBOARD_FILE = "dashboard.html"

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NRL Tracker — Performance Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --green: #16a34a; --red: #dc2626; --gold: #b45309; --blue: #1d4ed8;
    --bg: #0f172a; --card: #1e293b; --border: #334155; --text: #e2e8f0;
    --muted: #94a3b8; --accent: #38bdf8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; line-height: 1.5; }
  .container { max-width: 960px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 1.6rem; font-weight: 700; color: var(--accent); }
  h2 { font-size: 1.1rem; font-weight: 700; color: var(--accent); margin: 24px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  h3 { font-size: 0.9rem; font-weight: 600; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
  .header { display: flex; justify-content: space-between; align-items: center; padding: 16px 0 8px; border-bottom: 2px solid var(--accent); margin-bottom: 24px; flex-wrap: wrap; gap: 8px; }
  .header-links { display: flex; gap: 16px; align-items: center; }
  .header-links a { color: var(--accent); font-size: 0.85rem; text-decoration: none; }
  .header-links a:hover { text-decoration: underline; }
  .generated { color: var(--muted); font-size: 0.8rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .kpi-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; text-align: center; }
  .kpi-value { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
  .kpi-value.positive { color: #4ade80; }
  .kpi-value.negative { color: #f87171; }
  .kpi-label { font-size: 0.75rem; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
  .chart-container { position: relative; height: 280px; }
  .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 640px) { .chart-grid { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); background: #0f172a; }
  td { padding: 8px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 700; }
  .badge-pending { background: #451a03; color: #fbbf24; }
  .badge-win { background: #14532d; color: #4ade80; }
  .badge-loss { background: #450a0a; color: #f87171; }
  .badge-recommended { background: #0c1a3a; color: var(--accent); }
  .badge-caution { background: #451a03; color: #fbbf24; }
  .badge-avoid { background: #450a0a; color: #f87171; }
  .badge-baseline { background: #1e293b; color: var(--muted); }
  .positive { color: #4ade80; }
  .negative { color: #f87171; }
  .neutral { color: var(--muted); }
  .empty-state { text-align: center; color: var(--muted); padding: 40px 20px; }
  .empty-state-icon { font-size: 2rem; margin-bottom: 8px; }
  .empty-state p { margin-top: 4px; font-size: 0.85rem; }
  .strategy-note { background: #0c1a2e; border: 1px solid #1d4ed8; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 0.82rem; color: var(--muted); }
  .strategy-note strong { color: var(--text); }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <h1>NRL Tracker Dashboard</h1>
      <div class="generated">Generated {{ generated_at }}</div>
    </div>
    <div class="header-links">
      <a href="tipsheet_output.html">Tipsheet</a>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-value">{{ lifetime.total_rounds }}</div>
      <div class="kpi-label">Rounds Tracked</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if lifetime.win_rate >= 55 %}positive{% elif lifetime.win_rate >= 45 %}neutral{% else %}negative{% endif %}">
        {% if lifetime.total_bets > 0 %}{{ lifetime.win_rate }}%{% else %}—{% endif %}
      </div>
      <div class="kpi-label">Win Rate</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if lifetime.roi_pct > 0 %}positive{% elif lifetime.roi_pct < 0 %}negative{% else %}neutral{% endif %}">
        {% if lifetime.total_bets > 0 %}{{ "%+.1f" | format(lifetime.roi_pct) }}%{% else %}—{% endif %}
      </div>
      <div class="kpi-label">Lifetime ROI</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if current_bankroll > 80 %}positive{% elif current_bankroll < 80 %}negative{% else %}neutral{% endif %}">
        ${{ "%.2f" | format(current_bankroll) }}
      </div>
      <div class="kpi-label">Current Bankroll</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if lifetime.net_profit > 0 %}positive{% elif lifetime.net_profit < 0 %}negative{% else %}neutral{% endif %}">
        {% if lifetime.total_bets > 0 %}{{ "%+.2f" | format(lifetime.net_profit) }}{% else %}—{% endif %}
      </div>
      <div class="kpi-label">Net Profit ($)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value neutral">{{ lifetime.total_bets }}</div>
      <div class="kpi-label">Total Bets</div>
    </div>
  </div>

  <!-- Bankroll Growth -->
  <h2>Bankroll Growth</h2>
  {% if strategy_chart.round_ids | length > 1 %}
  <div class="card">
    <div class="strategy-note">
      <strong>Your Strategy</strong> (compounding): divide bankroll by 8 games each round and reinvest profits.<br>
      <strong>Flat $10</strong>: always stake $10 per game regardless of bankroll.<br>
      <strong>Quarter-Kelly</strong>: stake sized to model edge, capped at 25% of bankroll per bet.
    </div>
    <div class="chart-container">
      <canvas id="bankrollChart"></canvas>
    </div>
  </div>
  {% else %}
  <div class="card empty-state">
    <div class="empty-state-icon">📈</div>
    <strong>No resolved rounds yet</strong>
    <p>Bankroll chart will appear here after your first round result is recorded.</p>
  </div>
  {% endif %}

  <!-- Charts Row -->
  <div class="chart-grid">

    <!-- Win Rate per Round -->
    <div>
      <h2>Win Rate by Round</h2>
      {% if winrate_chart.labels | length > 0 %}
      <div class="card">
        <div class="chart-container">
          <canvas id="winrateChart"></canvas>
        </div>
      </div>
      {% else %}
      <div class="card empty-state">
        <div class="empty-state-icon">📊</div>
        <strong>No data yet</strong>
        <p>Win rate chart appears after rounds are resolved.</p>
      </div>
      {% endif %}
    </div>

    <!-- Performance by Label -->
    <div>
      <h2>Performance by Label</h2>
      {% if label_chart.labels | length > 0 %}
      <div class="card">
        <div class="chart-container">
          <canvas id="labelChart"></canvas>
        </div>
      </div>
      {% else %}
      <div class="card empty-state">
        <div class="empty-state-icon">🏷️</div>
        <strong>No data yet</strong>
        <p>Label performance appears after rounds are resolved.</p>
      </div>
      {% endif %}
    </div>

  </div>

  <!-- Model Calibration -->
  <h2>Model Calibration</h2>
  {% if calibration_chart.has_data %}
  <div class="card">
    <p style="color: var(--muted); font-size: 0.8rem; margin-bottom: 12px;">
      If the model is perfectly calibrated, the <strong style="color: #38bdf8;">Actual Win %</strong> line
      should follow the <strong style="color: #94a3b8;">Diagonal (Perfect)</strong> line. Points above = model underestimates. Points below = model overestimates.
    </p>
    <div class="chart-container">
      <canvas id="calibrationChart"></canvas>
    </div>
  </div>
  {% else %}
  <div class="card empty-state">
    <div class="empty-state-icon">🎯</div>
    <strong>Not enough data yet</strong>
    <p>Model calibration chart requires at least 10 resolved bets across probability buckets.</p>
  </div>
  {% endif %}

  <!-- My Punts -->
  <h2>My Punts</h2>
  <p style="color:var(--muted);font-size:0.82rem;margin-bottom:12px;">Your actual bets — recorded in <code>user_bets.json</code>. Tracked separately from the model recommendations.</p>
  {% if user_bets | length > 0 %}
  <div class="kpi-grid" style="margin-bottom:16px;">
    <div class="kpi-card">
      <div class="kpi-value {% if user_bets_summary.profit > 0 %}positive{% elif user_bets_summary.profit < 0 %}negative{% else %}neutral{% endif %}">
        {{ "%+.2f" | format(user_bets_summary.profit) if user_bets_summary.resolved > 0 else "—" }}
      </div>
      <div class="kpi-label">Net Profit ($)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if user_bets_summary.roi > 0 %}positive{% elif user_bets_summary.roi < 0 %}negative{% else %}neutral{% endif %}">
        {{ "%+.1f" | format(user_bets_summary.roi) ~ "%" if user_bets_summary.resolved > 0 else "—" }}
      </div>
      <div class="kpi-label">ROI</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{{ user_bets_summary.wins }}W / {{ user_bets_summary.losses }}L</div>
      <div class="kpi-label">Record</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">${{ "%.2f" | format(user_bets_summary.staked) }}</div>
      <div class="kpi-label">Total Staked</div>
    </div>
  </div>
  <div class="card" style="padding:0;overflow:hidden;">
    <table>
      <thead>
        <tr><th>Round</th><th>Bet</th><th>Odds</th><th>Stake</th><th>Result</th><th>Payout</th><th>Profit</th><th>Notes</th></tr>
      </thead>
      <tbody>
        {% for b in user_bets | sort(attribute='round_id', reverse=True) %}
        <tr>
          <td><strong>{{ b.round_id }}</strong><br><span style="color:var(--muted);font-size:0.74rem;">{{ b.game }}</span></td>
          <td>{{ b.description }}</td>
          <td>${{ b.odds }}</td>
          <td>${{ b.stake }}</td>
          <td>
            {% if b.result == "WIN" %}<span class="badge badge-win">WIN</span>
            {% elif b.result == "LOSS" %}<span class="badge badge-loss">LOSS</span>
            {% else %}<span class="badge badge-pending">PENDING</span>{% endif %}
          </td>
          <td class="{% if b.payout %}positive{% endif %}">
            {% if b.payout is not none %}${{ "%.2f" | format(b.payout) }}{% else %}—{% endif %}
          </td>
          <td class="{% if b.profit is not none %}{% if b.profit >= 0 %}positive{% else %}negative{% endif %}{% endif %}">
            {% if b.profit is not none %}{{ "%+.2f" | format(b.profit) }}{% else %}—{% endif %}
          </td>
          <td style="color:var(--muted);font-size:0.78rem;">{{ b.notes or "" }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="card empty-state">
    <div class="empty-state-icon">🎰</div>
    <strong>No personal punts recorded yet</strong>
    <p>Edit <code>user_bets.json</code> in the repo to add your actual bets.</p>
  </div>
  {% endif %}

  <!-- Strategy Recommendations -->
  <h2>Strategy Filter Analysis</h2>
  {% if recommendations | length > 0 %}
  <div class="card">
    <p style="color: var(--muted); font-size: 0.8rem; margin-bottom: 12px;">
      Based on {{ lifetime.total_bets }} resolved bets. Filters with fewer than 10 bets are excluded.
      Use these to refine which bets to take — not financial advice.
    </p>
    <table>
      <thead>
        <tr>
          <th>Filter</th>
          <th>Bets</th>
          <th>Wins</th>
          <th>Win Rate</th>
          <th>ROI</th>
          <th>Verdict</th>
        </tr>
      </thead>
      <tbody>
        {% for rec in recommendations %}
        <tr>
          <td><strong>{{ rec.filter }}</strong></td>
          <td>{{ rec.bets }}</td>
          <td>{{ rec.wins }}</td>
          <td class="{% if rec.win_rate_pct >= 55 %}positive{% elif rec.win_rate_pct >= 45 %}neutral{% else %}negative{% endif %}">{{ rec.win_rate_pct }}%</td>
          <td class="{% if rec.roi_pct > 0 %}positive{% elif rec.roi_pct < 0 %}negative{% else %}neutral{% endif %}">{{ "%+.1f" | format(rec.roi_pct) }}%</td>
          <td><span class="badge badge-{{ rec.verdict | lower }}">{{ rec.verdict }}</span></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="card empty-state">
    <div class="empty-state-icon">💡</div>
    <strong>Not enough data yet</strong>
    <p>Filter recommendations appear after at least 10 bets per category are resolved.</p>
  </div>
  {% endif %}

  <!-- This Week's Picks -->
  {% if current_picks %}
  <h2>{{ current_picks.round_id }} — Picks & Results</h2>
  {% if not current_picks.resolved %}
  <p style="color:var(--muted);font-size:0.82rem;margin-bottom:12px;">Results pending — enter them via GitHub Actions → "Record Round Results" to update the dashboard.</p>
  {% else %}
  <div class="kpi-grid" style="margin-bottom:16px;">
    <div class="kpi-card">
      <div class="kpi-value {% if current_picks.flat_profit > 0 %}positive{% elif current_picks.flat_profit < 0 %}negative{% else %}neutral{% endif %}">
        {{ "%+.2f" | format(current_picks.flat_profit) if current_picks.flat_profit is not none else "—" }}
      </div>
      <div class="kpi-label">Flat $10/game Net</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if current_picks.flat_roi > 0 %}positive{% elif current_picks.flat_roi < 0 %}negative{% else %}neutral{% endif %}">
        {{ "%+.1f" | format(current_picks.flat_roi) }}% if current_picks.flat_roi is not none else "—"
      </div>
      <div class="kpi-label">Flat $10 ROI</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">${{ "%.2f" | format(current_picks.flat_staked) }}</div>
      <div class="kpi-label">Total Staked ($10×{{ current_picks.bets }})</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value {% if current_picks.flat_returned >= current_picks.flat_staked %}positive{% else %}negative{% endif %}">
        ${{ "%.2f" | format(current_picks.flat_returned) }}
      </div>
      <div class="kpi-label">Total Returned</div>
    </div>
  </div>
  {% endif %}
  <div class="card" style="padding:0;overflow:hidden;">
    <table>
      <thead>
        <tr>
          <th>Game</th>
          <th>Recommendation</th>
          <th>Odds</th>
          <th>Label</th>
          <th>Result</th>
          <th>$10 Return</th>
          <th>Profit</th>
        </tr>
      </thead>
      <tbody>
        {% for g in current_picks.game_rows %}
        <tr>
          <td style="white-space:nowrap"><strong>{{ g.home_team }}</strong><br><span style="color:var(--muted);font-size:0.76rem;">vs {{ g.away_team }}</span></td>
          <td>{{ g.description }}</td>
          <td>${{ g.odds }}</td>
          <td>
            {% if g.label %}
            <span class="badge badge-{% if g.label == 'STRONG VALUE' %}win{% elif g.label == 'VALUE' %}recommended{% else %}pending{% endif %}">{{ g.label }}</span>
            {% else %}—{% endif %}
          </td>
          <td>
            {% if g.pending %}
            <span class="badge badge-pending">PENDING</span>
            {% elif g.won %}
            <span class="badge badge-win">WIN</span>
            {% else %}
            <span class="badge badge-loss">LOSS</span>
            {% endif %}
          </td>
          <td class="{% if g.flat_return is not none %}{% if g.flat_return > 0 %}positive{% else %}negative{% endif %}{% endif %}">
            {% if g.flat_return is not none %}${{ "%.2f" | format(g.flat_return) }}{% else %}—{% endif %}
          </td>
          <td class="{% if g.flat_profit is not none %}{% if g.flat_profit >= 0 %}positive{% else %}negative{% endif %}{% endif %}">
            {% if g.flat_profit is not none %}{{ "%+.2f" | format(g.flat_profit) }}{% else %}—{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <!-- Round-by-Round Table -->
  <h2>Round History</h2>
  <div class="card">
    {% if round_rows | length > 0 %}
    <table>
      <thead>
        <tr>
          <th>Round</th>
          <th>W / L</th>
          <th>Flat $10 Staked</th>
          <th>Flat $10 Returned</th>
          <th>Flat $10 Profit</th>
          <th>Flat $10 ROI</th>
          <th>Bankroll</th>
        </tr>
      </thead>
      <tbody>
        {% for row in round_rows %}
        <tr>
          <td><strong>{{ row.round_id }}</strong></td>
          <td>
            {% if row.resolved %}
            <span class="positive">{{ row.wins }}W</span> / <span class="negative">{{ row.losses }}L</span>
            {% else %}
            <span class="badge badge-pending">PENDING</span>
            {% endif %}
          </td>
          <td>{% if row.flat_staked %}${{ "%.2f" | format(row.flat_staked) }}{% else %}—{% endif %}</td>
          <td>{% if row.flat_returned is not none and row.resolved %}${{ "%.2f" | format(row.flat_returned) }}{% else %}—{% endif %}</td>
          <td class="{% if row.flat_profit is not none and row.resolved %}{% if row.flat_profit >= 0 %}positive{% else %}negative{% endif %}{% endif %}">
            {% if row.flat_profit is not none and row.resolved %}{{ "%+.2f" | format(row.flat_profit) }}{% else %}—{% endif %}
          </td>
          <td class="{% if row.flat_roi is not none and row.resolved %}{% if row.flat_roi >= 0 %}positive{% else %}negative{% endif %}{% endif %}">
            {% if row.flat_roi is not none and row.resolved %}{{ "%+.1f" | format(row.flat_roi) }}%{% else %}—{% endif %}
          </td>
          <td class="{% if row.bankroll_end %}{% if row.bankroll_end >= row.bankroll_start %}positive{% else %}negative{% endif %}{% endif %}">
            {% if row.bankroll_end %}${{ "%.2f" | format(row.bankroll_end) }}{% elif row.bankroll_start %}${{ "%.2f" | format(row.bankroll_start) }} →?{% else %}—{% endif %}
          </td>
        </tr>
        {% if row.game_rows %}
        <tr>
          <td colspan="7" style="padding:0;background:#0f172a;">
            <details style="padding:8px 12px;">
              <summary style="cursor:pointer;color:var(--muted);font-size:0.78rem;list-style:none;display:flex;align-items:center;gap:6px;">
                ▶ Show {{ row.game_rows | length }} game picks
              </summary>
              <table style="margin-top:8px;font-size:0.78rem;">
                <thead>
                  <tr>
                    <th>Game</th><th>Recommendation</th><th>Odds</th><th>Result</th><th>$10 Return</th><th>Profit</th>
                  </tr>
                </thead>
                <tbody>
                  {% for g in row.game_rows %}
                  <tr>
                    <td>{{ g.home_team }} vs {{ g.away_team }}</td>
                    <td>{{ g.description }}</td>
                    <td>${{ g.odds }}</td>
                    <td>
                      {% if g.pending %}<span class="badge badge-pending">PENDING</span>
                      {% elif g.won %}<span class="badge badge-win">WIN</span>
                      {% else %}<span class="badge badge-loss">LOSS</span>{% endif %}
                    </td>
                    <td class="{% if g.flat_return is not none %}{% if g.flat_return > 0 %}positive{% else %}negative{% endif %}{% endif %}">
                      {% if g.flat_return is not none %}${{ "%.2f" | format(g.flat_return) }}{% else %}—{% endif %}
                    </td>
                    <td class="{% if g.flat_profit is not none %}{% if g.flat_profit >= 0 %}positive{% else %}negative{% endif %}{% endif %}">
                      {% if g.flat_profit is not none %}{{ "%+.2f" | format(g.flat_profit) }}{% else %}—{% endif %}
                    </td>
                  </tr>
                  {% endfor %}
                </tbody>
              </table>
            </details>
          </td>
        </tr>
        {% endif %}
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty-state">
      <div class="empty-state-icon">📋</div>
      <strong>No rounds recorded yet</strong>
      <p>Round history will appear here after the tipsheet runs for the first time.</p>
    </div>
    {% endif %}
  </div>

  <div style="text-align:center; color: var(--muted); font-size: 0.75rem; margin-top: 24px; padding-bottom: 24px;">
    This is a statistical tracking tool, not financial advice. Gamble responsibly.
  </div>

</div>

<script>
const chartDefaults = {
  color: '#94a3b8',
  borderColor: '#334155',
};
Chart.defaults.color = chartDefaults.color;
Chart.defaults.borderColor = chartDefaults.borderColor;

{% if strategy_chart.round_ids | length > 1 %}
// Bankroll Growth Chart
new Chart(document.getElementById('bankrollChart'), {
  type: 'line',
  data: {
    labels: {{ strategy_chart.round_ids | tojson }},
    datasets: [
      {
        label: 'Your Strategy (Compounding)',
        data: {{ strategy_chart.compounding | tojson }},
        borderColor: '#4ade80',
        backgroundColor: 'rgba(74,222,128,0.08)',
        tension: 0.3,
        borderWidth: 2.5,
        pointRadius: 4,
      },
      {
        label: 'Flat $10/game',
        data: {{ strategy_chart.flat | tojson }},
        borderColor: '#38bdf8',
        backgroundColor: 'rgba(56,189,248,0.05)',
        tension: 0.3,
        borderWidth: 2,
        borderDash: [5,3],
        pointRadius: 3,
      },
      {
        label: 'Quarter-Kelly',
        data: {{ strategy_chart.kelly | tojson }},
        borderColor: '#a78bfa',
        backgroundColor: 'rgba(167,139,250,0.05)',
        tension: 0.3,
        borderWidth: 2,
        borderDash: [2,4],
        pointRadius: 3,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', labels: { color: '#94a3b8', font: { size: 11 } } },
      tooltip: {
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}`
        }
      }
    },
    scales: {
      x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
      y: {
        grid: { color: '#1e293b' },
        ticks: { color: '#94a3b8', callback: v => '$' + v },
      },
    },
  },
});
{% endif %}

{% if winrate_chart.labels | length > 0 %}
// Win Rate Chart
new Chart(document.getElementById('winrateChart'), {
  type: 'bar',
  data: {
    labels: {{ winrate_chart.labels | tojson }},
    datasets: [{
      label: 'Win Rate %',
      data: {{ winrate_chart.win_rates | tojson }},
      backgroundColor: {{ winrate_chart.colors | tojson }},
      borderRadius: 4,
    }],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(1)}% win rate` } },
    },
    scales: {
      x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
      y: {
        min: 0, max: 100,
        grid: { color: '#1e293b' },
        ticks: { color: '#94a3b8', callback: v => v + '%' },
      },
    },
  },
});
{% endif %}

{% if label_chart.labels | length > 0 %}
// Label Performance Chart
new Chart(document.getElementById('labelChart'), {
  type: 'bar',
  data: {
    labels: {{ label_chart.labels | tojson }},
    datasets: [
      {
        label: 'Win Rate %',
        data: {{ label_chart.win_rates | tojson }},
        backgroundColor: 'rgba(56,189,248,0.7)',
        borderRadius: 4,
        yAxisID: 'y',
      },
      {
        label: 'ROI %',
        data: {{ label_chart.roi_values | tojson }},
        backgroundColor: {{ label_chart.roi_colors | tojson }},
        borderRadius: 4,
        yAxisID: 'y2',
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: '#94a3b8', font: { size: 11 } } },
      tooltip: {
        callbacks: {
          label: ctx => ctx.datasetIndex === 0
            ? ` Win rate: ${ctx.parsed.y.toFixed(1)}%`
            : ` ROI: ${ctx.parsed.y > 0 ? '+' : ''}${ctx.parsed.y.toFixed(1)}%`
        }
      },
    },
    scales: {
      x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
      y: {
        type: 'linear', position: 'left',
        min: 0, max: 100,
        grid: { color: '#1e293b' },
        ticks: { color: '#38bdf8', callback: v => v + '%' },
      },
      y2: {
        type: 'linear', position: 'right',
        grid: { drawOnChartArea: false },
        ticks: { color: '#a78bfa', callback: v => (v >= 0 ? '+' : '') + v + '%' },
      },
    },
  },
});
{% endif %}

{% if calibration_chart.has_data %}
// Model Calibration Chart
new Chart(document.getElementById('calibrationChart'), {
  type: 'line',
  data: {
    labels: {{ calibration_chart.labels | tojson }},
    datasets: [
      {
        label: 'Actual Win %',
        data: {{ calibration_chart.actual | tojson }},
        borderColor: '#38bdf8',
        backgroundColor: 'rgba(56,189,248,0.1)',
        tension: 0.3,
        borderWidth: 2.5,
        pointRadius: 5,
      },
      {
        label: 'Perfect Calibration',
        data: {{ calibration_chart.perfect | tojson }},
        borderColor: '#334155',
        borderDash: [6, 3],
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: '#94a3b8', font: { size: 11 } } },
      tooltip: {
        callbacks: {
          label: ctx => ctx.datasetIndex === 0
            ? ` Actual: ${ctx.parsed.y !== null ? ctx.parsed.y.toFixed(1) + '%' : 'No data'}`
            : ` Perfect: ${ctx.parsed.y.toFixed(1)}%`
        }
      },
    },
    scales: {
      x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
      y: {
        min: 0, max: 100,
        grid: { color: '#1e293b' },
        ticks: { color: '#94a3b8', callback: v => v + '%' },
      },
    },
  },
});
{% endif %}

</script>
</body>
</html>"""


def build_dashboard_data(db):
    """Transform history_db.json into Chart.js-ready data structures."""
    lt = db.get("lifetime", {})
    rounds = db.get("rounds", [])
    sorted_rounds = sorted(rounds, key=lambda r: r["round_id"])

    strategy_chart = get_strategy_comparison(db)

    # Win rate per round
    winrate_labels = []
    winrate_values = []
    winrate_colors = []
    for rnd in sorted_rounds:
        rr = rnd.get("round_result", {})
        games = rnd.get("games", [])
        wins = rr.get("wins")
        losses = rr.get("losses")
        if wins is None:
            continue
        total = wins + (losses or 0)
        pct = round(wins / total * 100, 1) if total else 0
        winrate_labels.append(rnd["round_id"].replace("2026-", ""))
        winrate_values.append(pct)
        winrate_colors.append("rgba(74,222,128,0.7)" if pct >= 50 else "rgba(248,113,113,0.7)")

    # Label performance chart
    label_names = ["STRONG VALUE", "VALUE", "MARGINAL"]
    label_win_rates = []
    label_roi_values = []
    label_roi_colors = []
    has_label_data = False
    for label in label_names:
        data = lt.get("by_label", {}).get(label, {})
        bets = data.get("bets", 0)
        wins = data.get("wins", 0)
        staked = data.get("staked", 0.0)
        returned = data.get("returned", 0.0)
        win_rate = round(wins / bets * 100, 1) if bets else 0
        roi = round((returned - staked) / staked * 100, 1) if staked else 0
        label_win_rates.append(win_rate)
        label_roi_values.append(roi)
        label_roi_colors.append("rgba(74,222,128,0.7)" if roi >= 0 else "rgba(248,113,113,0.7)")
        if bets > 0:
            has_label_data = True

    # Model calibration
    cal_labels = []
    cal_actual = []
    cal_perfect = []
    has_calibration_data = False
    for i, (bucket, lo, hi) in enumerate(
        [("0-10%", 0, 10), ("10-20%", 10, 20), ("20-30%", 20, 30),
         ("30-40%", 30, 40), ("40-50%", 40, 50), ("50-60%", 50, 60),
         ("60-70%", 60, 70), ("70-80%", 70, 80), ("80-90%", 80, 90), ("90-100%", 90, 100)]
    ):
        cb = lt.get("model_calibration", [{}] * 10)
        entry = cb[i] if i < len(cb) else {}
        bets = entry.get("bets", 0)
        wins = entry.get("wins", 0)
        midpoint = (lo + hi) / 2
        cal_labels.append(bucket)
        cal_perfect.append(midpoint)
        if bets >= 3:
            cal_actual.append(round(wins / bets * 100, 1))
            has_calibration_data = True
        else:
            cal_actual.append(None)

    # Round history table + per-game detail
    FLAT_STAKE = 10.0
    round_rows = []
    for rnd in reversed(sorted_rounds):
        rr = rnd.get("round_result", {})
        games = rnd.get("games", [])
        resolved = rr.get("resolved_at") is not None or rr.get("wins") is not None

        n_games = len(games) if games else (
            (rr.get("wins", 0) or 0) + (rr.get("losses", 0) or 0)
        )
        wins = rr.get("wins")
        losses = rr.get("losses")
        staked = rr.get("total_staked") or 0.0
        returned = rr.get("total_returned") or 0.0
        profit = rr.get("net_profit") or 0.0
        roi = round(profit / staked * 100, 1) if staked else 0
        bank_end = rr.get("bankroll_end")
        bank_start = rnd.get("bankroll_start")

        # Flat $10 per game calculation
        flat_staked = 0.0
        flat_returned = 0.0
        game_rows = []
        for g in games:
            rec = g.get("recommended_bet") or {}
            odds = rec.get("odds") or 0
            won = g.get("won")
            result = g.get("result")
            pending = won is None and result is None

            if not pending and odds:
                flat_staked += FLAT_STAKE
                flat_ret = round(odds * FLAT_STAKE, 2) if won else 0.0
                flat_returned += flat_ret
            else:
                flat_ret = None

            game_rows.append({
                "home_team": g.get("home_team", ""),
                "away_team": g.get("away_team", ""),
                "description": rec.get("description", "—"),
                "bet_type": rec.get("bet_type", ""),
                "odds": odds,
                "label": rec.get("label", ""),
                "model_prob": rec.get("model_prob"),
                "edge": rec.get("edge"),
                "won": won,
                "pending": pending,
                "flat_stake": FLAT_STAKE if not pending else None,
                "flat_return": flat_ret,
                "flat_profit": round(flat_ret - FLAT_STAKE, 2) if flat_ret is not None else None,
            })

        flat_profit = round(flat_returned - flat_staked, 2) if flat_staked else None
        flat_roi = round(flat_profit / flat_staked * 100, 1) if flat_staked else None

        round_rows.append({
            "round_id": rnd["round_id"],
            "bets": n_games,
            "wins": wins or 0,
            "losses": losses or 0,
            "staked": staked,
            "returned": returned,
            "profit": profit,
            "roi_pct": roi,
            "bankroll_start": bank_start or 0,
            "bankroll_end": bank_end,
            "resolved": resolved,
            "game_rows": game_rows,
            "flat_staked": flat_staked,
            "flat_returned": flat_returned,
            "flat_profit": flat_profit,
            "flat_roi": flat_roi,
        })

    # Current round picks — most recent round (may be pending)
    current_picks = round_rows[0] if round_rows else None

    # Current bankroll
    from nrl_tracker import get_current_bankroll
    current_bankroll = get_current_bankroll(db)

    recommendations = get_filter_recommendations(db)

    return {
        "lifetime": lt,
        "current_bankroll": current_bankroll,
        "strategy_chart": strategy_chart,
        "winrate_chart": {
            "labels": winrate_labels,
            "win_rates": winrate_values,
            "colors": winrate_colors,
        },
        "label_chart": {
            "labels": label_names,
            "win_rates": label_win_rates,
            "roi_values": label_roi_values,
            "roi_colors": label_roi_colors,
            "has_data": has_label_data,
        },
        "calibration_chart": {
            "labels": cal_labels,
            "actual": cal_actual,
            "perfect": cal_perfect,
            "has_data": has_calibration_data,
        },
        "round_rows": round_rows,
        "current_picks": current_picks,
        "recommendations": recommendations,
        "user_bets": load_user_bets(),
        "user_bets_summary": user_bets_summary(load_user_bets()),
        "generated_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
    }


def render_dashboard(data):
    template = Template(DASHBOARD_TEMPLATE)
    return template.render(**data)


def main():
    db = load_db()
    data = build_dashboard_data(db)
    html = render_dashboard(data)
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    rounds = len(db.get("rounds", []))
    print(f"Dashboard written to {DASHBOARD_FILE} ({rounds} round(s) in history)")


if __name__ == "__main__":
    main()
