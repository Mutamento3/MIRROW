"""Bounded real pixels for Luna; the immutable delivery stays unchanged."""
import base64
import io
from PIL import Image, ImageOps

EXTENSIONS=frozenset(('.png','.jpg','.jpeg','.webp'))


def input_image(path, sha256):
    if path.stat().st_size>20*1024*1024:
        raise ValueError('图片超过质检输入上限')
    with Image.open(path) as original:
        if original.width*original.height>40_000_000:
            raise ValueError('图片像素超过质检输入上限')
        original.load()
        image=ImageOps.exif_transpose(original).convert('RGBA')
        dimensions=image.size
        image.thumbnail((1536,1536),Image.Resampling.LANCZOS)
        buffer=io.BytesIO()
        image.save(buffer,format='PNG')
        return {'type':'image','url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}, {
            'source_sha256':sha256,'width':dimensions[0],'height':dimensions[1],
            'input_width':image.width,'input_height':image.height,'mode':'actual_image',
            'animated':getattr(original,'n_frames',1)>1}
