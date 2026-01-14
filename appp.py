import streamlit as st
import feedparser
import urllib.parse
import os
import time
import requests
import urllib3
import sqlite3
import pandas as pd
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from newspaper import Article, Config
import nltk
import google.generativeai as genai

# -------------------------------------------
# 0. API 키 및 초기 설정
# -------------------------------------------

GOOGLE_API_KEY = "AIzaSyAdnBk6ZdKpxL98LHHaGj9Bjbfk_dX81DA" 

try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    st.error(f"API 키 설정 오류: {e}")

# SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# NLTK 데이터 다운로드
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    nltk.download('punkt_tab')

HISTORY_FILE = "seen_titles.txt"
DB_FILE = "news_database.db"

# -------------------------------------------
# 1. 유틸리티 및 DB 함수들
# -------------------------------------------

def get_final_url(url):
    """구글 리다이렉트 URL을 실제 뉴스 URL로 변환"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=5, allow_redirects=True, verify=False)
        return response.url
    except Exception:
        return url

def fetch_rss_feed(url):
    """RSS 피드 가져오기"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        if response.status_code != 200:
            return None
        return feedparser.parse(response.content)
    except Exception as e:
        return None

def is_within_1hour(published_str):
    """
    기사 발행 시간을 확인하여 1시간 이내인지 판별하는 함수
    """
    if not published_str:
        return False
    try:
        # RSS 날짜 파싱 (GMT 기준)
        pub_dt = parsedate_to_datetime(published_str)
        
        # 현재 시간 (UTC로 통일하여 계산)
        now_dt = datetime.now(timezone.utc)
        
        # 차이 계산
        diff = now_dt - pub_dt
        
        # 3600초(1시간) 이내면 True, 아니면 False
        # (약간의 오차 허용을 위해 65분까지 여유를 둠)
        if diff.total_seconds() <= 3900: 
            return True
        return False
    except:
        return False # 날짜 파싱 실패 시 제외

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            title TEXT,
            link TEXT,
            pub_date TEXT,
            saved_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_news_to_db(keyword, title, link, pub_date):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM saved_news WHERE title = ? AND link = ?", (title, link))
    if c.fetchone():
        conn.close()
        return False
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO saved_news (keyword, title, link, pub_date, saved_at) VALUES (?, ?, ?, ?, ?)",
              (keyword, title, link, pub_date, saved_at))
    conn.commit()
    conn.close()
    return True

def get_saved_news():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM saved_news ORDER BY saved_at DESC", conn)
    conn.close()
    return df

def delete_news_from_db(news_ids):
    if not news_ids: return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    placeholders = ', '.join('?' for _ in news_ids)
    c.execute(f"DELETE FROM saved_news WHERE id IN ({placeholders})", news_ids)
    conn.commit()
    conn.close()

init_db()

def load_seen_titles():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_seen_title(title):
    clean_title = title.replace("\n", " ")
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(clean_title + "\n")

def format_date_kor(date_str):
    try:
        if not date_str: return "시간 정보 없음"
        dt = parsedate_to_datetime(date_str)
        KST = timezone(timedelta(hours=9))
        dt_kst = dt.astimezone(KST)
        return dt_kst.strftime("%Y년 %m월 %d일 %H:%M")
    except:
        return date_str[:16]

def get_current_time_str():
    now = datetime.now()
    return now.strftime("%Y년 %m월 %d일 %H시 %M분 %S초")

# -------------------------------------------
# 2. 화면 구성 (UI)
# -------------------------------------------
st.set_page_config(page_title="기업 뉴스 모니터링", page_icon="💻", layout="wide")

if 'selected_article_url' not in st.session_state:
    st.session_state['selected_article_url'] = None
if 'selected_article_title' not in st.session_state:
    st.session_state['selected_article_title'] = None

with st.sidebar:
    st.header("⚙️ 모니터링 설정")
    default_keywords = "롯데마트, 롯데웰푸드, [단독]롯데, 롯데칠성, 세븐일레븐, 삼성"
    user_input = st.text_area("키워드 입력 (콤마 구분)", value=default_keywords, height=100)
    
    KEYWORDS = [k.strip() for k in user_input.split(',') if k.strip()]
    
    st.divider()
    
    st.subheader("⏱️ 자동 업데이트")
    auto_refresh = st.checkbox("자동 새로고침 켜기", value=True)
    refresh_interval = st.slider("업데이트 주기 (분)", 5, 60, 15)
    
    if st.button("🗑️ 기록 초기화"):
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
            st.rerun()

st.title("💻 실시간 뉴스 모니터링 (Gemini AI)")

# -------------------------------------------
# 3. 메인 로직
# -------------------------------------------
tab1, tab2, tab3 = st.tabs(["📢 뉴스 목록 (1시간 이내)", "📝 AI 상세 요약", "🗄️ 저장소 (DB)"])

# === [탭 1] 뉴스 목록 ===
with tab1:
    status_container = st.container()
    
    seen_titles = load_seen_titles()
    grouped_news = {k: [] for k in KEYWORDS}
    new_news_count = 0 
    
    with st.spinner("1시간 이내 뉴스를 정밀 검색 중입니다..."):
        for keyword in KEYWORDS:
            clean_keyword = keyword.strip()
            
            # [전략 변경] 
            # 구글에는 '12시간(when:12h)' 데이터를 요청해서 넉넉하게 받아옵니다.
            # 그 후, 아래 코드(is_within_1hour)에서 1시간 이내인 것만 살립니다.
            search_query = clean_keyword + " when:12h"
            encoded_keyword = urllib.parse.quote(search_query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
            
            feed = fetch_rss_feed(rss_url)
            
            if not feed or not feed.entries:
                continue

            for entry in feed.entries:
                # [여기서 필터링] 1시간 이내가 아니면 과감히 스킵
                if not is_within_1hour(entry.get('published', '')):
                    continue
                
                title = entry.title
                link = entry.link
                nice_date = format_date_kor(entry.get('published', ''))
                
                if clean_keyword not in title: continue
                
                grouped_news[clean_keyword].append({
                    "title": title, "link": link, "date": nice_date
                })
                
                if title not in seen_titles:
                    seen_titles.add(title)
                    save_seen_title(title)
                    new_news_count += 1

    current_time = get_current_time_str()
    total_news = sum(len(items) for items in grouped_news.values())

    if new_news_count > 0:
        status_container.success(f"🔥 **업데이트 완료 ({current_time})** : {new_news_count}건의 새로운 뉴스!")
        st.toast(f"{new_news_count}건의 새 뉴스가 있습니다!", icon="🔥")
    elif total_news > 0:
        status_container.info(f"✅ **업데이트 완료 ({current_time})** : 1시간 이내 새로운 뉴스는 없지만, 기존 {total_news}건이 표시됩니다.")
    else:
        status_container.warning(f"⚠️ **검색된 뉴스가 없습니다** ({current_time}) - 최근 1시간 이내에 발행된 기사가 감지되지 않았습니다.")

    btn_idx = 0 
    for keyword, items in grouped_news.items():
        if items: 
            with st.expander(f"📂 **{keyword}** ({len(items)}건)", expanded=True):
                for item in items:
                    with st.container():
                        c1, c2, c3, c4 = st.columns([1.2, 3.5, 0.8, 0.8])
                        c1.markdown(f":orange[{item['date']}]")
                        c2.markdown(f"[{item['title']}]({item['link']})")
                        
                        if c3.button("📝 요약", key=f"btn_sum_{btn_idx}"):
                            st.session_state['selected_article_url'] = item['link']
                            st.session_state['selected_article_title'] = item['title']
                            st.toast("탭 2로 이동하세요!", icon="👉")
                        
                        if c4.button("💾 저장", key=f"btn_save_{btn_idx}"):
                            success = save_news_to_db(keyword, item['title'], item['link'], item['date'])
                            if success:
                                st.toast("저장소(DB)에 저장되었습니다!", icon="✅")
                            else:
                                st.toast("이미 저장된 뉴스입니다.", icon="⚠️")
                                
                        btn_idx += 1
                    st.divider()

# === [탭 2] AI 요약 ===
with tab2:
    st.header("📝 Gemini 기사 요약")
    selected_url = st.session_state['selected_article_url']
    
    if selected_url is None:
        st.info("👈 [뉴스 목록] 탭에서 'AI 요약' 버튼을 먼저 눌러주세요.")
    else:
        st.subheader(f"{st.session_state['selected_article_title']}")
        st.markdown("---")
        
        with st.spinner("🔗 실제 기사 주소를 찾는 중..."):
            final_url = get_final_url(selected_url)
        
        with st.spinner(f"Gemini가 기사를 읽고 분석 중입니다..."):
            try:
                config = Config()
                config.request_timeout = 10
                config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                config.request_kwargs = {'verify': False}
                
                article = Article(final_url, language='ko', config=config)
                article.download()
                article.parse()
                
                if article.top_image:
                    st.image(article.top_image, use_container_width=True)

                if len(article.text) < 50:
                    st.warning("⚠️ 본문을 가져오지 못했습니다. (보안이 강한 언론사이거나 유료 기사일 수 있습니다)")
                    st.write(f"변환된 링크: {final_url}")
                else:
                    prompt = f"""
                    다음 뉴스 기사를 읽고 아래 형식으로 요약해줘:
                    1. **한줄 요약**: 기사의 핵심 주제
                    2. **상세 포인트**: 중요 내용 3가지 (글머리 기호)
                    3. **감정 분석**: 긍정/부정/중립 중 하나
                    
                    [기사 본문]
                    {article.text[:3000]}
                    """
                    response = model.generate_content(prompt)
                    st.success(response.text)

                with st.expander("원본 본문 보기"):
                    st.write(article.text)
                    
            except Exception as e:
                st.error("요약에 실패했습니다.")
                st.caption(f"Error: {e}")

# === [탭 3] 저장소 (DB) ===
with tab3:
    st.header("🗄️ 저장된 뉴스 관리")
    st.caption("영구 저장된 뉴스를 확인하고 엑셀로 내보내거나 삭제할 수 있습니다.")
    
    df = get_saved_news()
    
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다. '뉴스 목록' 탭에서 '💾 저장' 버튼을 눌러보세요.")
    else:
        st.subheader(f"총 {len(df)}건의 스크랩")
        
        df_display = df
