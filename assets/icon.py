"""Generate Sentivo Tools app icon — amber S on dark background"""
from PIL import Image, ImageDraw, ImageFont
import os

def create_icon():
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    
    for size in sizes:
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Dark background circle
        margin = size // 10
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill=(17, 17, 17, 255)  # #111111
        )
        
        # Amber border
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            outline=(212, 168, 67, 255),  # #D4A843
            width=max(1, size // 20)
        )
        
        # "S" letter in center
        font_size = int(size * 0.55)
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()
        
        text = "S"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size - text_width) // 2 - bbox[0]
        y = (size - text_height) // 2 - bbox[1]
        
        draw.text((x, y), text, fill=(212, 168, 67, 255), font=font)
        images.append(img)
    
    os.makedirs('assets', exist_ok=True)
    # Save from largest frame; Pillow embeds each requested size
    images[-1].save(
        'assets/icon.ico',
        format='ICO',
        sizes=[(s, s) for s in sizes],
    )
    print("Icon created: assets/icon.ico")

if __name__ == "__main__":
    create_icon()
