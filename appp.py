import streamlit as st
import feedparser
import urllib.parse
import os
import time
import requests
import urllib3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from newspaper import Article, Config
import nltk
import google.generativeai as genai

# -------------------------------------------
# 0. 초기 설정 및 API 연결
# -------------------------------------------
GOOGLE_API_KEY = "AIzaSyAdnBk6ZdKpxL98LHHaGj9Bjbfk_dX81DA" 

try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    st.error(f"API 키 설정 오류: {e}")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# NLTK 다운로드
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    nltk.download('punkt_tab')

HISTORY_FILE = "seen_titles.txt"

# 세션 상태 초기화 (필수)
if 'selected_article_url' not in st.session_state:
    st.session_state['selected_article_url'] = None
if 'selected_article_title' not in st.session_state:
    st.session_state['selected_article_title'] = None

# -------------------------------------------
# 1. 유틸리티 함수들
# -------------------------------------------
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

# 함수 내 UI 코드를 제거하고 순수하게 RSS만 가져오도록 수정
def fetch_rss_feed(url):
    try:
        feed = feedparser.parse(url)
        return feed
    except Exception as e:
        st.error(f"RSS 로드 실패: {e}")
        return None

# -------------------------------------------
# 2. 사이드바 / 설정 영역
# -------------------------------------------
st.title("💻 실시간 뉴스 모니터링")

# 설정 입력창들을 함수 밖으로 배치
default_keywords = "삼성전자, 엔비디아, 비트코인"
user_input = st.text_area("키워드 입력 (콤마 구분)", value=default_keywords, height=100)
KEYWORDS = [k.strip() for k in user_input.split(',') if k.strip()]

col1, col2 = st.columns(2)
with col1:
    auto_refresh = st.checkbox("자동 새로고침 켜기", value=True)
with col2:
    refresh_interval = st.slider("업데이트 주기 (분)", 5, 60, 15)

if st.button("🗑️ 기록 초기화"):
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
        st.rerun()

st.divider()

# -------------------------------------------
# 3. 메인 로직
# -------------------------------------------
tab1, tab2 = st.tabs(["📢 뉴스 목록", "📝 AI 상세 요약"])

# === [탭 1] 뉴스 목록 ===
with tab1:
    status_container = st.empty() # 상태 표시를 위한 빈 컨테이너
    seen_titles = load_seen_titles()
    grouped_news = {k: [] for k in KEYWORDS}
    new_news_count = 0 
    
    for keyword in KEYWORDS:
        search_query = f"{keyword} when:1h"
        encoded_query = urllib.parse.quote(search_query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        feed = fetch_rss_feed(rss_url)
        
        if not feed or not feed.entries:
            continue

        for entry in feed.entries:
            title = entry.title
            link = entry.link
            nice_date = format_date_kor(entry.get('published', ''))
            
            # 키워드가 제목에 포함된 경우만 필터링
            if keyword.lower() in title.lower():
                grouped_news[keyword].append({
                    "title": title, "link": link, "date": nice_date
                })
                
                if title not in seen_titles:
                    seen_titles.add(title)
                    save_seen_title(title)
                    new_news_count += 1

    # 업데이트 결과 표시
    current_time = get_current_time_str()
    if new_news_count > 0:
        status_container.success(f"🔥 **업데이트 완료 ({current_time})** : {new_news_count}건의 새로운 뉴스!")
    else:
        status_container.info(f"✅ **업데이트 완료 ({current_time})** : 새로운 소식이 없습니다.")

    # 뉴스 카드 출력
    btn_idx = 0 
    for keyword, items in grouped_news.items():
        if items: 
            with st.expander(f"📂 **{keyword}** ({len(items)}건)", expanded=True):
                for item in items:
                    c1, c2, c3 = st.columns([1.5, 4, 1])
                    c1.caption(item['date'])
                    c2.markdown(f"[{item['title']}]({item['link']})")
                    if c3.button("📝 요약", key=f"btn_{btn_idx}"):
                        st.session_state['selected_article_url'] = item['link']
                        st.session_state['selected_article_title'] = item['title']
                        st.rerun() # 탭 이동 안내 대신 즉시 반영
                    btn_idx += 1
                    st.divider()

# === [탭 2] AI 요약 ===
with tab2:
    st.header("📝 Gemini 기사 요약")
    selected_url = st.session_state.get('selected_article_url')
    
    if not selected_url:
        st.info("👈 [뉴스 목록] 탭에서 '요약' 버튼을 눌러주세요.")
    else:
        st.subheader(f"🔍 {st.session_state['selected_article_title']}")
        
        with st.spinner("AI가 분석 중..."):
            try:
                config = Config()
                config.request_timeout = 10
                article = Article(selected_url, language='ko', config=config)
                article.download()
                article.parse()
                
                if article.top_image:
                    st.image(article.top_image, use_container_width=True)

                if len(article.text) < 50:
                    st.warning("본문이 너무 짧습니다.")
                    st.write(article.text)
                else:
                    prompt = f"다음 기사를 [한줄 요약], [3가지 핵심 포인트], [긍부정 분석] 순으로 요약해줘:\n\n{article.text[:3000]}"
                    response = model.generate_content(prompt)
                    st.markdown(response.text)

                with st.expander("원본 본문 보기"):
                    st.write(article.text)
                    
            except Exception as e:
                st.error(f"요약 실패: {e}")

# 자동 새로고침 로직
if auto_refresh:
    time.sleep(2) # 즉시 재실행 방지 (무한루프 방지)
    st.empty() # 화면 유지
    # 실제 운영 시에는 st.empty()와 sleep을 조합한 별도 로직 권장
