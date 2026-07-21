import time
import requests
import json
import pandas as pd
import logging
import os

def analyze_stocks(df, gemini_api_key, strategy_name="Swing"):
    """
    Sends the shortlisted stocks DataFrame to Gemini AI for conviction analysis.
    Uses direct REST API calls to bypass SDK versioning issues.
    """
    if df.empty:
        return "No stocks to analyze."
        
    # Format dataframe to string representation
    data_str = df.to_markdown(index=False)
    
    if "Bearish" in strategy_name:
        prompt = f"""
You are an expert quantitative intraday short-seller. I have run my automated scanner for Bearish Breakdowns.
The scanner identifies stocks that are structurally weak (Price < 50 EMA, RSI < 45), have opened with a gap down, are trading below VWAP, and are in the lower 25% of their opening range.

Here are the stocks that passed the scan today, along with their technical metrics:

{data_str}

Analyze these shortlisted stocks purely based on the provided technical metrics. 
I want you to pick the TOP 3 highest conviction SHORT trade setups from this list for an intraday breakdown trade.

Provide your response in this format:
### Top Short Candidate 1: [Ticker]
**Rationale:** (Brief explanation of why the combination of technical metrics - especially the Gap, VWAP position, and RSI - makes this the strongest short setup).

### Top Short Candidate 2: [Ticker]
**Rationale:** ...

### Top Short Candidate 3: [Ticker]
**Rationale:** ...

Keep your analysis concise, professional, and actionable. Do not provide general trading disclaimers, just the analysis.
"""
    else:
        prompt = f"""
You are an expert quantitative swing trader. I have run my automated scanner on the Nifty 500 universe.
The scanner filters for stocks with strong trend alignment, positive momentum (RSI), and significant volume breakout.

Here are the stocks that passed the scan today, along with their technical metrics:

{data_str}

Analyze these shortlisted stocks purely based on the provided technical metrics. 
I want you to pick the TOP 1 or 2 highest conviction trade setups from this list for an 8-10 day swing trade.

Provide your response in this format:
### Top Conviction Pick 1: [Ticker]
**Rationale:** (Brief explanation of why the combination of technical metrics makes this the strongest setup).

### Top Conviction Pick 2: [Ticker] (Optional)
**Rationale:** ...

Keep your analysis concise, professional, and actionable. Do not provide general trading disclaimers, just the analysis.
"""


    # Using only verified models provided by the user
    max_retries = 5
    models_to_try = [
        'gemini-2.0-flash',
        'gemini-pro-latest',
        'gemini-flash-latest',
        'gemini-2.0-flash-lite',
        'gemini-2.5-flash',
        'gemini-2.5-pro',
        'gemini-3-pro-preview'
    ]
    
    last_error = "None"
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={gemini_api_key}"
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }]
                }
                headers = {'Content-Type': 'application/json'}
                
                response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
                res_data = response.json()
                
                if response.status_code == 200:
                    return res_data['candidates'][0]['content']['parts'][0]['text']
                else:
                    last_error = res_data.get('error', {}).get('message', response.text)
                    
                    # If model not found (404), try next model
                    if response.status_code == 404:
                        logging.warning(f"Model {model_name} not found. Trying next...")
                        break
                        
                    # Handle rate limits (429) or temporary errors (503)
                    if response.status_code in [429, 503, 500]:
                        # If quota is hit (429), try breaking to next model
                        if response.status_code == 429:
                            logging.warning(f"Quota exceeded for {model_name}. Trying next model...")
                            break 
                            
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            logging.warning(f"Gemini API busy ({response.status_code}). Retrying in {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                    
                    # For other errors, just report it
                    return f"The AI analysis service is temporarily busy. (Status: {response.status_code}, Error: {last_error})"

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return f"Error connecting to AI service: {e}"
                
    return f"Error: All AI models are currently unavailable. Last Error: {last_error}"

def analyze_active_positions(prompt, gemini_api_key):
    """
    Sends the compiled active position analysis prompt to Gemini AI and returns actionable advice.
    """
    max_retries = 5
    models_to_try = [
        'gemini-2.0-flash',
        'gemini-pro-latest',
        'gemini-flash-latest',
        'gemini-2.0-flash-lite',
        'gemini-2.5-flash',
        'gemini-2.5-pro',
        'gemini-3-pro-preview'
    ]
    
    last_error = "None"
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={gemini_api_key}"
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }]
                }
                headers = {'Content-Type': 'application/json'}
                
                response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
                res_data = response.json()
                
                if response.status_code == 200:
                    return res_data['candidates'][0]['content']['parts'][0]['text']
                else:
                    last_error = res_data.get('error', {}).get('message', response.text)
                    
                    if response.status_code == 404:
                        break
                        
                    if response.status_code in [429, 503, 500]:
                        if response.status_code == 429:
                            break
                            
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 3
                            time.sleep(wait_time)
                            continue
                            
                    return f"The AI analysis service is temporarily busy. (Status: {response.status_code}, Error: {last_error})"

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return f"Error connecting to AI service: {e}"
                
    return f"Error: All AI models are currently unavailable. Last Error: {last_error}"

SETTINGS_FILE = os.path.join("data", "state", ".ai_advisor_settings.json")

def is_ai_advisor_enabled() -> bool:
    """Helper to check if the AI Active Positions Advisor monitor is enabled (disabled globally)."""
    return False

def set_ai_advisor_enabled(enabled: bool):
    """Helper to persistent-save the toggle state of the AI Advisor."""
    import json
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"enabled": enabled}, f)
    except Exception as e:
        import logging
        logging.error(f"Failed to save AI Advisor settings: {e}")
