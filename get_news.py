import pandas as pd
import requests
import pathlib
import re
from bs4 import BeautifulSoup

from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.search import SearchParameters, web_source, news_source, x_source, rss_source

import os
import json
from datetime import datetime, timedelta
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Define your schema
REQUIRED_FIELDS = ["title", "description", "state", "lga", "status"]
OPTIONAL_FIELDS_i = ["incidentDate", "incidentTime"]
OPTIONAL_FIELDS_ii = ["lat", "lng"]
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS_i + OPTIONAL_FIELDS_ii + ["is_duplicate"]


def normalize_news(news_data):
    """
    Normalize list of dicts into a DataFrame with consistent schema.
    Missing fields are filled with None.
    """
    df = pd.DataFrame(news_data)
    for col in ALL_FIELDS:
        if col not in df.columns:
            df[col] = None  # fill missing fields
    # Ensure correct column order
    df = df[ALL_FIELDS]
    return df


def save_news_to_csv(news_data, folder="news_data"):
    """Save current run news into a timestamped CSV"""
    os.makedirs(folder, exist_ok=True)
    curr_dt = datetime.now()
    timestamp = curr_dt.strftime("%Y%m%d_%H")
    exec_month = curr_dt.strftime("%Y-%m")
    filepath = os.path.join(folder, exec_month, f"{timestamp}.csv")
    df = normalize_news(news_data)
    df.to_csv(filepath, index=False)
    print(f"✅ Saved {len(df)} news to {filepath}")
    return filepath


def load_recent_news(folder="news_data", days=5):
    """Load news from the last N days of CSVs"""
    curr_dt = datetime.now()
    exec_month = curr_dt.strftime("%Y-%m")
    
    cutoff = datetime.now() - timedelta(days=days)
    dfs = []
    for fname in os.listdir(os.path.join(folder, exec_month)):
        if fname.endswith(".csv"):
            try:
                file_date = datetime.strptime(fname.split(".")[0], "%Y%m%d_%H")
                if file_date >= cutoff:
                    dfs.append(pd.read_csv(os.path.join(folder, exec_month, fname)))
            except Exception:
                pass
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def deduplicate_news(current_news, past_news, threshold=0.35):
    """Remove duplicates by cosine similarity on title+description"""
    if past_news.empty:
        current_news["is_duplicate"] = False
        return current_news

    combined_past = (
        past_news["title"].fillna("") + " " + past_news["description"].fillna("")
    ).tolist()
    combined_current = (
        current_news["title"].fillna("") + " " + current_news["description"].fillna("")
    ).tolist()

    vectorizer = TfidfVectorizer().fit(combined_past + combined_current)
    past_vecs = vectorizer.transform(combined_past)
    curr_vecs = vectorizer.transform(combined_current)

    duplicates = []
    for i, vec in enumerate(curr_vecs):
        sim = cosine_similarity(vec, past_vecs).max()
        print(sim)
        duplicates.append(sim >= threshold)

    current_news["is_duplicate"] = duplicates
    return current_news

def publish_news(api_key: str, news_items: list):
    """
    Publishes news items to the Convex threats API.

    Args:
        api_key (str): Your Convex API key (string starting with stmp_...).
        news_items (list): A list of dicts containing threat data. 
                           Must include required fields:
                           title, description, state, lga, status
                           Optional fields: incidentDate, incidentTime, lat, lng
    """
    url = f"https://fantastic-mammoth-699.convex.site/api/threats?api_key={api_key}"
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, headers=headers, json=news_items)
        response.raise_for_status()
        print("✅ Successfully published news!")
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP error occurred: {http_err} - {response.text}")
    except Exception as err:
        print(f"❌ Other error occurred: {err}")

def fetch_security_news(api_key: str):
    """
    Fetch security-related news in Nigeria from the last 8 hours.
    
    Args:
        api_key (str): Your XAI API key.

    Returns:
        list | dict: Parsed JSON object with news items if successful, 
                     or None if parsing fails.
    """
    client = Client(api_key=api_key)

    # Dynamic dates for last 8 hours
    from_date = datetime.now() - timedelta(hours=8)
    to_date = datetime.now()

    search_config = SearchParameters(
        mode="on",
        return_citations=True,
        from_date=from_date,
        to_date=to_date,
        max_search_results=30,
        sources=[
            web_source(country="NG"),
            news_source(country="NG"),
            x_source(),
            rss_source(links=[
                'https://news.google.com/rss/search?q=nigeria+security&hl=en-NG&gl=NG&ceid=NG:en'
            ])
        ]
    )

    chat = client.chat.create(
        model="grok-4-fast-reasoning-latest",
        messages=[user(
            """I need news from the last 8 hours related to security issues such as banditry, gunmen or any kind on violent activities
            in Nigeria. I need the output json formatted. For each news, return like this:
            {"title": "<title>", 
             "description":"<summary>", 
             "state":"<state of occurrence>", 
             "lga":"<Please try to infer the lga from the news, the lga is compulsory>", 
             "incidentDate":"<date>", 
             "incidentTime":"<time if available>",
             "status":"High" or "Medium" or "Low"}.
            If any of the fields isn't available return Null, but for date or time, 
            return current date and for time return 00:00."""
        )],
        search_parameters=search_config
    )

    response = chat.sample()

    try:
        news_data = json.loads(response.content)  # Assumes valid JSON array/object
        return news_data
    except json.JSONDecodeError:
        print("⚠️ Content is not valid JSON—check the raw output instead.")
        return None

def quick_replace(val):
    if val==None:
        return "Somewhere"
    else:
        return val

def filter_and_publish(news_data, api_key, folder="news_data"):
    """Workflow to save, deduplicate, and publish only unique news"""

    # Load past 5 days
    past_news = load_recent_news(folder, days=5)
    display(past_news)

    # Save current
    filepath = save_news_to_csv(news_data, folder)

    # Deduplicate
    current_df = normalize_news(news_data)
    deduped = deduplicate_news(current_df, past_news)

    # Filter unique
    unique_news = deduped[deduped["is_duplicate"] == False]
    unique_news['lga'] = unique_news['lga'].apply(quick_replace)
    unique_news=unique_news[REQUIRED_FIELDS + OPTIONAL_FIELDS_i]

    print(f"📊 Found {len(unique_news)} unique news out of {len(current_df)}")

    if not unique_news.empty:
        print(unique_news.to_dict(orient="records"))
        publish_news(api_key, unique_news.to_dict(orient="records"))
    else:
        print("⚠️ No unique news to publish.")


# Example integration after you parse your API response
try:    
    grok_api_key = os.getenv("XAI_API_KEY")
    convex_api_key = os.getenv("CONVEX_API_KEY")
    
    news_data = fetch_security_news(grok_api_key)
    
    if isinstance(news_data, list) and news_data:
        filter_and_publish(news_data, api_key=convex_api_key)
    else:
        print("⚠️ No news items returned.")
except json.JSONDecodeError:
    print("❌ Could not parse response content as JSON.")
