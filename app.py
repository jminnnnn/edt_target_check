# -*- coding: utf-8 -*-
"""
국내 종목 거래량 조회 Streamlit 앱

- 회사명 또는 종목코드로 검색 (회사명 입력 시 종목코드 자동 매칭)
- 일별 거래량 / 종가 그래프
- 최근 1주 / 1개월 / 3개월 평균거래량, 평균거래대금
- 시가총액 및 (평균거래량 / 상장주식수) 회전율

필요 패키지:
    pip install streamlit yfinance pandas plotly openpyxl requests lxml finance-datareader

실행 방법:
    streamlit run app.py

※ 전종목 목록: 1순위 KIND(기업공시채널), 실패 시 FinanceDataReader
※ 시가총액/상장주식수: 1순위 FinanceDataReader 목록 값, 없으면 야후파이낸스(fast_info → get_info) 순서로 시도
"""

import io
import math

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="종목 거래량 조회", layout="wide")

WINDOWS = {
    "1주": 5,
    "1개월": 21,
    "3개월": 63,
}

PERIOD_OPTIONS = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
    "2년": "2y",
}

LISTING_COLS = ["종목명", "종목코드", "시장구분", "시가총액", "상장주식수"]


# ────────────────────────────────────────────────────────
# 데이터 로딩 (캐시)
# ────────────────────────────────────────────────────────
KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"


def _fetch_kind_market(market_type: str) -> pd.DataFrame:
    """KIND(기업공시채널) 상장법인목록 다운로드 - 로그인 불필요"""
    params = {
        "method": "download",
        "searchType": "13",
        "marketType": market_type,  # stockMkt=코스피, kosdaqMkt=코스닥
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://kind.krx.co.kr/corpgeneral/corpList.do?method=loadInitPage",
    }
    resp = requests.get(KIND_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = "euc-kr"

    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    df = df[["회사명", "종목코드"]].rename(columns={"회사명": "종목명"})

    # KIND 목록에는 시가총액/상장주식수가 없으므로 빈 값으로 둠 (나중에 야후로 채움)
    df["시가총액"] = float("nan")
    df["상장주식수"] = float("nan")
    return df


def _normalize_market(value) -> str | None:
    """FinanceDataReader의 시장 표기를 KOSPI / KOSDAQ 으로 통일"""
    v = str(value).upper()
    if v in ("KOSPI", "STK"):
        return "KOSPI"
    if v.startswith("KOSDAQ") or v == "KSQ":  # 'KOSDAQ GLOBAL' 포함
        return "KOSDAQ"
    return None  # KONEX 등은 제외


def _fetch_fdr_listing() -> pd.DataFrame:
    """FinanceDataReader 전종목 목록 (시가총액 Marcap, 상장주식수 Stocks 포함)"""
    import FinanceDataReader as fdr

    try:
        fdr_df = fdr.StockListing("KRX")
    except Exception:
        fdr_df = pd.concat(
            [fdr.StockListing("KOSPI"), fdr.StockListing("KOSDAQ")], ignore_index=True
        )

    code_col = "Code" if "Code" in fdr_df.columns else "Symbol"
    market_col = "Market" if "Market" in fdr_df.columns else "MarketId"

    out = pd.DataFrame()
    out["종목명"] = fdr_df["Name"]
    out["종목코드"] = fdr_df[code_col].astype(str).str.zfill(6)
    out["시장구분"] = fdr_df[market_col].map(_normalize_market)

    # 시가총액(원), 상장주식수 - 컬럼이 없거나 비어 있으면 NaN
    out["시가총액"] = pd.to_numeric(fdr_df.get("Marcap"), errors="coerce") if "Marcap" in fdr_df.columns else float("nan")
    out["상장주식수"] = pd.to_numeric(fdr_df.get("Stocks"), errors="coerce") if "Stocks" in fdr_df.columns else float("nan")

    return out.dropna(subset=["시장구분"])


def _fetch_krx_listing():
    """1순위: KIND. 실패 시 2순위: FinanceDataReader."""
    errors = []

    try:
        kospi = _fetch_kind_market("stockMkt")
        kospi["시장구분"] = "KOSPI"
        kosdaq = _fetch_kind_market("kosdaqMkt")
        kosdaq["시장구분"] = "KOSDAQ"
        return pd.concat([kospi, kosdaq], ignore_index=True)[LISTING_COLS]
    except Exception as e:
        errors.append(f"KIND 상장법인목록 조회 실패: {e}")

    try:
        return _fetch_fdr_listing()[LISTING_COLS]
    except ImportError:
        errors.append("FinanceDataReader 미설치 (requirements.txt에 finance-datareader 추가 필요)")
    except Exception as e:
        errors.append(f"FinanceDataReader 폴백도 실패: {e}")

    raise RuntimeError(
        "전종목 목록을 가져오지 못했습니다.\n"
        + "\n".join(errors)
        + "\n\n해결 방법: 1) 인터넷 연결 확인 2) pip install -U requests lxml 후 재시도"
        + "\n3) 잠시 후 다시 시도 (KIND 서버 쪽 일시적 문제일 수 있음)"
    )


@st.cache_data(ttl=60 * 60 * 6, show_spinner="전종목 목록 불러오는 중...")
def load_ticker_list():
    df = _fetch_krx_listing()

    df["야후심볼"] = df.apply(
        lambda r: r["종목코드"] + (".KS" if r["시장구분"] == "KOSPI" else ".KQ"), axis=1
    )

    return df[LISTING_COLS + ["야후심볼"]].reset_index(drop=True)


def _valid(x) -> bool:
    """None, NaN, 0 이 아닌 숫자인지 확인"""
    try:
        return x is not None and not math.isnan(float(x)) and float(x) > 0
    except (TypeError, ValueError):
        return False


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def get_yahoo_cap_info(symbol: str):
    """야후파이낸스에서 시가총액/상장주식수 조회. fast_info 먼저, 안 되면 get_info."""
    market_cap, shares = None, None

    # 1) fast_info : 가볍고 빠르며 클라우드 환경에서도 비교적 잘 동작
    try:
        fi = yf.Ticker(symbol).fast_info
        shares = fi.get("shares") if hasattr(fi, "get") else getattr(fi, "shares", None)
        market_cap = fi.get("market_cap") if hasattr(fi, "get") else getattr(fi, "market_cap", None)
    except Exception:
        pass

    # 2) get_info : 무겁고 가끔 막히지만 정보가 더 많음
    if not (_valid(market_cap) and _valid(shares)):
        try:
            info = yf.Ticker(symbol).get_info()
            if not _valid(shares):
                shares = info.get("sharesOutstanding")
            if not _valid(market_cap):
                market_cap = info.get("marketCap")
        except Exception:
            pass

    return (
        float(market_cap) if _valid(market_cap) else None,
        float(shares) if _valid(shares) else None,
    )


def get_market_cap_info(symbol: str, last_price: float, listing_cap, listing_shares):
    """
    시가총액(억원), 상장주식수, 출처를 반환.
    1순위: 전종목 목록(FinanceDataReader)에 들어있는 값
    2순위: 야후파이낸스
    둘 중 하나만 있으면 '주식수 x 종가' 로 시가총액을 계산.
    """
    market_cap = float(listing_cap) if _valid(listing_cap) else None
    shares = float(listing_shares) if _valid(listing_shares) else None
    source = "KRX 목록(FinanceDataReader)" if (market_cap or shares) else None

    if market_cap is None or shares is None:
        y_cap, y_shares = get_yahoo_cap_info(symbol)
        if market_cap is None and y_cap:
            market_cap = y_cap
        if shares is None and y_shares:
            shares = y_shares
        if (y_cap or y_shares) and source is None:
            source = "야후파이낸스"
        elif (y_cap or y_shares) and source:
            source += " + 야후파이낸스"

    # 시가총액이 없고 주식수만 있으면 직접 계산
    if market_cap is None and shares:
        market_cap = shares * last_price

    marcap_eok = round(market_cap / 1e8, 1) if market_cap else None
    return marcap_eok, shares, source


@st.cache_data(ttl=60 * 30, show_spinner="시세 데이터 가져오는 중...")
def load_history(symbol: str, period: str):
    hist = yf.Ticker(symbol).history(period=period, auto_adjust=False)
    if hist.empty:
        return None
    hist = hist.copy()
    hist.index = hist.index.tz_localize(None)
    hist["거래대금"] = hist["Close"] * hist["Volume"]
    return hist


def search_tickers(ticker_df: pd.DataFrame, query: str) -> pd.DataFrame:
    query = query.strip()
    if not query:
        return ticker_df.iloc[0:0]

    if query.isdigit():
        return ticker_df[ticker_df["종목코드"] == query.zfill(6)]

    return ticker_df[ticker_df["종목명"].str.contains(query, case=False, na=False)]


# ────────────────────────────────────────────────────────
# 사이드바: 검색
# ────────────────────────────────────────────────────────
st.sidebar.title("종목 검색")
try:
    ticker_df = load_ticker_list()
except Exception as e:
    st.error(f"전종목 목록을 불러오는 데 실패했습니다.\n\n{e}")
    st.stop()

query = st.sidebar.text_input("회사명 또는 종목코드 입력", placeholder="예: 삼기, 신스틸, 122350")

selected_row = None

if query:
    matches = search_tickers(ticker_df, query)

    if matches.empty:
        st.sidebar.warning("일치하는 종목이 없습니다.")
    elif len(matches) == 1:
        selected_row = matches.iloc[0]
    else:
        options = matches.apply(
            lambda r: f"{r['종목명']} ({r['종목코드']}, {r['시장구분']})", axis=1
        ).tolist()
        picked = st.sidebar.selectbox(f"{len(matches)}건 검색됨 - 종목 선택", options)
        idx = options.index(picked)
        selected_row = matches.iloc[idx]

chart_period_label = st.sidebar.selectbox("차트 조회 기간", list(PERIOD_OPTIONS.keys()), index=2)

st.sidebar.caption(
    "※ 종목 목록은 KIND 또는 FinanceDataReader 기준, 시세·거래량은 야후파이낸스 기준입니다. "
    "시가총액은 FinanceDataReader 값을 우선 사용하고, 없으면 야후파이낸스 값을 사용합니다."
)


# ────────────────────────────────────────────────────────
# 메인 화면
# ────────────────────────────────────────────────────────
st.title("국내 종목 거래량 조회")

if selected_row is None:
    st.info("왼쪽 사이드바에서 회사명 또는 종목코드를 입력하세요. (예: 삼기, 신스틸, 122350)")
    st.stop()

symbol = selected_row["야후심볼"]
name = selected_row["종목명"]
code = selected_row["종목코드"]
market = selected_row["시장구분"]

hist_full = load_history(symbol, PERIOD_OPTIONS[chart_period_label])
hist_3mo = load_history(symbol, "4mo")  # 평균 계산용 여유분 (3개월 + 버퍼)

if hist_full is None or hist_3mo is None:
    st.error(f"{name}({code}) - 야후파이낸스에서 데이터를 찾지 못했습니다. (.KS/.KQ 확인 필요)")
    st.stop()

hist_full = hist_full.sort_index()
hist_3mo = hist_3mo.sort_index()

last_price = float(hist_full["Close"].iloc[-1])
last_date = hist_full.index[-1].strftime("%Y-%m-%d")

marcap_eok, shares_out, cap_source = get_market_cap_info(
    symbol,
    last_price,
    selected_row.get("시가총액"),
    selected_row.get("상장주식수"),
)

st.subheader(f"{name} ({code}) · {market}")
caption = f"기준일: {last_date} · 야후심볼: {symbol}"
if cap_source:
    caption += f" · 시가총액 출처: {cap_source}"
st.caption(caption)

# ── 상단 요약 지표 ──
col1, col2, col3, col4 = st.columns(4)
col1.metric("최근 종가", f"{last_price:,.0f}원")
col2.metric("시가총액", f"{marcap_eok:,.0f}억원" if marcap_eok else "정보 없음")

vol_1w = hist_3mo["Volume"].tail(WINDOWS["1주"]).mean()
vol_1m = hist_3mo["Volume"].tail(WINDOWS["1개월"]).mean()
col3.metric("1주 평균거래량", f"{vol_1w:,.0f}주")
col4.metric("1개월 평균거래량", f"{vol_1m:,.0f}주")

# ── 기간별 평균거래량 / 평균거래대금 / 회전율 표 ──
st.markdown("### 기간별 평균거래량 · 평균거래대금 · 회전율")

summary_rows = []
for label, n_days in WINDOWS.items():
    sub = hist_3mo.tail(n_days)
    avg_vol = sub["Volume"].mean()
    avg_val = sub["거래대금"].mean()
    turnover = (avg_vol / shares_out * 100) if shares_out else None

    summary_rows.append(
        {
            "기간": label,
            "평균거래량(주)": round(avg_vol),
            "평균거래대금(억원)": round(avg_val / 1e8, 2),
            "회전율(%)": round(turnover, 3) if turnover is not None else None,
        }
    )

summary_df = pd.DataFrame(summary_rows)
st.dataframe(summary_df, hide_index=True, use_container_width=True)

if shares_out is None:
    st.caption("※ 상장주식수 정보를 찾지 못해 회전율을 계산하지 못했습니다.")

# ── 일별 그래프 (종가 + 거래량) ──
st.markdown(f"### 일별 종가 · 거래량 ({chart_period_label})")

fig = go.Figure()
fig.add_trace(
    go.Bar(
        x=hist_full.index,
        y=hist_full["Volume"],
        name="거래량",
        marker_color="#8ecae6",
        yaxis="y2",
        opacity=0.6,
    )
)
fig.add_trace(
    go.Scatter(
        x=hist_full.index,
        y=hist_full["Close"],
        name="종가",
        line=dict(color="#e63946", width=2),
        yaxis="y1",
    )
)

fig.update_layout(
    height=500,
    xaxis=dict(title="날짜"),
    yaxis=dict(title="종가(원)", side="left"),
    yaxis2=dict(title="거래량(주)", overlaying="y", side="right", showgrid=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=10, r=10, t=30, b=10),
)

st.plotly_chart(fig, use_container_width=True)

# ── 거래대금 그래프 ──
st.markdown(f"### 일별 거래대금 ({chart_period_label})")

fig2 = go.Figure()
fig2.add_trace(
    go.Bar(
        x=hist_full.index,
        y=hist_full["거래대금"] / 1e8,
        name="거래대금(억원)",
        marker_color="#ffb703",
    )
)
fig2.update_layout(
    height=350,
    xaxis=dict(title="날짜"),
    yaxis=dict(title="거래대금(억원)"),
    margin=dict(l=10, r=10, t=30, b=10),
)
st.plotly_chart(fig2, use_container_width=True)

# ── 원본 데이터 테이블 + 다운로드 ──
st.markdown("### 일별 원본 데이터")

display_df = hist_full[["Open", "High", "Low", "Close", "Volume", "거래대금"]].copy()
display_df.columns = ["시가", "고가", "저가", "종가", "거래량", "거래대금(원)"]
display_df = display_df.sort_index(ascending=False)

st.dataframe(display_df, use_container_width=True)

csv = display_df.to_csv().encode("utf-8-sig")
st.download_button(
    "CSV로 다운로드",
    data=csv,
    file_name=f"{name}_{code}_일별데이터.csv",
    mime="text/csv",
)
