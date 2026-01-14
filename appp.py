import streamlit as st
import feedparser
import urllib.parse
import os
import time
import requests
import urllib3
import sqlite3  # DB 사용을 위해 추가
import pandas as pd  # 데이터 관리를 위해 추가
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    nltk.download('punkt_tab')

HISTORY_FILE = "seen_titles.txt"
DB_FILE = "news_database.db" # DB 파일명

# -------------------------------------------
# 1. 유틸리티 및 DB 함수들
# -------------------------------------------

# [DB 관련 함수 추가] 
def init_db():
    """DB 테이블 초기화"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 이미 존재하면 건너뜀
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
    """뉴스 DB 저장"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 중복 체크 (제목과 링크가 같으면 저장 안 함)
    c.execute("SELECT id FROM saved_news WHERE title = ? AND link = ?", (title, link))
    if c.fetchone():
        conn.close()
        return False # 이미 존재함
    
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO saved_news (keyword, title, link, pub_date, saved_at) VALUES (?, ?, ?, ?, ?)",
              (keyword, title, link, pub_date, saved_at))
    conn.commit()
    conn.close()
    return True

def get_saved_news():
    """저장된 뉴스 불러오기 (최신순)"""
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM saved_news ORDER BY saved_at DESC", conn)
    conn.close()
    return df

def delete_news_from_db(news_ids):
    """뉴스 삭제"""
    if not news_ids: return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 리스트를 튜플 문자열로 변환하여 쿼리 실행
    placeholders = ', '.join('?' for _ in news_ids)
    c.execute(f"DELETE FROM saved_news WHERE id IN ({placeholders})", news_ids)
    conn.commit()
    conn.close()

# 앱 시작 시 DB 초기화 실행
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
    # 현재 시간을 한국 시간(KST)으로 가져오기
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST) 
    return now.strftime("%Y년 %m월 %d일 %H시 %M분 %S초")

def fetch_rss_feed(url):
    try:
        response = requests.get(url, timeout=10, verify=False)
        return feedparser.parse(response.content)
    except Exception as e:
        return None

# -------------------------------------------
# 2. 화면 구성 (UI)
# -------------------------------------------
st.set_page_config(page_title="실시간 뉴스 모니터링", page_icon="💻", layout="wide")

if 'selected_article_url' not in st.session_state:
    st.session_state['selected_article_url'] = None
if 'selected_article_title' not in st.session_state:
    st.session_state['selected_article_title'] = None

with st.sidebar:
    st.header("⚙️ 모니터링 설정")
    default_keywords = "롯데마트, 롯데웰푸드, [단독]롯데, 롯데칠성, 세븐일레븐, 식약처, 식중독, 리콜"
    user_input = st.text_area("키워드 입력 (콤마 구분)", value=default_keywords, height=100)
    
    KEYWORDS = [k.strip() for k in user_input.split(',') if k.strip()]
    
    st.divider()
    
    st.subheader("⏱️ 자동 업데이트")
    auto_refresh = st.checkbox("자동 새로고침 켜기", value=True)
    refresh_interval = st.slider("업데이트 주기 (분)", 5, 60, 15)
    
    if st.button("🗑️ 수동 업데이트"):
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
            st.rerun()

st.title("💻 실시간 뉴스 모니터링")

# -------------------------------------------
# 3. 메인 로직
# -------------------------------------------
# [변경] 탭을 3개로 늘림
tab1, tab2, tab3 = st.tabs(["📢 뉴스 목록", "📝 AI 상세 요약", "🗄️ DB 저장"])

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
                        # [변경] 버튼 배치를 위해 컬럼 조정 (저장 버튼 추가)
                        c1, c2, c3, c4 = st.columns([1.2, 3.5, 0.8, 0.8])
                        
                        c1.markdown(f":orange[{item['date']}]")
                        c2.markdown(f"[{item['title']}]({item['link']})")
                        
                        # AI 요약 버튼
                        if c3.button("📝 요약", key=f"btn_sum_{btn_idx}"):
                            st.session_state['selected_article_url'] = item['link']
                            st.session_state['selected_article_title'] = item['title']
                            st.toast("탭 2로 이동하세요!", icon="👉")
                        
                        # [추가] DB 저장 버튼
                        if c4.button("💾 저장", key=f"btn_save_{btn_idx}"):
                            success = save_news_to_db(keyword, item['title'], item['link'], item['date'])
                            if success:
                                st.toast("저장소(DB)에 저장되었습니다!", icon="✅")
                            else:
                                st.toast("이미 저장된 뉴스입니다.", icon="⚠️")
                                
                        btn_idx += 1
                    st.divider()

# === [탭 2] AI 요약 (수정됨) ===
with tab2:
    st.header("📝 Gemini 기사 요약")
    selected_url = st.session_state['selected_article_url']
    
    if selected_url is None:
        st.info("👈 [뉴스 목록] 탭에서 'AI 요약' 버튼을 먼저 눌러주세요.")
    else:
        st.subheader(f"{st.session_state['selected_article_title']}")
        st.markdown("---")
        
        with st.spinner("🔗 실제 기사 주소를 찾는 중..."):
            # [중요] 여기서 구글 주소를 실제 언론사 주소로 바꿉니다.
            final_url = get_final_url(selected_url)
        
        # 실제 주소로 요약 시도
        with st.spinner(f"Gemini가 기사를 읽고 분석 중입니다... ({final_url})"):
            try:
                config = Config()
                config.request_timeout = 10
                # 브라우저처럼 보이게 헤더 설정
                config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                config.request_kwargs = {'verify': False}
                
                # 변환된 final_url 사용
                article = Article(final_url, language='ko', config=config)
                article.download()
                article.parse()
                
                if article.top_image:
                    st.image(article.top_image, use_container_width=True)

                if len(article.text) < 50:
                    st.warning("⚠️ 본문을 가져오지 못했습니다. (보안이 강한 언론사이거나 유료 기사일 수 있습니다)")
                    st.write(f"원본 링크: {final_url}")
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
# === [탭 3] 저장소 (신규 기능) ===
with tab3:
    st.header("🗄️ 저장된 뉴스 관리")
    st.caption("영구 저장된 뉴스를 확인하고 엑셀로 내보내거나 삭제할 수 있습니다.")
    
    # 데이터 로드
    df = get_saved_news()
    
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다. '뉴스 목록' 탭에서 '💾 저장' 버튼을 눌러보세요.")
    else:
        # 데이터프레임 표시 (선택 삭제 기능을 위해 data_editor 사용)
        st.subheader(f"총 {len(df)}건의 스크랩")
        
        # 삭제를 위한 체크박스 컬럼 추가 (UI용)
        df_display = df.copy()
        df_display['삭제선택'] = False
        
        # 컬럼 순서 및 이름 정리
        df_display = df_display[['삭제선택', 'keyword', 'title', 'pub_date', 'saved_at', 'link', 'id']]
        
        edited_df = st.data_editor(
            df_display,
            column_config={
                "삭제선택": st.column_config.CheckboxColumn("선택", help="삭제할 항목 선택"),
                "keyword": "키워드",
                "title": "제목",
                "pub_date": "기사 날짜",
                "saved_at": "저장 일시",
                "link": st.column_config.LinkColumn("링크"),
                "id": None # ID는 숨김
            },
            hide_index=True,
            use_container_width=True
        )
        
        col1, col2 = st.columns([1, 4])
        
        with col1:
            if st.button("🗑️ 선택 항목 삭제", type="primary"):
                # 체크된 항목의 ID 추출
                selected_ids = edited_df[edited_df['삭제선택'] == True]['id'].tolist()
                if selected_ids:
                    delete_news_from_db(selected_ids)
                    st.success(f"{len(selected_ids)}건 삭제 완료!")
                    time.sleep(1) # 잠시 대기 후 리런
                    st.rerun()
                else:
                    st.warning("삭제할 항목을 선택해주세요.")
                    
        with col2:
            # CSV 다운로드 버튼
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 엑셀(CSV)로 다운로드",
                data=csv,
                file_name=f"news_scrap_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

# 자동 새로고침
if auto_refresh:
    time.sleep(refresh_interval * 60)
    st.rerun()


