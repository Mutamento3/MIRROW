import { useEffect, useRef, useState } from 'react';

type Decoded = { image: VideoFrame };
type Decoder = { tracks:{ready:Promise<void>;selectedTrack:{frameCount:number}|null}; decode:(options:{frameIndex:number})=>Promise<Decoded>; close:()=>void };
type DecoderConstructor = new (options:{data:ArrayBuffer;type:string})=>Decoder;
type Frame = { bitmap:ImageBitmap; end:number; offset:number };
// Translation encoded by the shipped GIFs. Blink poses remain the original art.
const offsets = {
  astra:[0,1,2,3,2,1,0,-1,-2,-3,-3,-3,-2,-1,0],
  sol:[0,1,2,3,2,1,0,-1,-2,-3,-3,-3,-2,-1,0],
  luna:[0,1,2,3,4,3,2,1,1,1,1,0,0,-1,-2,-3,-4,-3,-2,-1,0],
};

/** Smooth rendering of existing art, without regenerating/recoloring the assets. */
export default function WorkshopSprite({ src, kind, label, paused=false }: { src:string; kind:keyof typeof offsets; label:string; paused?:boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const frames = useRef<Frame[]>([]);
  const [ready, setReady] = useState(false);
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setReduced(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    const ImageDecoderClass = (globalThis as unknown as {ImageDecoder?:DecoderConstructor}).ImageDecoder;
    setReady(false);
    if (!ImageDecoderClass) return;
    const abort = new AbortController();
    let canceled = false;
    let decoder:Decoder|undefined;
    const loaded:Frame[] = [];
    void (async () => {
      try {
        const response = await fetch(src, { signal:abort.signal });
        if (!response.ok) return;
        decoder = new ImageDecoderClass({ data:await response.arrayBuffer(), type:'image/gif' });
        await decoder.tracks.ready;
        const count = decoder.tracks.selectedTrack?.frameCount;
        if (count !== offsets[kind].length) return; // A replaced asset keeps native playback.
        let end = 0;
        for (let i=0; i<count; i++) {
          const { image } = await decoder.decode({frameIndex:i});
          try {
            const bitmap = await createImageBitmap(image, { resizeWidth:320, resizeHeight:320, resizeQuality:'high' });
            if (canceled) { bitmap.close(); return; }
            end += (image.duration || 70000) / 1000;
            loaded.push({ bitmap, end, offset:offsets[kind][i] * 320 / (kind === 'luna' ? 480 : 500) });
          } finally { image.close(); }
        }
        if (!canceled) { frames.current = loaded; setReady(true); }
      } catch { /* Older WebViews or failed decoding retain the original GIF. */ }
      finally { decoder?.close(); }
    })();
    return () => { canceled=true; abort.abort(); frames.current=[]; loaded.forEach(frame => frame.bitmap.close()); };
  }, [src, kind]);
  useEffect(() => {
    if (!ready || !canvas.current || !frames.current.length) return;
    const context = canvas.current.getContext('2d');
    if (!context) return;
    let animation = 0, elapsed = 0, previous = 0;
    const duration = frames.current.at(-1)!.end;
    const draw = (phase:number) => {
      const frame = frames.current.find(item => phase < item.end) || frames.current[0];
      const y = Math.sin(phase / duration * Math.PI * 2) * (kind === 'luna' ? 4 : 3) * 320 / (kind === 'luna' ? 480 : 500);
      context.clearRect(0,0,320,320);
      context.drawImage(frame.bitmap,0,y-frame.offset);
    };
    const tick = (time:number) => {
      if (previous) elapsed += time-previous;
      previous=time; draw(elapsed % duration);
      animation=requestAnimationFrame(tick);
    };
    const sync = () => {
      cancelAnimationFrame(animation); previous=0;
      if (reduced) draw(0);
      else if (!document.hidden && !paused) animation=requestAnimationFrame(tick);
    };
    draw(0); sync(); document.addEventListener('visibilitychange',sync);
    return () => { cancelAnimationFrame(animation); document.removeEventListener('visibilitychange',sync); };
  }, [ready, reduced, paused, kind]);
  return <span className="workshop-worker workshop-sprite" role="img" aria-label={label} data-playback={ready ? 'smooth' : 'gif'}>
    <img src={src} alt="" aria-hidden="true" style={{visibility:ready ? 'hidden' : 'visible'}} />
    <canvas ref={canvas} width={320} height={320} aria-hidden="true" style={{visibility:ready ? 'visible' : 'hidden'}} />
  </span>;
}
