import { useRef, useState } from 'react';
import { getApiBase } from '../config';
import type { ImageAttachment } from '../types';
import type { LightboxImage } from './ImageLightbox';

type Props = {
  images: ImageAttachment[];
  onOpen: (images: LightboxImage[], index: number) => void;
};

export default function MessageImageCarousel({ images, onOpen }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const lightboxImages = images.map(image => ({
    src: image.url ? (image.url.startsWith('/') ? `${getApiBase()}${image.url}` : image.url) : '',
    filename: image.filename,
    alt: image.filename || '消息图片',
  }));

  const showImage = (index: number) => {
    const track = trackRef.current;
    if (!track || index < 0 || index >= images.length) return;
    track.scrollTo({ left: index * track.clientWidth, behavior: 'smooth' });
    setActiveIndex(index);
  };

  return (
    <div className="message-images message-image-carousel" aria-label={`共 ${images.length} 张图片`}>
      <div className="message-image-carousel-track" ref={trackRef} onScroll={event => {
        const track = event.currentTarget;
        if (track.clientWidth > 0) setActiveIndex(Math.max(0, Math.min(images.length - 1, Math.round(track.scrollLeft / track.clientWidth))));
      }}>
        {lightboxImages.map((image, index) => (
          <div className="message-image-carousel-slide" key={images[index].id || index}>
            {image.src ? (
              <button type="button" className="message-image-carousel-preview" onClick={() => {
                const availableImages = lightboxImages.filter(item => item.src);
                const selectedIndex = lightboxImages.slice(0, index).filter(item => item.src).length;
                onOpen(availableImages, selectedIndex);
              }} aria-label={`查看第 ${index + 1} 张大图，共 ${images.length} 张`}>
                <img src={image.src} alt={image.alt} loading="lazy" />
              </button>
            ) : (
              <div className="message-image-placeholder">
                <span className="image-placeholder-icon">🖼️</span>
                <span className="image-placeholder-name">{image.filename}</span>
              </div>
            )}
          </div>
        ))}
      </div>
      <span className="message-image-carousel-count" aria-live="polite">{activeIndex + 1} / {images.length}</span>
      {activeIndex > 0 && <button type="button" className="message-image-carousel-arrow previous" onClick={() => showImage(activeIndex - 1)} aria-label="上一张图片">‹</button>}
      {activeIndex < images.length - 1 && <button type="button" className="message-image-carousel-arrow next" onClick={() => showImage(activeIndex + 1)} aria-label="下一张图片">›</button>}
    </div>
  );
}
