import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { getApiBase } from '../config';
import './ImageLightbox.css';

export type LightboxImage = { src: string; filename?: string; alt?: string };

type ImageLightboxProps = {
  src: string;
  filename?: string;
  alt?: string;
  images?: LightboxImage[];
  initialIndex?: number;
  onClose: () => void;
  onLocateMessage?: () => void;
  galleryCategory?: 'daily' | 'night' | 'unknown';
  onGalleryCategoryChange?: (category: 'daily' | 'night') => void;
  galleryCategorySaving?: boolean;
  galleryCategoryError?: string;
};

type MobileToolResult = { success?: boolean; content?: string };
type MobileTool = { execute(args: { tool: string; params: string }): Promise<MobileToolResult> };
type CapacitorWindow = Window & { Capacitor?: { Plugins?: { MobileTool?: MobileTool } } };

function extensionForMime(mime: string): string {
  if (mime === 'image/jpeg') return 'jpg';
  if (mime === 'image/webp') return 'webp';
  if (mime === 'image/gif') return 'gif';
  return 'png';
}

function safeFilename(source: string | undefined, mime: string): string {
  const fallback = `MIRROW_${new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '')}`;
  let name = source || '';
  try {
    if (!name) name = new URL(source || '', window.location.href).pathname.split('/').pop() || '';
  } catch { /* use fallback */ }
  name = (name || fallback)
    .replace(/[\\/:*?"<>|]/g, '_')
    .split('')
    .map(char => char.charCodeAt(0) < 32 ? '_' : char)
    .join('')
    .slice(0, 120);
  if (!/\.(png|jpe?g|webp|gif)$/i.test(name)) name += `.${extensionForMime(mime)}`;
  return name;
}

function blobAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('读取图片失败'));
    reader.readAsDataURL(blob);
  });
}

function dataUrlAsBlob(dataUrl: string): Blob {
  const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\s]+)$/i.exec(dataUrl);
  if (!match) throw new Error('历史图片数据格式无法识别');
  const binary = window.atob(match[2].replace(/\s/g, ''));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return new Blob([bytes], { type: match[1].toLowerCase() });
}

async function readImageBlob(src: string): Promise<Blob> {
  // Older MIRROW tool records embedded screenshots as data URLs. Android
  // WebView can render them in <img> but fetch(data:) is not reliable.
  if (src.startsWith('data:')) return dataUrlAsBlob(src);
  try {
    const url = new URL(src, window.location.href);
    if (url.pathname.startsWith('/static/generated_images/') || url.pathname.startsWith('/static/chat_images/')) {
      const response = await fetch(`${getApiBase()}/api/local-images/data?path=${encodeURIComponent(url.pathname)}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `读取历史图片失败（${response.status}）`);
      if (typeof body.data_url !== 'string') throw new Error('历史图片读取接口未返回图片数据');
      return dataUrlAsBlob(body.data_url);
    }
  } catch (cause) {
    if (cause instanceof Error && !cause.message.includes('Invalid URL')) throw cause;
  }
  const response = await fetch(src);
  if (!response.ok) throw new Error(`读取图片失败（${response.status}）`);
  return response.blob();
}

export default function ImageLightbox({
  src,
  filename,
  alt = '图片预览',
  images,
  initialIndex = 0,
  onClose,
  onLocateMessage,
  galleryCategory,
  onGalleryCategoryChange,
  galleryCategorySaving = false,
  galleryCategoryError = '',
}: ImageLightboxProps) {
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle');
  const [error, setError] = useState('');
  const [index, setIndex] = useState(initialIndex);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const imageCount = images?.length || 1;
  const currentImage = images?.[index] || { src, filename, alt };
  const currentSrc = currentImage.src;
  const displayName = useMemo(() => currentImage.filename || currentSrc.split(/[?#]/, 1)[0].split('/').pop() || '', [currentImage.filename, currentSrc]);

  useEffect(() => { setIndex(initialIndex); }, [src, initialIndex]);

  const moveImage = (direction: number) => {
    setIndex(current => Math.max(0, Math.min(imageCount - 1, current + direction)));
    setStatus('idle');
    setError('');
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (imageCount > 1 && event.key === 'ArrowLeft') {
        event.preventDefault();
        setIndex(current => Math.max(0, current - 1));
        setStatus('idle');
        setError('');
      }
      if (imageCount > 1 && event.key === 'ArrowRight') {
        event.preventDefault();
        setIndex(current => Math.min(imageCount - 1, current + 1));
        setStatus('idle');
        setError('');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, imageCount]);

  const saveImage = async () => {
    if (status === 'saving') return;
    setStatus('saving');
    setError('');
    try {
      const blob = await readImageBlob(currentSrc);
      if (!blob.type.startsWith('image/')) throw new Error('返回内容不是图片');
      const finalName = safeFilename(displayName, blob.type);
      const mobileTool = (window as CapacitorWindow).Capacitor?.Plugins?.MobileTool;
      if (mobileTool) {
        const dataUrl = await blobAsDataUrl(blob);
        const result = await mobileTool.execute({
          tool: 'save_image',
          params: JSON.stringify({ data_url: dataUrl, filename: finalName }),
        });
        if (!result?.success) throw new Error(result?.content || '保存到相册失败');
      } else {
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = finalName;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      }
      setStatus('saved');
      window.setTimeout(() => setStatus('idle'), 1800);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存图片失败');
      setStatus('failed');
    }
  };

  return createPortal(
    <div className="image-lightbox-overlay" role="dialog" aria-modal="true" aria-label="图片预览" onClick={onClose}>
      <header className="image-lightbox-header" onClick={event => event.stopPropagation()}>
        {imageCount > 1 ? <div className="image-lightbox-counter">{index + 1} / {imageCount}</div> : <span>图片预览</span>}
        <button type="button" className="image-lightbox-close" onClick={onClose} aria-label="关闭图片预览">×</button>
      </header>
      <div className="image-lightbox-stage" onClick={event => event.stopPropagation()}
        onTouchStart={event => { touchStart.current = { x: event.touches[0].clientX, y: event.touches[0].clientY }; }}
        onTouchEnd={event => {
          const start = touchStart.current;
          touchStart.current = null;
          if (!start || imageCount < 2) return;
          const dx = event.changedTouches[0].clientX - start.x;
          const dy = event.changedTouches[0].clientY - start.y;
          if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.2) moveImage(dx < 0 ? 1 : -1);
        }}>
        {imageCount > 1 && index > 0 && <button type="button" className="image-lightbox-nav previous" onClick={() => moveImage(-1)} aria-label="上一张图片">‹</button>}
        <img src={currentSrc} alt={currentImage.alt || alt} className="image-lightbox-img" />
        {imageCount > 1 && index < imageCount - 1 && <button type="button" className="image-lightbox-nav next" onClick={() => moveImage(1)} aria-label="下一张图片">›</button>}
      </div>
      <div className="image-lightbox-toolbar" onClick={event => event.stopPropagation()}>
        <div className="image-lightbox-actions">
          <button type="button" className="image-lightbox-download" onClick={saveImage} disabled={status === 'saving'}>
            {status === 'saving' ? '保存中…' : status === 'saved' ? '已保存到相册 ✓' : '↓ 保存图片'}
          </button>
          {onLocateMessage && (
            <button type="button" className="image-lightbox-locate" onClick={onLocateMessage}>
              ↗ 定位到消息
            </button>
          )}
        </div>
        <div className="image-lightbox-details">
        {onGalleryCategoryChange && (
          <div className="image-lightbox-category-wrap">
            <span>相册分类</span>
            <div className="image-lightbox-category" role="group" aria-label="切换相册分类">
              <button
                type="button"
                className={galleryCategory === 'daily' ? 'active' : ''}
                disabled={galleryCategorySaving}
                onClick={() => onGalleryCategoryChange('daily')}
              >日常</button>
              <button
                type="button"
                className={galleryCategory === 'night' ? 'active night' : ''}
                disabled={galleryCategorySaving}
                onClick={() => onGalleryCategoryChange('night')}
              >成人</button>
            </div>
          </div>
        )}
        {galleryCategoryError && <span className="image-lightbox-error" role="alert">{galleryCategoryError}</span>}
        {status === 'failed' && <span className="image-lightbox-error" role="alert">{error}</span>}
        </div>
      </div>
    </div>, document.body
  );
}
