import streamlit as st
import pandas as pd
import altair as alt
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from nba_api.stats.static import players
from nba_api.stats.endpoints import (
    playergamelog, commonplayerinfo, leaguedashteamstats
)

# --- Config ---
st.set_page_config(page_title="NBA Player Stats", layout="centered")
st.title("🏀 NBA Player Last 10 Games Stats")

prop_line1, prop_line2, prop_line3 = 10, 15, 20
team_abbrs = sorted([
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
    "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
    "POR", "SAC", "SAS", "TOR", "UTA", "WAS"
])
opponent_abbr = st.selectbox("Select Team:", team_abbrs, index=team_abbrs.index("BOS"))

# --- Cached Utilities ---
@st.cache_data(show_spinner=False)
def get_season_string():
    today = datetime.today()
    if today.month >= 10:
        start_year = today.year
        end_year = today.year + 1
    else:
        start_year = today.year - 1
        end_year = today.year
    return f"{start_year}-{str(end_year)[-2:]}"

@st.cache_data(show_spinner=False)
def get_player_id(name):
    matches = players.find_players_by_full_name(name)
    return matches[0]["id"] if matches else None

@st.cache_data(show_spinner=False)
def get_player_position(player_id):
    info = commonplayerinfo.CommonPlayerInfo(player_id=player_id).get_data_frames()[0]
    return info.loc[0, "POSITION"]

@st.cache_data(show_spinner=False)
def get_last_10_games(player_id):
    current_season = get_season_string()
    previous_season = f"{int(current_season[:4]) - 1}-{current_season[:2]}"
    logs_current = playergamelog.PlayerGameLog(player_id=player_id, season=current_season).get_data_frames()[0]
    logs_previous = playergamelog.PlayerGameLog(player_id=player_id, season=previous_season).get_data_frames()[0]
    frames = [df for df in [logs_current, logs_previous] if not df.empty and not df.isna().all(axis=1).all()]
    all_logs = pd.concat(frames) if frames else pd.DataFrame()
    all_logs["GAME_DATE"] = pd.to_datetime(all_logs["GAME_DATE"])
    all_logs = all_logs.sort_values("GAME_DATE", ascending=False).head(10)
    if all(col in all_logs.columns for col in ["PTS", "REB", "AST", "MIN"]):
        all_logs["FPPM"] = (all_logs["PTS"] + 1.2 * all_logs["REB"] + 1.5 * all_logs["AST"]) / all_logs["MIN"]
        all_logs["FPPM"] = all_logs["FPPM"].fillna(0)
    return all_logs[["GAME_DATE", "MATCHUP", "PTS", "REB", "AST", "MIN", "FPPM"]]

@st.cache_data(show_spinner=False)
def get_dvp_table():
    position_groups = ['G', 'F', 'C']
    dfs = []
    for pos in position_groups:
        stats = leaguedashteamstats.LeagueDashTeamStats(
            measure_type_detailed_defense='Opponent',
            per_mode_detailed='PerGame',
            player_position_abbreviation_nullable=pos,
            season='2024-25'
        )
        df = stats.get_data_frames()[0]
        df['Position'] = pos
        dfs.append(df)
    df_all = pd.concat(dfs)
    df_pivot = df_all.pivot(index='TEAM_NAME', columns='Position', values='OPP_PTS').reset_index()
    df_pivot.columns.name = None
    df_pivot.rename(columns={
        'G': 'Guard Pts Allowed',
        'F': 'Forward Pts Allowed',
        'C': 'Center Pts Allowed'
    }, inplace=True)
    return df_pivot

def matchup_multiplier(pts_allowed, avg, std):
    z = (pts_allowed - avg) / std
    if z >= 1.0: return 1.25
    elif z >= 0.5: return 1.15
    elif z <= -1.0: return 0.85
    elif z <= -0.5: return 0.75
    else: return 1.00

def get_matchup_score(row, slot, guard_avg, guard_std, forward_avg, forward_std, center_avg, center_std):
    if slot in ['PG', 'SG']:
        return matchup_multiplier(row['Guard Pts Allowed'], guard_avg, guard_std)
    elif slot in ['SF', 'PF']:
        return matchup_multiplier(row['Forward Pts Allowed'], forward_avg, forward_std)
    elif slot == 'C':
        return matchup_multiplier(row['Center Pts Allowed'], center_avg, center_std)
    else:
        return 1.00

def simplify_slot(position):
    if "G" in position: return "PG"
    elif "F" in position: return "SF"
    elif "C" in position: return "C"
    else: return "PG"

def extract_name(raw):
    name_only = re.sub(r"\s{2,}.*", "", raw.strip())
    name_only = re.sub(r"\b(Jr|Sr|III|IV)\.\b", r"\1", name_only)
    name_only = re.sub(r"\b(Jr|Sr|III|IV)\.$", r"\1", name_only)
    return name_only

def stat_chart(df, stat, line_value):
    df["OverLine"] = df[stat] > line_value
    max_val = df[stat].max()
    y_max = max_val * 1.2

    bars = alt.Chart(df).mark_bar(size=20).encode(
    x=alt.X("GAME_DATE:T", title="Game Date", axis=alt.Axis(grid=False)),
    y=alt.Y(stat, title=stat, scale=alt.Scale(domain=[0, y_max], nice=False), axis=alt.Axis(grid=False)),
    color=alt.condition(
        alt.datum.OverLine,
        alt.value("green"),
        alt.value("red")
    ),
    tooltip=["GAME_DATE", stat]
)

    line = alt.Chart(pd.DataFrame({stat: [line_value]})).mark_rule(
        color="red", strokeDash=[4, 4]
    ).encode(y=stat)

    labels = alt.Chart(df).mark_text(
        dy=-15,
        color="white",
        fontSize=12
    ).encode(
        x=alt.X("GAME_DATE:T"),
        y=alt.Y(stat),
        text=alt.Text(stat, format=".0f")
    )

    chart = (bars + line + labels).properties(
        height=250,
        title=f"{stat} over Last 10 Games",
        padding={"top": 10, "left": 5, "right": 5, "bottom": 0}
    ).configure_axis(
        labelColor="white",
        titleColor="white"
    ).configure_axisX(
        grid=False,
        labelFontSize=10,
        labelPadding=0,
        domain=False,
        ticks=False
    ).configure_axisY(
        grid=False,
        domain=False,
        ticks=False
    ).configure_title(
        color="white"
    ).configure_view(
        fill="black"
    ).configure(
        background="black"
    )

    return chart



# --- Main Analysis ---
def run_analysis(opponent_abbr):
    with st.spinner("⏳ Gathering starters and analyzing prop lines..."):
        # --- Scrape RotoGrinders ---
        url = "https://rotogrinders.com/lineups/nba"
        headers = {"User-Agent": "Mozilla/5.0"}
        soup = BeautifulSoup(requests.get(url, headers=headers).text, "html.parser")
        lineup_cards = soup.find_all("div", class_="lineup-card")
        team_nameplates = soup.find_all("div", class_="team-nameplate")

        filtered_starters = []

        for card, nameplate in zip(lineup_cards, team_nameplates):
            team_span = nameplate.find("span", class_="team-nameplate-title")
            team_abbr = team_span.get("data-abbr", "").strip().upper() if team_span else None

            if team_abbr != opponent_abbr.upper():
                continue

            body_div = card.find("div", class_=lambda c: c and "lineup-card-body" in c)
            if not body_div:
                continue

            players_div = body_div.find("div", class_="lineup-card-players")
            if not players_div:
                continue

            labels = players_div.find_all("span", class_=["bold small", "bold small lineup-card-bench"])
            for label in labels:
                label_text = label.text.strip().lower()
                ul = label.find_next_sibling("ul")
                if not ul:
                    continue
                player_names = [li.text.strip() for li in ul.find_all("li")]
                if "starters" in label_text:
                    filtered_starters = player_names
                    break

        starter_names = [extract_name(name) for name in filtered_starters if isinstance(name, str) and name.strip()]
        starter_names = pd.Series(starter_names).dropna().unique().tolist()

        if not starter_names:
            st.warning(f"No starters found for {opponent_abbr}. They may not be playing tonight.")
            return pd.DataFrame(), pd.DataFrame()

        dvp_table = get_dvp_table()
        guard_avg = dvp_table["Guard Pts Allowed"].mean()
        guard_std = dvp_table["Guard Pts Allowed"].std()
        forward_avg = dvp_table["Forward Pts Allowed"].mean()
        forward_std = dvp_table["Forward Pts Allowed"].std()
        center_avg = dvp_table["Center Pts Allowed"].mean()
        center_std = dvp_table["Center Pts Allowed"].std()

        # --- DvP Setup ---
        dvp_table = get_dvp_table()
        guard_avg = dvp_table["Guard Pts Allowed"].mean()
        guard_std = dvp_table["Guard Pts Allowed"].std()
        forward_avg = dvp_table["Forward Pts Allowed"].mean()
        forward_std = dvp_table["Forward Pts Allowed"].std()
        center_avg = dvp_table["Center Pts Allowed"].mean()
        center_std = dvp_table["Center Pts Allowed"].std()

        results = []
        chart_data = []

        for name in starter_names:
            player_id = get_player_id(name)
            if not player_id:
                continue
            try:
                df = get_last_10_games(player_id)
                df = df[df["GAME_DATE"] > pd.Timestamp("2025-10-01")]
                if df.empty or "PTS" not in df.columns:
                    continue

                avg_fppm = df["FPPM"].mean() if "FPPM" in df.columns else 0
                position = get_player_position(player_id)
                slot = simplify_slot(position)
                matchup_str = df.iloc[0]["MATCHUP"]
                opponent = matchup_str.split("vs")[-1].strip() if "vs" in matchup_str else matchup_str.split("@")[-1].strip()
                matchup_row = dvp_table[dvp_table["TEAM_NAME"].str.contains(opponent, case=False, na=False)]
                multiplier = get_matchup_score(matchup_row.iloc[0], slot, guard_avg, guard_std, forward_avg, forward_std, center_avg, center_std) if not matchup_row.empty else 1.00

                for line in [prop_line1, prop_line2, prop_line3]:
                    try:
                        pct = (df["PTS"] > line).sum() / len(df) * 100
                        score = (pct * 0.6) + (multiplier * 100 * 0.2) + (avg_fppm * 100 * 0.2)

                        results.append({
                            "Player": name,
                            "Line": line,
                            "PctOver": round(pct, 1),
                            "FPPM": round(avg_fppm, 2),
                            "MatchupMultiplier": round(multiplier, 2),
                            "Score": round(score, 2)
                        })

                        chart_data.append({
                            "Player": name,
                            "Line": line,
                            "PctOver": round(pct, 1)
                        })
                    except Exception as e:
                        print(f"Error processing {name} at line {line}: {e}")
                        continue

            except Exception as e:
                print(f"Error for {name}: {e}")
                continue

        return pd.DataFrame(results), pd.DataFrame(chart_data)
    
if st.button("Run Analysis"):
    df_results, df_chart = run_analysis(opponent_abbr)

    if df_results.empty:
        st.warning("No valid player data found.")
    else:
        st.subheader("Top Starters by Composite Score (All Prop Lines)")
        for line in [prop_line1, prop_line2, prop_line3]:
            top_10 = df_results[df_results["Line"] == line].sort_values("Score", ascending=False).head(10)
            st.markdown(f"### 🔹 {line} Points")
            st.dataframe(top_10[["Player", "PctOver", "FPPM", "MatchupMultiplier", "Score"]])

        if not df_chart.empty:
            
            st.subheader("📊 Player Points Over Last 10 Games")

            for name in df_results["Player"].unique():
                player_id = get_player_id(name)
                if not player_id:
                    continue
                df = get_last_10_games(player_id)
                df = df[df["GAME_DATE"] > pd.Timestamp("2025-10-01")]
                if df.empty or "PTS" not in df.columns:
                    continue

                df["GAME_DATE"] = df["GAME_DATE"].dt.date
                st.markdown(f"### {name}")
                st.altair_chart(stat_chart(df, "PTS", prop_line1), use_container_width=True)
