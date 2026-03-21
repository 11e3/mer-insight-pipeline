"""
Prediction Accuracy Dashboard

Usage:
    streamlit run src/dashboard/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import os
if not os.environ.get("DATABASE_URL") and hasattr(st, "secrets"):
    try:
        os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError):
        pass

from src.dashboard.queries import (
    load_prediction_summary,
    load_predictions_for_topics,
    load_monthly_trends,
    load_all_predictions,
    load_leaderboard,
)
from src.dashboard.topics import classify_topic

st.set_page_config(page_title="Prediction Tracker", layout="wide")

# ─── 1. Summary ──────────────────────────────────────────────────────────────

st.title("Prediction Tracker")
st.caption("Auto-verified predictions from financial influencers (News DB + Haiku)")

summary = load_prediction_summary()

col1, col2, col3 = st.columns(3)
verified = summary["correct"] + summary["incorrect"]
accuracy = summary["correct"] / verified * 100 if verified > 0 else 0

col1.metric("Accuracy", f"{accuracy:.1f}%", help=f"Based on {verified:,} verified predictions")
col2.metric("Verified", f"{verified:,}", help=f"CORRECT {summary['correct']:,} + INCORRECT {summary['incorrect']:,}")
col3.metric("Total", f"{summary['total']:,}")

col4, col5, col6, col7 = st.columns(4)
col4.metric("CORRECT", f"{summary['correct']:,}")
col5.metric("INCORRECT", f"{summary['incorrect']:,}")
col6.metric("Pending", f"{summary.get('verifiable_pending', 0):,}", help="Verifiable but not yet judged")
col7.metric("Future", f"{summary.get('future', 0):,}", help="Awaiting expected_date")

pie_data = pd.DataFrame({
    "status": ["CORRECT", "INCORRECT", "Pending", "Unverifiable", "Future"],
    "count": [
        summary["correct"],
        summary["incorrect"],
        summary.get("verifiable_pending", 0),
        summary.get("unverifiable", 0),
        summary.get("future", 0),
    ],
})
pie_data = pie_data[pie_data["count"] > 0]

if not pie_data.empty:
    fig_pie = px.pie(
        pie_data, values="count", names="status",
        color="status",
        color_discrete_map={
            "CORRECT": "#2ecc71", "INCORRECT": "#e74c3c",
            "Pending": "#3498db", "Unverifiable": "#95a5a6", "Future": "#f39c12",
        },
        title="Prediction Status",
    )
    fig_pie.update_traces(textinfo="percent+value")
    st.plotly_chart(fig_pie, use_container_width=True)

st.divider()

# ─── 2. Source Leaderboard ───────────────────────────────────────────────────

st.subheader("Source Leaderboard")

leaderboard = load_leaderboard()

if leaderboard:
    df_lb = pd.DataFrame(leaderboard)
    df_lb["accuracy"] = df_lb.apply(
        lambda r: f"{r['correct'] / r['verified'] * 100:.1f}%" if r["verified"] > 0 else "—", axis=1
    )
    df_lb = df_lb.rename(columns={
        "source": "Source", "total": "Total", "verified": "Verified",
        "correct": "Correct", "incorrect": "Incorrect",
        "pending": "Pending", "future": "Future", "accuracy": "Accuracy",
    })
    st.dataframe(
        df_lb[["Source", "Total", "Verified", "Correct", "Incorrect", "Accuracy", "Pending", "Future"]],
        use_container_width=True, hide_index=True,
    )

    # 바 차트
    if len(df_lb) > 1:
        fig_lb = px.bar(
            df_lb, x="Source", y=["Correct", "Incorrect"],
            barmode="group",
            color_discrete_map={"Correct": "#2ecc71", "Incorrect": "#e74c3c"},
            title="Verified Predictions by Source",
        )
        st.plotly_chart(fig_lb, use_container_width=True)

st.divider()

# ─── 3. Accuracy by Topic ───────────────────────────────────────────────────

st.subheader("Accuracy by Topic")

preds = load_predictions_for_topics()

if preds:
    for p in preds:
        if not p.get("topic"):
            p["topic"] = classify_topic(p["prediction_text"])
        if p["is_correct"] is True:
            p["verdict"] = "CORRECT"
        elif p["is_correct"] is False:
            p["verdict"] = "INCORRECT"
        else:
            p["verdict"] = "PENDING"

    df_topic = pd.DataFrame(preds)

    df_verified = df_topic[df_topic["verdict"] != "PENDING"]
    if not df_verified.empty:
        topic_stats = df_verified.groupby(["topic", "verdict"]).size().reset_index(name="count")
        fig_bar = px.bar(
            topic_stats, x="topic", y="count", color="verdict",
            barmode="group",
            color_discrete_map={"CORRECT": "#2ecc71", "INCORRECT": "#e74c3c"},
            title="Verification Results by Topic",
            labels={"topic": "Topic", "count": "Count", "verdict": "Verdict"},
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        topic_acc = df_verified.groupby("topic").apply(
            lambda g: pd.Series({
                "Verified": len(g),
                "CORRECT": (g["verdict"] == "CORRECT").sum(),
                "Accuracy": f"{(g['verdict'] == 'CORRECT').mean() * 100:.1f}%",
            }),
            include_groups=False,
        ).reset_index()
        topic_acc = topic_acc.sort_values("CORRECT", ascending=False)
        st.dataframe(topic_acc, use_container_width=True, hide_index=True)
    else:
        st.info("No verified predictions yet.")
else:
    st.info("No prediction data.")

st.divider()

# ─── 3. Monthly Trends ──────────────────────────────────────────────────────

st.subheader("Monthly Trends")

monthly = load_monthly_trends()

if monthly:
    df_m = pd.DataFrame(monthly)
    df_m["accuracy"] = df_m.apply(
        lambda r: r["correct"] / r["verified"] * 100 if r["verified"] > 0 else None, axis=1
    )

    fig_trend = go.Figure()

    fig_trend.add_trace(go.Bar(
        x=df_m["month"], y=df_m["total"],
        name="Predictions", marker_color="#3498db", opacity=0.7,
        hovertemplate="Month: %{x}<br>Predictions: %{y}<extra></extra>",
    ))

    fig_trend.add_trace(go.Scatter(
        x=df_m["month"], y=df_m["accuracy"],
        name="Accuracy (%)", yaxis="y2",
        mode="lines+markers", marker_color="#e74c3c",
        hovertemplate="Month: %{x}<br>Accuracy: %{y:.1f}%<extra></extra>",
    ))

    fig_trend.update_layout(
        title="Monthly Predictions & Accuracy",
        xaxis=dict(title="Month", type="category"),
        yaxis=dict(title="Predictions", side="left"),
        yaxis2=dict(title="Accuracy (%)", side="right", overlaying="y", range=[0, 100]),
        legend=dict(x=0.01, y=0.99),
    )

    st.plotly_chart(fig_trend, use_container_width=True)
else:
    st.info("No monthly data.")

st.divider()

# ─── 4. All Predictions ─────────────────────────────────────────────────────

st.subheader("All Predictions")

all_preds = load_all_predictions()

if all_preds:
    for p in all_preds:
        if not p.get("topic"):
            p["topic"] = classify_topic(p["prediction_text"])

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    verdict_options = ["All", "CORRECT", "INCORRECT", "PENDING", "Unverifiable"]
    verdict_filter = col_f1.selectbox("Verdict", verdict_options)
    topics = sorted({p.get("topic", "Other") for p in all_preds})
    topic_filter = col_f2.selectbox("Topic", ["All"] + topics)
    direction_filter = col_f3.selectbox("Direction", ["All", "up", "down", "neutral"])
    search_query = col_f4.text_input("Search", placeholder="keyword")

    filtered = all_preds
    if verdict_filter == "CORRECT":
        filtered = [p for p in filtered if p["is_correct"] is True]
    elif verdict_filter == "INCORRECT":
        filtered = [p for p in filtered if p["is_correct"] is False]
    elif verdict_filter == "PENDING":
        filtered = [p for p in filtered if p["is_correct"] is None and p.get("is_verifiable") is not False]
    elif verdict_filter == "Unverifiable":
        filtered = [p for p in filtered if p.get("is_verifiable") is False]
    if topic_filter != "All":
        filtered = [p for p in filtered if p.get("topic", "Other") == topic_filter]
    if direction_filter != "All":
        filtered = [p for p in filtered if p["predicted_direction"] == direction_filter]
    if search_query:
        q = search_query.lower()
        filtered = [p for p in filtered if q in (p["prediction_text"] or "").lower()
                    or q in (p["target_asset"] or "").lower()]

    PAGE_SIZE = 20
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)

    if "pred_page" not in st.session_state:
        st.session_state.pred_page = 1
    if st.session_state.pred_page > total_pages:
        st.session_state.pred_page = 1

    page = st.session_state.pred_page

    st.caption(f"{len(filtered):,} shown ({len(all_preds):,} total) — page {page}/{total_pages}")

    nav_cols = st.columns([1, 1, 1, 1, 1, 3])
    if nav_cols[0].button("First", disabled=page <= 1):
        st.session_state.pred_page = 1
        st.rerun()
    if nav_cols[1].button("Prev", disabled=page <= 1):
        st.session_state.pred_page = page - 1
        st.rerun()
    if nav_cols[2].button("Next", disabled=page >= total_pages):
        st.session_state.pred_page = page + 1
        st.rerun()
    if nav_cols[3].button("Last", disabled=page >= total_pages):
        st.session_state.pred_page = total_pages
        st.rerun()
    jump = nav_cols[4].selectbox(
        "Go to", range(1, total_pages + 1), index=page - 1, label_visibility="collapsed",
    )
    if jump != page:
        st.session_state.pred_page = jump
        st.rerun()

    page_items = filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    for r in page_items:
        if r["is_correct"] is True:
            icon, verdict = "O", "CORRECT"
        elif r["is_correct"] is False:
            icon, verdict = "X", "INCORRECT"
        elif r.get("is_verifiable") is False:
            icon, verdict = "-", "UNVERIFIABLE"
        else:
            icon, verdict = "?", "PENDING"

        pred_date = r["prediction_date"].strftime("%Y-%m-%d") if r["prediction_date"] else ""
        asset = r["target_asset"] or ""
        source = r.get("source_name", "")
        short = (r["prediction_text"] or "")[:50]
        label = f"{icon} [{pred_date}] {source} | {asset} — {short}"

        with st.expander(label):
            pred_text = r["prediction_text"] or ""
            if r.get("post_url"):
                st.markdown(f"{pred_text}\n\n[Source]({r['post_url']})")
            else:
                st.write(pred_text)

            parts = [f"**Verdict: {verdict}**", f"Direction: `{r['predicted_direction']}`"]
            if r.get("source_name"):
                parts.append(f"Source: `{r['source_name']}`")
            if r.get("verification_date"):
                parts.append(f"Verified: {r['verification_date']}")
            if r.get("expected_date"):
                parts.append(f"Due: {r['expected_date']}")
            st.markdown(" | ".join(parts))

            outcome = r.get("actual_outcome") or ""
            if outcome:
                st.markdown(f"**Evidence:** {outcome}")
else:
    st.info("No prediction data.")

# ─── Disclaimer ──────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Predictions are auto-extracted by Claude from financial influencer blogs "
    "and auto-verified against a news headline database (Haiku). "
    "This is not financial advice. AI extraction and verification may contain errors."
)
