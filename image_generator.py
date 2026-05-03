import os
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
import logging

def create_infographic(df: pd.DataFrame, scan_name="3:15 PM", output_path="generated_infographic.jpg"):
    template_path = os.path.join("Support Files", "FinFunda-transparent.jpg")
    
    if not os.path.exists(template_path):
        logging.error(f"Template image not found at {template_path}")
        return None
        
    try:
        # Load template and ensure we can draw with alpha (transparency)
        base = Image.open(template_path).convert("RGBA")
        
        # We will create an overlay for the transparent box
        overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        
        # Attempt to load a bold Windows font
        font_paths = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "C:/Windows/Fonts/tahoma.ttf"
        ]
        
        title_font = None
        text_font = None
        
        for path in font_paths:
            if os.path.exists(path):
                title_font = ImageFont.truetype(path, 42)
                text_font = ImageFont.truetype(path, 32)
                break
                
        if not title_font:
            title_font = ImageFont.load_default()
            text_font = ImageFont.load_default()
            
        # Determine layout (Center-Right)
        # Image is 1584x672
        box_x0 = 850
        box_y0 = 120
        box_x1 = 1500
        
        # Calculate dynamic height based on number of stocks (max 5)
        top_stocks = df.head(5)
        box_y1 = box_y0 + 80 + (len(top_stocks) * 70) + 40
        
        # Draw semi-transparent black background box for readability
        draw.rounded_rectangle(
            [box_x0, box_y0, box_x1, box_y1],
            radius=15,
            fill=(0, 0, 0, 180) # Black with ~70% opacity
        )
        
        # Text layout
        text_draw = ImageDraw.Draw(overlay)
        
        # Draw Title
        title_text = f"🚀 TOP {scan_name.upper()} PICKS"
        text_draw.text((box_x0 + 40, box_y0 + 30), title_text, font=title_font, fill=(255, 215, 0, 255)) # Gold color
        
        # Draw Stocks
        y_offset = box_y0 + 110
        for _, row in top_stocks.iterrows():
            ticker = str(row['Ticker'])
            ltp = str(row.get('LTP', row.get('Close', 'N/A')))
            target = str(row.get('Target', 'N/A'))
            sl = str(row.get('Stop Loss', 'N/A'))
            
            # Formatting the line
            line_text = f"▪ {ticker[:10]:<10} | Price: ₹{ltp}"
            text_draw.text((box_x0 + 40, y_offset), line_text, font=text_font, fill=(255, 255, 255, 255))
            
            # Second line for targets to fit nicely
            sub_text = f"   Tgt: ₹{target}  |  SL: ₹{sl}"
            text_draw.text((box_x0 + 40, y_offset + 35), sub_text, font=text_font, fill=(180, 255, 180, 255)) # Light green
            
            y_offset += 70
            
        # Combine the base image with the text overlay
        out = Image.alpha_composite(base, overlay)
        
        # Convert back to RGB to save as JPEG
        out = out.convert("RGB")
        out.save(output_path, "JPEG", quality=95)
        logging.info(f"Infographic generated at {output_path}")
        return output_path
        
    except Exception as e:
        logging.error(f"Failed to generate infographic: {e}")
        return None
