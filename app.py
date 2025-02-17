from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import requests
from dotenv import load_dotenv
from flask_cors import CORS  # pip install flask-cors
from apscheduler.schedulers.background import BackgroundScheduler
from newspaper import Article  # pip install newspaper3k
from bs4 import BeautifulSoup  # pip install beautifulsoup4
import html
import re

load_dotenv()

general_api_key = os.getenv('NEWS_API_KEY')

if not general_api_key:
    print("No general API key found. Please set NEWS_API_KEY in your .env file.")
else:
    print(f"Loaded General API Key: {general_api_key}")

app = Flask(__name__)
CORS(app)

# Mapping regions (excluding Africa) to ISO country codes for dynamic source retrieval.
REGION_COUNTRIES = {
    'europe': ['gb', 'fr', 'de', 'it', 'es', 'ie', 'nl'],
    'asia': ['in', 'cn', 'jp', 'kr', 'sg'],
    'north-america': ['us', 'ca', 'mx'],
    'south-america': ['br', 'ar', 'co', 'cl', 'pe']
}

# Mapping regions to target language for translation.
REGION_TARGET_LANG = {
    'europe': 'en',
    'asia': 'en',
    'north-america': 'en',
    'south-america': 'es'
}

def clean_content(text):
    """
    Removes duplicate lines from text.
    Splits by newline, and only keeps the first occurrence of each non-empty line.
    """
    if not text:
        return ""
    lines = text.splitlines()
    seen = set()
    unique_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique_lines.append(line)
    return "\n".join(unique_lines)

def normalize_title(title):
    """Normalize title by removing non-alphanumeric characters and lowercasing."""
    if not title:
        return ""
    return re.sub(r'\W+', '', title).lower()

def contains_multimedia_or_login_or_live(text):
    """
    Check if the text contains multimedia, video, login requirements, or is a live page.
    Returns False if text is None.
    """
    if not text:
        return False
    keywords = [
        'video', 'watch', 'player', 'play', 'stream', 'log in to comment', 'join in on the fun',
        'this live page is now closed', 'live updates', 'follow our live coverage'
    ]
    return any(keyword in text.lower() for keyword in keywords)

def get_sources_by_region(region):
    """
    For regions other than Africa, retrieve an up‑to‑date list of source IDs from NewsAPI's
    /v2/top-headlines/sources endpoint filtering by country using REGION_COUNTRIES.
    Excludes any source whose id contains 'google-news'.
    """
    country_codes = REGION_COUNTRIES.get(region, [])
    url = f"https://newsapi.org/v2/top-headlines/sources?apiKey={general_api_key}"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Error fetching sources: {response.text}")
        return []
    sources = response.json().get("sources", [])
    filtered_ids = [
        source["id"] for source in sources
        if source.get("country") in country_codes and source.get("id") and "google-news" not in source.get("id")
    ]
    return filtered_ids

def fetch_news(region):
    """
    Fetches news based on the selected region.
    Ensures exactly 12 articles (if available) are returned by iterating through pages,
    and skips articles with insufficient or duplicate content.
    """
    max_articles = 12
    filtered_articles = []
    seen_titles = set()
    page = 1
    max_pages = 3  # Maximum pages to try
    
    while len(filtered_articles) < max_articles and page <= max_pages:
        source_ids = get_sources_by_region(region)
        if not source_ids:
            print(f"No valid sources found for region: {region}")
            break
        url = f"https://newsapi.org/v2/top-headlines?sources={','.join(source_ids)}&pageSize=12&page={page}&apiKey={general_api_key}"
        
        print(f"Fetching URL for {region} (page {page}): {url}")
        response = requests.get(url)
        print(f"Response Status: {response.status_code}")
        if response.status_code != 200:
            print(f"Error fetching news for {region}: {response.text}")
            break
        
        articles = response.json().get('articles', [])
        if not articles:
            break
        
        for article in articles:
            if len(filtered_articles) >= max_articles:
                break
            article_url = article.get('url', '')
            if not article_url:
                continue
            
            # For North America, if the article URL contains aljazeera.com, replace it with cnn.com
            if region == 'north-america' and "aljazeera.com" in article_url:
                article_url = article_url.replace("aljazeera.com", "cnn.com")
            
            try:
                news_article = Article(article_url)
                news_article.download()
                news_article.parse()
                content = news_article.text or ""
                if not content.strip():
                    content = article.get("content", "") or ""
                # Clean content to remove repeated lines.
                content = clean_content(content)
                # Check if the content has at least 4 non-empty lines.
                non_empty_lines = [line for line in content.splitlines() if line.strip()]
                if len(non_empty_lines) < 4:
                    print(f"Skipping article as content has less than 4 lines: {article.get('title', 'No Title')}")
                    continue
            except Exception as e:
                print(f"Skipping article due to error: {e}")
                continue
            
            if contains_multimedia_or_login_or_live(content):
                continue
            
            normalized = normalize_title(article.get("title", ""))
            if normalized in seen_titles:
                print(f"Skipping duplicate article: {article.get('title', 'No Title')}")
                continue
            seen_titles.add(normalized)
            
            filtered_articles.append({
                "title": html.unescape(article.get("title", "No Title")),
                "url": article_url,
                "api_url": article_url
            })
        page += 1
    
    if len(filtered_articles) < max_articles:
        print(f"Warning: Only {len(filtered_articles)} articles found for region {region}.")
    return filtered_articles

def translate_text(text, target_lang):
    """
    Translates text using the LibreTranslate API.
    If the API call fails, returns the original text.
    """
    url = "https://libretranslate.de/translate"
    payload = {
        "q": text,
        "source": "auto",
        "target": target_lang,
        "format": "text"
    }
    try:
        r = requests.post(url, data=payload)
        if r.status_code == 200:
            return r.json().get("translatedText", text)
        else:
            print(f"Translation API error: {r.text}")
            return text
    except Exception as e:
        print(f"Error in translation: {e}")
        return text

@app.route('/favicon.ico')
def favicon():
    """Serve the favicon from the static folder."""
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/', methods=['GET', 'POST'])
def home():
    region = request.values.get('region', 'europe')
    news = fetch_news(region)
    available_regions = list(REGION_COUNTRIES.keys())
    return render_template('index.html', news=news, regions=available_regions)

@app.route('/fetch_article', methods=['GET'])
def fetch_article():
    """Fetches full content of an article using newspaper3k and auto-translates it based on region."""
    article_url = request.args.get('url')
    region = request.args.get('region', 'europe')
    if not article_url:
        return jsonify({'error': 'Invalid URL'}), 400

    # For North America, replace aljazeera.com with cnn.com in the article URL
    if region == 'north-america' and "aljazeera.com" in article_url:
        article_url = article_url.replace("aljazeera.com", "cnn.com")

    if '/videos/' in article_url:
        return jsonify({'error': 'This is a video article. Please view it on the website.'}), 400

    try:
        article = Article(article_url)
        article.download()
        article.parse()
        content = article.text or ""
        content = clean_content(content)
        # Check if content has at least 4 non-empty lines
        non_empty_lines = [line for line in content.splitlines() if line.strip()]
        if len(non_empty_lines) < 4:
            return jsonify({'error': 'Article content is too short to display.'}), 400
        
        if contains_multimedia_or_login_or_live(content):
            return jsonify({'error': 'This article contains multimedia, video, login requirements, or is a live page and cannot be displayed.'}), 400
        
        target_lang = REGION_TARGET_LANG.get(region, 'en')
        translated_content = translate_text(content, target_lang)
        return jsonify({'full_content': translated_content})
    except Exception as e:
        print(f"Error fetching article: {str(e)}")
        return jsonify({'error': f'Failed to fetch content: {str(e)}'}), 500

def scheduled_task():
    """Scheduled task to fetch news periodically."""
    all_regions = list(REGION_COUNTRIES.keys())
    for region in all_regions:
        news = fetch_news(region)
        print(f"News for {region.capitalize()}:")
        for article in news:
            print(f"- {article['title']}")
        print("")

if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_task, 'interval', hours=24)
    scheduler.start()
    app.run(debug=True)
#