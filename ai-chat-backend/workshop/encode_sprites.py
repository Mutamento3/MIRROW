"""Encode the supplied open/wink poses as transparent GIFs, without redrawing.

The user supplies the two raster poses. This script only scales/places frames
and encodes animation, so identity and artwork remain the supplied originals.
"""
import argparse
import math
import shutil
from pathlib import Path
from PIL import Image


def encode(open_path, wink_path, output):
    poses = [Image.open(path).convert('RGBA').resize((480, 480), Image.Resampling.LANCZOS) for path in (open_path, wink_path)]
    frames = []
    for index in range(48):
        # 3.36 s loop: a quick supplied wink with a gentle 6px vertical bob.
        source = poses[1 if index in (33, 34) else 0]
        frame = Image.new('RGBA', (500, 500))
        frame.paste(source, (10, 10+round(3*math.sin(index*math.tau/48))))
        rgb = frame.convert('RGB')
        indexed = rgb.quantize(colors=255)
        palette = indexed.getpalette()
        indexed.putpalette((palette+[0,0,0])[:768])
        alpha = frame.getchannel('A').point(lambda value: 255 if value < 100 else 0)
        indexed.paste(255, mask=alpha)
        frames.append(indexed)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=70,
                   loop=0, transparency=255, disposal=2, optimize=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--export', type=Path)
    args = parser.parse_args()
    dest = Path(__file__).parent / 'assets'
    dest.mkdir(exist_ok=True)
    encode(args.source/'ChatGPT 图像 2026年10月3日 18_26_56-1.png', args.source/'ChatGPT 图像 2026年10月3日 18_26_46-4.png', dest/'astra.gif')
    encode(args.source/'ChatGPT 图像 2026年10月3日 18_26_57-3.png', args.source/'ChatGPT 图像 2026年10月3日 18_26_56-2.png', dest/'sol.gif')
    shutil.copyfile(args.source/'阳光下的创意绿洲工作室.png', dest/'background.png')
    shutil.copyfile(args.source/'奇幻木牌与星月藤饰.png', dest/'sign.png')
    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        for name in ('astra.gif', 'sol.gif'):
            shutil.copyfile(dest/name, args.export/name)


if __name__ == '__main__':
    main()
