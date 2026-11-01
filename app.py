#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Хувийн санхүүгийн апп v3.0 — ажиллуулах: python -m streamlit run app.py"""
from __future__ import annotations
import sqlite3, hashlib, re, io
from pathlib import Path
from datetime import date
from typing import Optional
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from dateutil.relativedelta import relativedelta
 
DB_PATH = Path("finance.db")
VERSION = "3.0"
GOAL_CAT = "Хадгаламж / Зорилго"
CATEGORY_RULES = [
    (r"цалин|salary|wage|цалингийн", "Цалин"),
    (r"хүү\s*олгов|interest\s*income|ногдол", "Хүү / Ногдол ашиг"),
    (r"нэмэлт\s*орлого|bonus|урамшуулал|шагнал", "Нэмэлт орлого"),
    (r"nomin|миний\s*дэлгүүр|spar|спар|supermarket|хүнс|food|хоол|ресторан|кафе|coffee|kfc|pizza|delivery", "Хоол хүнс"),
    (r"такси|taxi|uber|убер|тээвэр|автобус|bus|шатахуун|petrol|diesel|parking|зогсоол", "Тээвэр"),
    (r"түрээс|rent|орон\s*сууц|байрны|хотхон|apartment", "Орон сууц / Түрээс"),
    (r"цахилгаан|дулаан|интернет|мобайл|unitel|mobicom|skytel|utility|коммунал", "Коммунал"),
    (r"эмнэлэг|pharmacy|эмийн\s*сан|эрүүл|hospital|clinic|шүд", "Эрүүл мэнд"),
    (r"сургууль|сургалт|ном|education|курс", "Боловсрол"),
    (r"кино|театр|зугаа|club|game|тоглоом|netflix|spotify", "Зугаа цэнгэл"),
    (r"хувцас|clothing|шоп|mall|дэлгүүр|fashion", "Хувцас"),
    (r"зээл|loan|interest\s*payment|зээлийн", "Зээл / Хүү"),
    (r"даатгал|insurance", "Даатгал"),
    (r"хадгаламж|savings|deposit|шилжүүлэг|transfer", GOAL_CAT),
]
EXPENSE_CATS = ["Хоол хүнс", "Тээвэр", "Орон сууц / Түрээс", "Коммунал", "Эрүүл мэнд", "Боловсрол",
                "Зугаа цэнгэл", "Хувцас", "Зээл / Хүү", "Даатгал", GOAL_CAT, "Бусад зарлага"]
GOAL_KINDS = ["Байр", "Машин", "Бусад"]
 
 
# ---------------- DB ----------------
def get_conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c
 
 
def init_db():
    c = get_conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, description TEXT,
        amount REAL NOT NULL, category TEXT, account TEXT DEFAULT 'Үндсэн', type TEXT CHECK(type IN ('income','expense')),
        source_file TEXT, tx_hash TEXT UNIQUE, created_at TEXT DEFAULT (datetime('now','localtime')));
    CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY AUTOINCREMENT, year_month TEXT NOT NULL, category TEXT NOT NULL,
        amount REAL NOT NULL, UNIQUE(year_month, category));
    CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, target_amount REAL NOT NULL,
        current_amount REAL DEFAULT 0, target_date TEXT, monthly_contribution REAL DEFAULT 0, priority INTEGER DEFAULT 1,
        notes TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
    CREATE TABLE IF NOT EXISTS category_rules(id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL UNIQUE, category TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
    """)
    # Хуучин өгөгдлийг алдалгүй шинэ багана нэмэх (migration)
    have = {r["name"] for r in c.execute("PRAGMA table_info(goals)")}
    for col, ddl in [("kind", "TEXT DEFAULT 'Бусад'"), ("down_pct", "REAL DEFAULT 100"), ("other_sources", "REAL DEFAULT 0"),
                     ("loan_rate", "REAL DEFAULT 0"), ("loan_years", "REAL DEFAULT 0")]:
        if col not in have:
            c.execute(f"ALTER TABLE goals ADD COLUMN {col} {ddl}")
    c.commit(); c.close()
 
 
def money(x): return f"{x:,.0f} ₮"
def make_hash(d, a, desc, n=0):
    raw = f"{d}|{a:.2f}|{str(desc).strip().lower()}" + (f"|{n}" if n else "")  # n=0 → хуучин хэштэй ижил
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
 
 
def parse_amount(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)): return None
    s = str(v).strip().replace("₮", "").replace("MNT", "").replace(" ", "").replace("\u00a0", "")
    if not s or s in ("-", "—"): return None
    if re.search(r"\d+\.\d{3},\d+$", s): s = s.replace(".", "").replace(",", ".")
    elif re.search(r"\d+,\d{3}\.\d+$", s): s = s.replace(",", "")
    else: s = s.replace(",", "")  # 1,234 → 1234
    s = re.sub(r"[^\d.\-]", "", s)
    try: return float(s)
    except ValueError: return None
 
 
def parse_dates(sr):
    for kw in ({"format": "%Y-%m-%d"}, {"dayfirst": True}, {}):
        dt = pd.to_datetime(sr, errors="coerce", **kw)
        if dt.notna().mean() > 0.6: return dt
    return dt
 
 
def auto_categorize(desc, is_income, conn=None):
    d = str(desc or "").lower()
    try:
        if conn:
            for r in conn.execute("SELECT keyword, category FROM category_rules ORDER BY length(keyword) DESC"):
                if r["keyword"].lower() in d: return r["category"]
    except Exception: pass
    for p, cat in CATEGORY_RULES:
        if re.search(p, d, re.I):
            if is_income and cat in EXPENSE_CATS and cat != GOAL_CAT: continue
            return cat
    return "Бусад орлого" if is_income else "Бусад зарлага"
 
 
# ---------------- 1. Импорт ----------------
def import_df(df, account="Үндсэн", source="upload"):
    if df is None or df.empty: return 0, 0
    df = df.copy(); df.columns = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in df.columns]
    find = lambda cands: next((c for c in cands if c in df.columns), None)
    dcol = find(["date", "огноо", "transaction date", "гүйлгээний огноо", "val date"]) or df.columns[0]
    dbt = find(["debit", "зарлага", "дебит", "зарлагын дүн", "гарах"])
    crd = find(["credit", "орлого", "кредит", "орлогын дүн", "орох"])
    acol = find(["amount", "дүн", "сумма", "мөнгөн дүн"])
    desc = find(["description", "тайлбар", "narration", "details", "гүйлгээний утга", "content", "memo"])
    if desc is None:
        desc = next((c for c in df.columns if c not in (dcol, dbt, crd, acol)), None)
    w = pd.DataFrame({"date": parse_dates(df[dcol]), "description": df[desc].fillna("").astype(str).str.strip() if desc else ""})
    if dbt and crd:       # орлого/зарлага тусдаа баганатай хуулга
        w["signed"] = df[crd].apply(parse_amount).fillna(0).abs() - df[dbt].apply(parse_amount).fillna(0).abs()
    elif acol or dbt or crd:   # нэг багана (+/−)
        w["signed"] = df[acol or dbt or crd].apply(parse_amount)
        if not acol and dbt: w["signed"] = -w["signed"].abs()
    else:
        st.error("Дүнгийн багана олдсонгүй."); return 0, 0
    w = w.dropna(subset=["date", "signed"]); w = w[w["signed"] != 0]
    w["date"] = w["date"].dt.strftime("%Y-%m-%d"); w["amount"] = w["signed"].abs()
    w["type"] = w["signed"].apply(lambda x: "income" if x > 0 else "expense")
    # Нэг файлд бодитоор давтагдсан ижил гүйлгээг хадгалж, дахин импортыг л давхардал гэж үзнэ
    w["n"] = w.groupby(["date", "amount", "description", "type"]).cumcount()
    c = get_conn(); ins = skip = 0
    for r in w.itertuples():
        try:
            c.execute("INSERT INTO transactions(date,description,amount,category,account,type,source_file,tx_hash) VALUES(?,?,?,?,?,?,?,?)",
                      (r.date, r.description, r.amount, auto_categorize(r.description, r.type == "income", c), account, r.type,
                       source, make_hash(r.date, r.amount, r.description, r.n)))
            ins += 1
        except sqlite3.IntegrityError: skip += 1
    c.commit(); c.close(); return ins, skip
 
 
def get_tx(start=None, end=None):
    c = get_conn(); q = "SELECT id,date,description,amount,category,account,type,source_file FROM transactions WHERE 1=1"; p = []
    if start: q += " AND date>=?"; p.append(start)
    if end: q += " AND date<=?"; p.append(end)
    df = pd.read_sql(q + " ORDER BY date DESC", c, params=p); c.close(); return df
 
 
# ---------------- 3. Зорилго (хосолмол санхүүжилт) ----------------
def months_left(td):
    try:
        d = pd.Period(str(td)[:7], "M"); return max(1, (d - pd.Period(date.today(), "M")).n)
    except Exception: return 1
 
 
def goal_plan(g) -> dict:
    """Үнэ = урьдчилгаа (өөрийн хуримтлал + бусад эх үүсвэр + сар бүрийн хадгаламж) + банкны зээл."""
    price = float(g["target_amount"]); down_pct = float(g.get("down_pct") if pd.notna(g.get("down_pct")) else 100)
    down = price * down_pct / 100; loan = price - down
    have = float(g["current_amount"] or 0) + float(g.get("other_sources") or 0)
    gap = max(0.0, down - have); m = months_left(g["target_date"]); monthly = gap / m
    r = float(g.get("loan_rate") or 0) / 1200; n = int(round(float(g.get("loan_years") or 0) * 12))
    pay = (loan * r / (1 - (1 + r) ** -n) if r > 0 else loan / n) if (loan > 0 and n > 0) else 0.0
    return dict(down=down, loan=loan, have=have, gap=gap, months=m, monthly=monthly, pay=pay,
                total_interest=max(0.0, pay * n - loan) if n else 0.0)
 
 
def get_goals():
    c = get_conn(); df = pd.read_sql("SELECT * FROM goals ORDER BY priority, target_date", c); c.close(); return df
 
 
def goals_monthly_total():
    gs = get_goals(); return sum(goal_plan(g)["monthly"] for _, g in gs.iterrows()) if not gs.empty else 0.0
 
 
# ---------------- 2. Төсөв ----------------
def generate_budget(ym):
    """ym-ээс өмнөх 3 сарын зарлагын дунджаар төсөв үүсгэнэ."""
    c = get_conn(); end = pd.Period(ym, "M").start_time.strftime("%Y-%m-%d")
    start = (pd.Period(ym, "M") - 3).start_time.strftime("%Y-%m-%d")
    rows = c.execute("""SELECT category, AVG(m) a FROM (SELECT category, strftime('%Y-%m',date) ym, SUM(amount) m FROM transactions
        WHERE type='expense' AND date>=? AND date<? GROUP BY category, ym) GROUP BY category""", (start, end)).fetchall()
    data = {cat: 0 for cat in EXPENSE_CATS if cat != GOAL_CAT}
    for r in rows:
        if r["category"] != GOAL_CAT: data[r["category"]] = round(float(r["a"] or 0) / 1000) * 1000
    for cat, a in data.items():
        c.execute("INSERT OR REPLACE INTO budgets(year_month,category,amount) VALUES(?,?,?)", (ym, cat, a))
    c.commit(); c.close()
 
 
def get_budget(ym):
    c = get_conn(); df = pd.read_sql("SELECT category, amount FROM budgets WHERE year_month=? AND category<>?", c, params=(ym, GOAL_CAT)); c.close(); return df
 
 
def budget_vs_actual(ym, goal_total):
    c = get_conn()
    b = pd.read_sql("SELECT category, amount budget FROM budgets WHERE year_month=? AND category<>?", c, params=(ym, GOAL_CAT))
    a = pd.read_sql("SELECT category, SUM(amount) actual FROM transactions WHERE type='expense' AND strftime('%Y-%m',date)=? GROUP BY category", c, params=(ym,))
    c.close()
    m = pd.merge(b, a, on="category", how="outer").fillna(0)
    # Зорилгын хуримтлал төсөвт автоматаар (давхардахгүйгээр) орно
    m.loc[m["category"] == GOAL_CAT, "budget"] = 0
    goal_row = pd.DataFrame([{"category": GOAL_CAT + " (зорилго)", "budget": goal_total,
                              "actual": float(a.loc[a["category"] == GOAL_CAT, "actual"].sum())}])
    m = pd.concat([m[m["category"] != GOAL_CAT], goal_row], ignore_index=True)
    m["diff"] = m["budget"] - m["actual"]
    m["pct"] = m.apply(lambda r: r["actual"] / r["budget"] * 100 if r["budget"] > 0 else 0, axis=1)
    return m.sort_values("actual", ascending=False)
 
 
def avg_income(months=3):
    tx = get_tx()
    if tx.empty: return 0.0
    inc = tx[tx["type"] == "income"].copy(); inc["m"] = inc["date"].str[:7]
    return float(inc.groupby("m")["amount"].sum().tail(months).mean()) if not inc.empty else 0.0
 
 
# ---------------- UI ----------------
st.set_page_config(page_title="Санхүүгийн апп", page_icon="💰", layout="wide")
init_db()
st.sidebar.title("💰 Санхүүгийн апп"); st.sidebar.caption(f"v{VERSION}")
page = st.sidebar.radio("Цэс", ["📊 Хянах самбар", "📥 Хуулга импорт", "📋 Гүйлгээнүүд", "📅 Төсөв", "🎯 Зорилго", "📈 Тайлан", "⚙️ Тохиргоо"])
NEXT_YM = (date.today().replace(day=1) + relativedelta(months=1)).strftime("%Y-%m")
 
if page == "📊 Хянах самбар":
    st.title("Хянах самбар"); tx = get_tx()
    if tx.empty: st.info("Гүйлгээ алга. «Хуулга импорт» хэсгээс эхлээрэй.")
    else:
        mt = tx[tx["date"].str.startswith(date.today().strftime("%Y-%m"))]
        inc = mt[mt["type"] == "income"]["amount"].sum(); exp = mt[mt["type"] == "expense"]["amount"].sum()
        a, b, c_, d = st.columns(4)
        a.metric("Энэ сарын орлого", money(inc)); b.metric("Зарлага", money(exp))
        c_.metric("Үлдэгдэл", money(inc - exp)); d.metric("Нийт гүйлгээ", f"{len(tx):,}")
        l, r = st.columns(2)
        e = mt[mt["type"] == "expense"]
        if not e.empty:
            l.plotly_chart(px.pie(e.groupby("category")["amount"].sum().reset_index(), values="amount", names="category", hole=.4, title="Зарлагын бүтэц"), use_container_width=True)
        tx["month"] = tx["date"].str[:7]
        mo = tx.groupby(["month", "type"])["amount"].sum().unstack(fill_value=0).reindex(columns=["income", "expense"], fill_value=0).reset_index().tail(6)
        f = go.Figure([go.Bar(x=mo["month"], y=mo["income"], name="Орлого", marker_color="#22c55e"),
                       go.Bar(x=mo["month"], y=mo["expense"], name="Зарлага", marker_color="#ef4444")]); f.update_layout(title="Сүүлийн 6 сар", barmode="group")
        r.plotly_chart(f, use_container_width=True)
 
elif page == "📥 Хуулга импорт":
    st.title("Банкны хуулга импортлох")
    st.write("CSV, Excel, PDF. Дараагийн хугацааны хуулга өмнөх өгөгдөл дээр **нэмэгдэнэ**, давхардсан гүйлгээг алгасна.")
    up = st.file_uploader("Файл", type=["csv", "xlsx", "xls", "pdf"]); acc = st.text_input("Дансны нэр", "Үндсэн")
    if up is not None:
        sfx = Path(up.name).suffix.lower(); df = None
        try:
            if sfx == ".csv":
                for enc in ("utf-8-sig", "utf-8", "cp1251", "latin1"):
                    try: up.seek(0); df = pd.read_csv(up, encoding=enc); break
                    except Exception: continue
            elif sfx in (".xlsx", ".xls"): df = pd.read_excel(up)
            else:
                import pdfplumber
                rows = []
                with pdfplumber.open(io.BytesIO(up.read())) as pdf:
                    for pg in pdf.pages:
                        for t in pg.extract_tables() or []:
                            rows += [[x or "" for x in r] for r in t if r and any(x and str(x).strip() for x in r)]
                if len(rows) > 1: df = pd.DataFrame(rows[1:], columns=[str(x).strip() for x in rows[0]])
            if df is None or df.empty: st.error("Файлаас өгөгдөл уншиж чадсангүй.")
            else:
                st.dataframe(df.head(10), use_container_width=True); st.caption(f"Нийт {len(df)} мөр")
                if st.button("Импортлох", type="primary"):
                    i, s = import_df(df, acc, up.name); st.success(f"✅ {i} шинэ гүйлгээ нэмэгдлээ, {s} давхардсан алгаслаа.")
        except Exception as ex: st.error(f"Алдаа: {ex}")
 
elif page == "📋 Гүйлгээнүүд":
    st.title("Гүйлгээнүүд")
    a, b, c_ = st.columns(3)
    s = a.date_input("Эхлэх", date.today() - relativedelta(months=1)); e = b.date_input("Дуусах", date.today())
    t = c_.selectbox("Төрөл", ["Бүгд", "Орлого", "Зарлага"])
    tx = get_tx(str(s), str(e))
    if t != "Бүгд": tx = tx[tx["type"] == ("income" if t == "Орлого" else "expense")]
    if tx.empty: st.info("Гүйлгээ олдсонгүй.")
    else:
        cats = sorted(set(EXPENSE_CATS + ["Цалин", "Нэмэлт орлого", "Хүү / Ногдол ашиг", "Бусад орлого"] + tx["category"].dropna().tolist()))
        ed = st.data_editor(tx.drop(columns=["source_file"]), hide_index=True, use_container_width=True, height=450,
                            disabled=["id", "date", "description", "amount", "type", "account"],
                            column_config={"category": st.column_config.SelectboxColumn("category", options=cats)})
        if st.button("Ангилал хадгалах"):
            c = get_conn(); c.executemany("UPDATE transactions SET category=? WHERE id=?", [(r.category, int(r.id)) for r in ed.itertuples()])
            c.commit(); c.close(); st.success("Хадгаллаа")
 
elif page == "📅 Төсөв":
    st.title("Төсөв")
    ym = st.text_input("Сар (YYYY-MM)", NEXT_YM, help="Анхдагчаар дараа сар")
    if st.button("Сүүлийн 3 сарын дунджаас төсөв үүсгэх"): generate_budget(ym); st.success(f"{ym} төсөв үүслээ"); st.rerun()
    cur = get_budget(ym)
    if cur.empty: cur = pd.DataFrame({"category": [x for x in EXPENSE_CATS if x != GOAL_CAT], "amount": 0.0}); st.caption("Төсөв хараахан үүсээгүй — дээрх товчоор санал болгуулах эсвэл доор шууд бөглөнө.")
    ed = st.data_editor(cur, hide_index=True, use_container_width=True, disabled=["category"], key=f"ed_{ym}")
    if st.button("Төсөв хадгалах", type="primary"):
        c = get_conn(); c.executemany("INSERT OR REPLACE INTO budgets(year_month,category,amount) VALUES(?,?,?)", [(ym, r.category, float(r.amount)) for r in ed.itertuples()])
        c.commit(); c.close(); st.success("Хадгаллаа")
    gt = goals_monthly_total(); inc = avg_income(); tot = float(ed["amount"].sum())
    a, b, c_, d = st.columns(4)
    a.metric("Дундаж сарын орлого", money(inc)); b.metric("Төсөвт зарлага", money(tot))
    c_.metric("Зорилгын хуримтлал", money(gt)); d.metric("Үлдэгдэл", money(inc - tot - gt))
    if inc - tot - gt < 0: st.warning("Төсөв + зорилгын хуримтлал орлогоос хэтэрч байна.")
    bva = budget_vs_actual(ym, gt)
    st.subheader(f"Төсөв vs Гүйцэтгэл — {ym}")
    f = go.Figure([go.Bar(name="Төсөв", x=bva["category"], y=bva["budget"], marker_color="#667eea"),
                   go.Bar(name="Гүйцэтгэл", x=bva["category"], y=bva["actual"], marker_color="#f59e0b")])
    f.update_layout(barmode="group", xaxis_tickangle=-30, height=400); st.plotly_chart(f, use_container_width=True)
    st.dataframe(bva.rename(columns={"category": "Ангилал", "budget": "Төсөв", "actual": "Гүйцэтгэл", "diff": "Зөрүү", "pct": "%"}).style.format(
        {"Төсөв": "{:,.0f}", "Гүйцэтгэл": "{:,.0f}", "Зөрүү": "{:,.0f}", "%": "{:.0f}%"}), use_container_width=True)
    st.caption("Зорилгын хуримтлал «Зорилго» хэсгээс автоматаар тооцогдоно (давхар нэмэгдэхгүй).")
 
elif page == "🎯 Зорилго":
    st.title("Урт хугацааны зорилго")
    st.caption("Үнэ = урьдчилгаа (өөрийн хуримтлал + бусад эх үүсвэр + сар бүрийн хадгаламж) + банкны зээл")
    gs = get_goals()
    for _, g in gs.iterrows():
        p = goal_plan(g)
        with st.container(border=True):
            st.markdown(f"### {g['name']} · {g.get('kind') or 'Бусад'} · {money(g['target_amount'])}")
            a, b, c_, d = st.columns(4)
            a.metric("Урьдчилгаа", money(p["down"])); b.metric("Одоо байгаа", money(p["have"]))
            c_.metric(f"Сард хуримтлуулах ({p['months']} сар)", money(p["monthly"])); d.metric("Зээл / сарын төлбөр", f"{money(p['loan'])} / {money(p['pay'])}")
            st.progress(min(1.0, p["have"] / p["down"]) if p["down"] > 0 else 1.0)
            if p["total_interest"]: st.caption(f"Зээлийн нийт хүү ≈ {money(p['total_interest'])}")
            x, y = st.columns([3, 1])
            add = x.number_input("Хуримтлал нэмэх (₮)", 0.0, step=100000.0, key=f"a{g['id']}")
            if x.button("Нэмэх", key=f"b{g['id']}") and add > 0:
                c = get_conn(); c.execute("UPDATE goals SET current_amount=current_amount+? WHERE id=?", (add, int(g["id"]))); c.commit(); c.close(); st.rerun()
            if y.button("Устгах", key=f"d{g['id']}"):
                c = get_conn(); c.execute("DELETE FROM goals WHERE id=?", (int(g["id"]),)); c.commit(); c.close(); st.rerun()
    if not gs.empty:
        gt = goals_monthly_total(); inc = avg_income(); bt = float(get_budget(NEXT_YM)["amount"].sum())
        st.subheader("Үндсэн төсөвтэй хосолсон дүн")
        a, b, c_, d = st.columns(4)
        a.metric("Дундаж орлого", money(inc)); b.metric(f"{NEXT_YM} төсөв", money(bt)); c_.metric("Бүх зорилгын сарын хуримтлал", money(gt))
        d.metric("Үлдэгдэл", money(inc - bt - gt))
        if inc - bt - gt < 0: st.error("Зорилгууд төсөвтэйгээ хамт орлогоос хэтэрч байна — хугацааг уртасгах, урьдчилгааг багасгах эсвэл төсвөө бууруулна уу.")
        else: st.success("Зорилгууд төсөвтэй зөрчилгүй.")
    st.subheader("Шинэ зорилго нэмэх")
    with st.form("goal"):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Нэр"); kind = c2.selectbox("Төрөл", GOAL_KINDS); price = c3.number_input("Нийт үнэ (₮)", 0.0, step=1000000.0)
        c4, c5, c6 = st.columns(3)
        td = c4.date_input("Хүрэх хугацаа", date.today() + relativedelta(years=3)); down = c5.number_input("Урьдчилгаа (%)", 0.0, 100.0, 30.0)
        cur = c6.number_input("Одоогийн хуримтлал (₮)", 0.0, step=100000.0)
        c7, c8, c9 = st.columns(3)
        oth = c7.number_input("Бусад эх үүсвэр (₮) — тусламж, борлуулалт", 0.0, step=100000.0)
        rate = c8.number_input("Зээлийн хүү (жилийн %)", 0.0, 100.0, 8.0); yrs = c9.number_input("Зээлийн хугацаа (жил)", 0.0, 40.0, 20.0)
        notes = st.text_input("Тэмдэглэл")
        if st.form_submit_button("Нэмэх", type="primary") and name and price > 0:
            try:
                c = get_conn(); c.execute("""INSERT INTO goals(name,target_amount,current_amount,target_date,notes,kind,down_pct,other_sources,loan_rate,loan_years)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (name, price, cur, str(td), notes, kind, down, oth, rate, yrs)); c.commit(); c.close(); st.rerun()
            except sqlite3.IntegrityError: st.error("Ийм нэртэй зорилго байна")
 
elif page == "📈 Тайлан":
    st.title("Тайлан"); ym = st.text_input("Сар (YYYY-MM)", date.today().strftime("%Y-%m")); tx = get_tx(); mt = tx[tx["date"].str.startswith(ym)] if not tx.empty else tx
    if mt.empty: st.info("Өгөгдөл алга")
    else:
        l, r = st.columns(2)
        for col, tp, ttl in ((l, "income", "Орлого"), (r, "expense", "Зарлага")):
            s = mt[mt["type"] == tp].groupby("category")["amount"].sum()
            col.subheader(ttl); col.dataframe(s.reset_index().style.format({"amount": "{:,.0f}"})); col.metric(f"Нийт {ttl.lower()}", money(s.sum()))
    if st.button("Excel рүү экспортлох"):
        c = get_conn(); out = "finance_export.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            for t, sh in (("transactions", "Гүйлгээ"), ("budgets", "Төсөв"), ("goals", "Зорилго")): pd.read_sql(f"SELECT * FROM {t}", c).to_excel(w, sheet_name=sh, index=False)
        c.close(); st.download_button("Татаж авах", open(out, "rb"), file_name=out)
 
else:
    st.title("Тохиргоо"); st.subheader("Ангиллын дүрэм нэмэх")
    with st.form("rule"):
        kw = st.text_input("Түлхүүр үг (жишээ: nomin)"); cat = st.selectbox("Ангилал", EXPENSE_CATS + ["Цалин", "Нэмэлт орлого", "Хүү / Ногдол ашиг", "Бусад орлого"])
        if st.form_submit_button("Нэмэх") and kw:
            c = get_conn()
            try: c.execute("INSERT INTO category_rules(keyword,category) VALUES(?,?)", (kw.lower(), cat)); c.commit(); st.success(f"«{kw}» → {cat}")
            except sqlite3.IntegrityError: st.warning("Аль хэдийн байна")
            c.close()
    c = get_conn(); st.write(f"Гүйлгээ: **{c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]}** · Зорилго: **{c.execute('SELECT COUNT(*) FROM goals').fetchone()[0]}**"); c.close()
    st.caption(f"Өгөгдлийн сан: {DB_PATH.absolute()}")