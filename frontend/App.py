import os
import requests
import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BACKEND_BASE_URL = os.getenv('BACKEND_BASE_URL')
if BACKEND_BASE_URL:
    BACKEND = BACKEND_BASE_URL.rstrip('/')
else:
    BACKEND = f"http://{os.getenv('BACKEND_HOST', '127.0.0.1')}:{os.getenv('BACKEND_PORT', '8000')}"

st.set_page_config(page_title="LLM UI", layout="wide")

file = st.file_uploader("Файл", type=["csv", "xlsx"], label_visibility="collapsed")
if st.button("Загрузить") and file is not None:
    try:
        files = {"file": (file.name, file.getvalue())}
        resp = requests.post(f"{BACKEND}/api/v1/ingest/upload", files=files, data={}, timeout=180)
        if resp.ok:
            st.success("Файл загружен")
            try:
                out = resp.json()
                if isinstance(out, dict) and out.get("table"):
                    st.session_state["current_table"] = out["table"]
                    st.rerun()
            except Exception:
                pass
        else:
            st.error(resp.text)
    except Exception as e:
        st.error(str(e))

q = st.text_input("Запрос", placeholder="Введите NL-запрос или SELECT/WITH и нажмите Enter", label_visibility="collapsed")

rows = []
if q:
    try:
        upper = q.strip().upper()
        if upper.startswith("SELECT") or upper.startswith("WITH"):
            r = requests.post(f"{BACKEND}/api/v1/query/sql", json={"sql": q}, timeout=180)
            if r.ok:
                rows = r.json().get("rows", [])
            else:
                st.error(r.text)
        else:
            r = requests.post(
                f"{BACKEND}/api/v1/llm/execute",
                json={"task": q, "table_hint": st.session_state.get("current_table")},
                timeout=240,
            )
            if r.ok:
                data = r.json()
                st.code(data.get("raw", ""))
                results = data.get("results", [])
                for res in results:
                    if res.get("type") == "read":
                        rows = res.get("rows", [])
                        break
            else:
                st.error(r.text)
    except Exception as e:
        st.error(str(e))

display_rows = rows
if not display_rows:
    try:
        t = requests.get(f"{BACKEND}/api/v1/schema/tables", timeout=30)
        tables = t.json().get("tables", []) if t.ok else []
        current = st.session_state.get("current_table")
        if current in tables:
            table_to_show = current
        else:
            table_to_show = tables[0] if tables else None
        if table_to_show:
            s = requests.get(f"{BACKEND}/api/v1/schema/tables/{table_to_show}/sample", params={"limit": 50}, timeout=30)
            if s.ok:
                display_rows = s.json().get("rows", [])
    except Exception:
        pass

st.dataframe(display_rows or [], width="stretch")

try:
    wants_plot = False
    if q:
        lower = q.lower()
        for kw in ["график", "диаграм", "построй", "plot", "chart", "hist", "гист", "распределение", "top", "частоты"]:
            if kw in lower:
                wants_plot = True
                break
    if wants_plot:
        pr = requests.post(
            f"{BACKEND}/api/v1/plots/nlplot",
            json={"task": q, "table_hint": st.session_state.get("current_table")},
            timeout=180,
        )
        if pr.ok:
            payload = pr.json()
            spec = payload.get("spec", {})
            chart_type = (spec.get("type") or "").lower()
            rows_plot = payload.get("rows", [])
            if rows_plot:
                df = pd.DataFrame(rows_plot)
                # Только числовые графики: линейная оценка плотности (KDE)
                if "x" in df.columns:
                    x_series = pd.to_numeric(df["x"], errors="coerce")
                    if x_series.notna().any():
                        df_num = pd.DataFrame({"x": x_series.dropna()})
                        kde = (
                            alt.Chart(df_num)
                            .transform_density('x', as_=['x', 'density'])
                            .mark_line()
                            .encode(x='x:Q', y='density:Q')
                        )
                        st.altair_chart(kde, use_container_width=True)
        else:
            st.info("Не удалось построить график: " + pr.text)
except Exception as e:
    st.info("График не построен: " + str(e))

try:
    df_disp = pd.DataFrame(display_rows or [])
    if not df_disp.empty:
        num_cols = [c for c in df_disp.columns if pd.api.types.is_numeric_dtype(df_disp[c])]
        # Попытка привести строковые к числу, если нет явных числовых
        if not num_cols:
            for c in df_disp.columns:
                if df_disp[c].dtype == object:
                    coerced = pd.to_numeric(df_disp[c], errors='coerce')
                    if coerced.notna().sum() >= max(5, int(len(coerced) * 0.2)):
                        df_disp[c] = coerced
                        num_cols.append(c)
                        break
        if num_cols:
            nc = num_cols[0]
            base_num = df_disp.dropna(subset=[nc])
            kde = (
                alt.Chart(base_num)
                .transform_density(nc, as_=[str(nc), 'density'])
                .mark_line()
                .encode(x=f"{nc}:Q", y='density:Q')
            )
            st.altair_chart(kde, use_container_width=True)

        
except Exception:
    pass
