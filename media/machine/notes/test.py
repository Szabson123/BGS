from PIL import Image, ImageOps, ImageFilter

def optimize_image(input_path, output_path, max_width=2560, quality=80):
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        
        exif_data = img.info.get("exif")

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = int(float(img.height) * float(ratio))
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
        
        save_kwargs = {
            "quality": 65, 
            "method": 6,
            "subsampling": 2,
            "optimize": True,
        }

        if exif_data:
            save_kwargs["exif"] = exif_data

        img.save(output_path, "WEBP", **save_kwargs)

        if exif_data:
            save_kwargs["exif"] = exif_data
        
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
        img.save(output_path, "WEBP", **save_kwargs)

optimize_image("oryginal.jpg", "skompresowane.webp")