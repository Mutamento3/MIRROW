"""Separate a stored visual description from the user's image caption."""
import re


def image_caption(content: str) -> str:
    content = re.sub(r'^\[图片消息\]（[\s\S]*?）(?=\r?\n|$)\r?\n?', '', content)
    return re.sub(r'^\[图片消息\]\r?\n?', '', content).strip()


def with_image_description(content: str, description: str) -> str:
    caption = image_caption(content)
    marker = f'[图片消息]（{" ".join(description.split())}）'
    return f'{marker}\n{caption}' if caption else marker
