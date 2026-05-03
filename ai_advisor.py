from google import genai
import pandas as pd
import logging

def analyze_stocks(df, gemini_api_key):
    """
    Sends the shortlisted stocks DataFrame to Gemini AI for conviction analysis.
    """
    if df.empty:
        return "No stocks to analyze."
        
    try:
        # Initialize the new SDK client
        client = genai.Client(api_key=gemini_api_key)
        
        # Format dataframe to string representation
        data_str = df.to_markdown(index=False)
        
        prompt = f"""
You are an expert quantitative swing trader. I have run my automated scanner on the Nifty 500 universe exactly at 3:15 PM IST.
The scanner applies the following filters:
1. Trend: Last Traded Price (LTP) is above the 50-day and 200-day EMA.
2. Momentum: 14-period Daily RSI is between 60 and 80.
3. Volume Anomaly: Today's volume is > 1.5x the 20-day average.
4. Closing Conviction: The LTP is within 2% of the day's High.

Here are the stocks that passed the scan today, along with their technical metrics:

{data_str}

Analyze these shortlisted stocks purely based on the provided technical metrics (Volume Spike Ratio, % Gain, RSI). 
I want you to pick the TOP 1 or 2 highest conviction trade setups from this list for an 8-10 day swing trade.

Provide your response in this format:
### Top Conviction Pick 1: [Ticker]
**Rationale:** (Brief explanation of why the combination of % Gain, Volume Spike, and RSI makes this the strongest setup).

### Top Conviction Pick 2: [Ticker] (Optional)
**Rationale:** ...

Keep your analysis concise, professional, and actionable. Do not provide general trading disclaimers, just the analysis.
"""
        # Call the new endpoint using the latest recommended fast model
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text
        
    except Exception as e:
        logging.error(f"Error generating AI analysis: {e}")
        return f"Failed to generate AI analysis. Please ensure your Gemini API key is valid. Error: {e}"
