import React, { useState, useRef, useCallback } from 'react';
import { getApiBase } from '../config';
import { showToast } from './Toast';

export interface VoiceResult {
  text: string;
  voicePath: string;
  voiceDuration: number;
  prosody: Record<string, any>;
  cochleaJson: Record<string, any>;
  tone: string;
}

interface VoiceRecordButtonProps {
  onVoiceResult: (result: VoiceResult) => void;
  disabled?: boolean;
  voiceMode: boolean;
  onVoiceModeChange: (active: boolean) => void;
  onProcessingChange?: (active: boolean) => void;
}

declare global {
  interface Window {
    MirrowAudio?: { start(): boolean; stop(): void; isRecording(): boolean };
    _mirrowAudioChunk?: (b64: string) => void;
  }
}

const SAMPLE_RATE = 16000;
const MIN_DURATION = 0.6;
const MAX_DURATION = 60;
const CANCEL_SWIPE_Y = 80;

/** AionsHome 久经考验的 WAV 编码器 — Float32Array → WAV ArrayBuffer */
function encodeWav(frames: Float32Array[], sampleRate: number, totalSamples: number): ArrayBuffer {
  const samples = new Float32Array(totalSamples);
  let offset = 0;
  for (const frame of frames) { samples.set(frame, offset); offset += frame.length; }
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const write = (pos: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(pos + i, text.charCodeAt(i));
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let pos = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(pos, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    pos += 2;
  }
  return buf;
}

/** Int16 PCM bytes → Float32Array (normalized -1..1) */
function pcmToFloat(pcm: Uint8Array): Float32Array {
  const len = pcm.length / 2;
  const floats = new Float32Array(len);
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  for (let i = 0; i < len; i++) {
    floats[i] = view.getInt16(i * 2, true) / 32768;
  }
  return floats;
}

const VoiceRecordButton: React.FC<VoiceRecordButtonProps> = ({ onVoiceResult, disabled = false, onVoiceModeChange, onProcessingChange }) => {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [duration, setDuration] = useState(0);

  // Native bridge refs
  const floatFramesRef = useRef<Float32Array[]>([]);
  const isNativeRef = useRef(false);
  // Web fallback refs
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const webChunksRef = useRef<Blob[]>([]);
  // Shared
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef(0);
  const startYRef = useRef(0);
  const hasMovedRef = useRef(false);

  const cleanup = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    mediaRecorderRef.current = null;
    webChunksRef.current = [];
    floatFramesRef.current = [];
    isNativeRef.current = false;
  }, []);

  // ── 核心：发送 WAV → /api/voice/stt ──
  const processFrames = useCallback(async (forceCancel: boolean) => {
    const elapsed = (Date.now() - startTimeRef.current) / 1000;
    if (forceCancel || elapsed < MIN_DURATION) { cleanup(); setRecording(false); return; }

    if (!floatFramesRef.current.length) { cleanup(); setRecording(false); return; }

    setRecording(false);
    setProcessing(true);
    onProcessingChange?.(true);

    try {
      const frames = floatFramesRef.current.slice();
      const totalSamples = frames.reduce((s, f) => s + f.length, 0);
      const wav = encodeWav(frames, SAMPLE_RATE, totalSamples);
      const form = new FormData();
      form.append('file', new Blob([wav], { type: 'audio/wav' }), 'voice.wav');

      const resp = await fetch(`${getApiBase()}/api/voice/stt`, { method: 'POST', body: form });
      if (!resp.ok) throw new Error(`STT ${resp.status}`);

      const data = await resp.json();
      const text = (data.text || '').trim();
      if (!text) { showToast('没有听清，再说一次'); cleanup(); setProcessing(false); onProcessingChange?.(false); return; }

      onVoiceResult({
        text,
        voicePath: data.voice_path || '',
        voiceDuration: data.voice_duration || parseFloat(elapsed.toFixed(1)),
        prosody: data.prosody || {},
        cochleaJson: (data.cochlea || {}).summary_json || data.cochlea_json || {},
        tone: data.tone_label || '',
      });
      cleanup();
      setProcessing(false);
      onProcessingChange?.(false);
      onVoiceModeChange(false);

    } catch (err: any) {
      showToast(`识别失败: ${err.message?.slice(0, 50)}`);
      cleanup();
      setProcessing(false);
      onProcessingChange?.(false);
    }
  }, [cleanup, onVoiceResult, onVoiceModeChange, onProcessingChange]);

  // ── 开始录音 ──
  const beginRecording = useCallback(async (startY: number) => {
    if (disabled || recording || processing) return;
    startYRef.current = startY;
    hasMovedRef.current = false;
    setCancelling(false);
    setDuration(0);
    cleanup();

    // ① 原生 AudioBridge
    if (window.MirrowAudio) {
      isNativeRef.current = true;
      floatFramesRef.current = [];

      window._mirrowAudioChunk = (b64: string) => {
        try {
          const raw = atob(b64);
          const bytes = new Uint8Array(raw.length);
          for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
          floatFramesRef.current.push(pcmToFloat(bytes));
        } catch {}
      };

      const ok = window.MirrowAudio.start();
      if (!ok) { showToast('录音启动失败'); cleanup(); return; }

      startTimeRef.current = Date.now();
      setRecording(true);
      timerRef.current = setInterval(() => {
        const elapsed = (Date.now() - startTimeRef.current) / 1000;
        setDuration(elapsed);
        if (elapsed >= MAX_DURATION) processFrames(false);
      }, 100);
      return;
    }

    // ② Web 兜底
    if (!navigator.mediaDevices?.getUserMedia) { showToast('此设备不支持录音'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus' : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      mediaRecorderRef.current = recorder;
      webChunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) webChunksRef.current.push(e.data); };
      recorder.start(100);

      startTimeRef.current = Date.now();
      setRecording(true);
      timerRef.current = setInterval(() => {
        const elapsed = (Date.now() - startTimeRef.current) / 1000;
        setDuration(elapsed);
        if (elapsed >= MAX_DURATION) processFrames(false);
      }, 100);
    } catch (err: any) {
      showToast(`录音失败: ${err.message?.slice(0, 50)}`);
      cleanup();
    }
  }, [recording, processing, cleanup, processFrames]);

  // ── 停止录音 ──
  const stopRecording = useCallback((forceCancel: boolean) => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (isNativeRef.current) {
      window.MirrowAudio?.stop();
      window._mirrowAudioChunk = undefined;
      processFrames(forceCancel);
      return;
    }
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state !== 'recording') { cleanup(); setRecording(false); return; }
    setRecording(false);
    setProcessing(true);
    onProcessingChange?.(true);
    recorder.onstop = () => {
      // Web 兜底：用 MediaRecorder 的 webm blob
      const blob = new Blob(webChunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      const reader = new FileReader();
      reader.onload = () => {
        // webm 没法用 encodeWav，但 faster-whisper 也支持 webm
        // 走 REST endpoint 试试
        const form = new FormData();
        form.append('file', blob, 'voice.webm');
        fetch(`${getApiBase()}/api/voice/stt`, { method: 'POST', body: form })
          .then(r => { if (!r.ok) throw new Error(`STT ${r.status}`); return r.json(); })
          .then(data => {
            const text = (data.text || '').trim();
            if (!text) { showToast('没有听清'); cleanup(); setProcessing(false); onProcessingChange?.(false); return; }
            onVoiceResult({
              text, voicePath: data.voice_path || '', voiceDuration: data.voice_duration || 0,
              prosody: data.prosody || {}, cochleaJson: (data.cochlea || {}).summary_json || {},
              tone: data.tone_label || '',
            });
            cleanup(); setProcessing(false); onProcessingChange?.(false); onVoiceModeChange(false);
          })
          .catch(err => { showToast(`识别失败: ${err.message?.slice(0, 50)}`); cleanup(); setProcessing(false); onProcessingChange?.(false); });
      };
      reader.readAsDataURL(blob);
    };
    recorder.stop();
  }, [cleanup, processFrames, onVoiceResult, onVoiceModeChange, onProcessingChange]);

  // ── Touch ──
  const handleTouchStart = useCallback((e: React.TouchEvent) => { e.preventDefault(); e.stopPropagation(); beginRecording(e.touches[0].clientY); }, [beginRecording]);
  const handleTouchMove = useCallback((e: React.TouchEvent) => { if (!recording) return; e.preventDefault(); e.stopPropagation(); const dy = startYRef.current - e.touches[0].clientY; if (Math.abs(dy) > 5) hasMovedRef.current = true; setCancelling(dy > CANCEL_SWIPE_Y); }, [recording]);
  const handleTouchEnd = useCallback((e: React.TouchEvent) => { e.preventDefault(); e.stopPropagation(); if (!recording) return; const dy = startYRef.current - (e.changedTouches[0]?.clientY ?? startYRef.current); stopRecording(hasMovedRef.current && dy > CANCEL_SWIPE_Y); }, [recording, stopRecording]);
  const handleTouchCancel = useCallback((e: React.TouchEvent) => { e.preventDefault(); if (isNativeRef.current) { window.MirrowAudio?.stop(); window._mirrowAudioChunk = undefined; } if (timerRef.current) { clearInterval(timerRef.current); } cleanup(); setRecording(false); }, [cleanup]);

  // ── Mouse ──
  const handleMouseDown = useCallback((e: React.MouseEvent) => { e.preventDefault(); beginRecording(e.clientY); }, [beginRecording]);
  const handleMouseUp = useCallback(() => { if (!recording) return; stopRecording(false); }, [recording, stopRecording]);

  const dur = Math.floor(duration);
  const durDisplay = `${Math.floor(dur / 60)}:${(dur % 60).toString().padStart(2, '0')}`;

  return (
    <div className="voice-record-wrap voice-bar-only">
      <div className={`voice-hold-bar ${(recording || processing) ? 'active' : ''} ${cancelling ? 'cancelling' : ''}`}>
        {/* 录音中：全屏浮层 */}
        {recording && (
          <div className={`voice-record-overlay ${cancelling ? 'cancelling' : ''}`}
            onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd} onTouchCancel={handleTouchCancel} onMouseUp={handleMouseUp}>
            <div className="voice-record-popup">
              <div className={`voice-record-wave ${cancelling ? 'cancel' : ''}`}><span className="voice-record-dot" /></div>
              <div className="voice-record-hint">{cancelling ? '松开取消' : '松开发送，上滑取消'}</div>
              <div className="voice-record-timer">{cancelling ? '✕' : durDisplay}</div>
            </div>
          </div>
        )}
        {/* 识别中：轻量顶栏 */}
        {processing && (
          <div className="voice-processing-bar">
            <div className="voice-record-spinner" />
            <span>识别中...</span>
          </div>
        )}
        <button className={`voice-hold-btn ${recording ? 'recording' : ''} ${processing ? 'processing' : ''}`}
          onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd} onTouchCancel={handleTouchCancel}
          onMouseDown={handleMouseDown} onMouseUp={handleMouseUp}
          onContextMenu={e => e.preventDefault()} disabled={processing}>
          {!recording && !processing && '按住 说话'}
          {recording && <span className={`voice-hold-label ${cancelling ? 'cancel' : ''}`}>{cancelling ? '松开 取消' : `录音中 ${durDisplay}`}</span>}
          {processing && '识别中...'}
        </button>
      </div>
    </div>
  );
};

export default VoiceRecordButton;
