import streamlit as st
import feedparser
import urllib.parse
import os
import time
import requests
import urllib3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
# plyer는 웹(Streamlit Cloud)에서 작동 안 해서 제외함 (에러 방지)
from newspaper import Article, Config
import nltk
import google.generativeai as genai

# -------------------------------------------
# 0. API 키 및 초기 설정 (여기만 보세요!)
# -------------------------------------------

# 👇 [중요] 아까 그 키를 여기에 따옴표 안에 넣어줘!
GOOGLE_API_KEY = "AIzaSyAdnBk6ZdKpxL98LHHaGj9Bjbfk_dX81DA" 

# Gemini 연결 설정
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # 무료 버전 모델인 flash 사용
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

def fetch_rss_feed(url):
    # try: "response" 부분을 지우고 바로 코드를 시작합니다.
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

st.title("💻 실시간 뉴스 모니터링")

# -------------------------------------------
# 3. 메인 로직
# -------------------------------------------
tab1, tab2 = st.tabs(["📢 뉴스 목록", "📝 AI 상세 요약"])

# === [탭 1] 뉴스 목록 ===
with tab1:
    status_container = st.container()
    
    seen_titles = load_seen_titles()
    grouped_news = {k: [] for k in KEYWORDS}
    new_news_count = 0 
    
    for keyword in KEYWORDS:
        clean_keyword = keyword.strip()
        search_query = clean_keyword + " when:1h"
        encoded_keyword = urllib.parse.quote(search_query)
        rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
        
        feed = fetch_rss_feed(rss_url)
        
        if not feed or not feed.entries:
            continue

        for entry in feed.entries:
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

    # 상태 업데이트
    current_time = get_current_time_str()
    if new_news_count > 0:
        status_container.success(f"🔥 **업데이트 완료 ({current_time})** : {new_news_count}건의 새로운 뉴스!")
        st.toast(f"{new_news_count}건의 새 뉴스가 있습니다!", icon="🔥")
    else:
        status_container.info(f"✅ **업데이트 완료 ({current_time})** : 새로운 뉴스가 없습니다.")

    # 뉴스 카드 출력
    btn_idx = 0 
    for keyword, items in grouped_news.items():
        if items: 
            with st.expander(f"📂 **{keyword}** ({len(items)}건)", expanded=True):
                for item in items:
                    with st.container():
                        c1, c2, c3 = st.columns([1.2, 4, 1])
                        c1.markdown(f":orange[{item['date']}]")
                        c2.markdown(f"[{item['title']}]({item['link']})")
                        if c3.button("📝 AI 요약", key=f"btn_{btn_idx}"):
                            st.session_state['selected_article_url'] = item['link']
                            st.session_state['selected_article_title'] = item['title']
                            st.toast("탭 2로 이동하세요!", icon="👉")
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
        
        with st.spinner("Gemini가 기사를 읽고 분석 중입니다... 🤖"):
            try:
                # 기사 본문 다운로드
                config = Config()
                config.request_timeout = 10
                config.request_kwargs = {'verify': False}
                article = Article(selected_url, language='ko', config=config)
                article.download()
                article.parse()
                
                # 이미지 있으면 표시
                if article.top_image:
                    st.image(article.top_image, use_container_width=True)

                # Gemini에게 요약 요청
                if len(article.text) < 50:
                    st.warning("본문이 너무 짧아 요약할 수 없습니다.")
                    st.write(article.text)
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

# 자동 새로고침
if auto_refresh:
    time.sleep(refresh_interval * 60)
    st.rerun()


이게 전체 코드야 수정해줘

