// Explicit foreground recording only. Never submit to the normal voice-chat API.
export type EnrollmentRecording = { source: string; stop: (cancel?: boolean) => Promise<Blob | null> };

function wavBlob(samples: Float32Array): Blob {
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);
  const text = (offset: number, value: string) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, s)) * 32767), true));
  return new Blob([bytes], { type: 'audio/wav' });
}

export async function startEnrollmentRecording(deviceId: string): Promise<EnrollmentRecording> {
  if (window.MirrowAudio) {
    if (window.MirrowAudio.isRecording()) throw new Error('另一项录音正在进行，请先结束');
    const frames: Float32Array[] = [];
    window._mirrowAudioChunk = b64 => {
      const raw = atob(b64);
      const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
      const view = new DataView(bytes.buffer);
      frames.push(Float32Array.from({ length: Math.floor(bytes.length / 2) }, (_, i) => view.getInt16(i * 2, true) / 32768));
    };
    if (!window.MirrowAudio.start()) {
      window._mirrowAudioChunk = undefined;
      throw new Error('手机录音启动失败，请检查麦克风权限');
    }
    let stopped = false;
    return { source: '手机麦克风（原生）', stop: async cancel => {
      if (stopped) return null;
      stopped = true;
      window.MirrowAudio?.stop(); window._mirrowAudioChunk = undefined;
      if (cancel) return null;
      const result = new Float32Array(frames.reduce((n, frame) => n + frame.length, 0));
      let pos = 0;
      frames.forEach(frame => { result.set(frame, pos); pos += frame.length; });
      return wavBlob(result);
    } };
  }
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('此页面无法录音，请用手机 App 或电脑本机页面');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: {
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1,
    echoCancellation: false, noiseSuppression: false, autoGainControl: false,
  } });
  try {
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    recorder.start(100);
    let stopped = false;
    return { source: stream.getAudioTracks()[0]?.label || '浏览器麦克风', stop: cancel => new Promise((resolve, reject) => {
      if (stopped) { resolve(null); return; }
      stopped = true;
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        if (cancel) { resolve(null); return; }
        const context = new AudioContext();
        try {
          const decoded = await context.decodeAudioData(await new Blob(chunks, { type: recorder.mimeType }).arrayBuffer());
          const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
          const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
          const converted = await offline.startRendering();
          resolve(wavBlob(converted.getChannelData(0)));
        } catch (e) { reject(e); } finally { await context.close(); }
      };
      recorder.stop();
    }) };
  } catch (e) { stream.getTracks().forEach(track => track.stop()); throw e; }
}
