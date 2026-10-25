import { useState, useRef, useCallback, useEffect } from 'react';
import { getApiBase } from '../config';
import type { ToolCallRecord } from '../types';

interface VoiceTrack {
  voicePath: string;
  voiceText: string;
}

interface VoicePlayerProps {
  voiceTracks: VoiceTrack[];
}

function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function SingleVoicePlayer({ voicePath, voiceText }: VoiceTrack) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const progressRef = useRef<HTMLDivElement | null>(null);

  const [audioLoaded, setAudioLoaded] = useState(false);
  const [audioError, setAudioError] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  // 构建完整 URL
  const fullUrl = voicePath.startsWith('/')
    ? `${getApiBase()}${voicePath}`
    : voicePath;

  // 初始化 audio 元素
  useEffect(() => {
    const audio = new Audio(fullUrl);
    audio.preload = 'metadata';
    audioRef.current = audio;

    const onLoaded = () => {
      setAudioLoaded(true);
      setDuration(audio.duration);
    };
    const onError = () => setAudioError(true);
    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onEnded = () => { setIsPlaying(false); setCurrentTime(0); };
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onLoadedMeta = () => {
      setAudioLoaded(true);
      setDuration(audio.duration);
    };

    audio.addEventListener('loadeddata', onLoaded);
    audio.addEventListener('loadedmetadata', onLoadedMeta);
    audio.addEventListener('error', onError);
    audio.addEventListener('timeupdate', onTimeUpdate);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);

    // 尝试加载
    audio.load();

    return () => {
      audio.removeEventListener('loadeddata', onLoaded);
      audio.removeEventListener('loadedmetadata', onLoadedMeta);
      audio.removeEventListener('error', onError);
      audio.removeEventListener('timeupdate', onTimeUpdate);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.pause();
      audio.src = '';
      audioRef.current = null;
    };
  }, [fullUrl]);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || audioError) return;

    if (isPlaying) {
      audio.pause();
    } else {
      if (audio.ended) {
        audio.currentTime = 0;
      }
      audio.play().catch(() => setAudioError(true));
    }
  }, [isPlaying, audioError]);

  const handleSeek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const audio = audioRef.current;
    const bar = progressRef.current;
    if (!audio || !bar || !duration) return;

    const rect = bar.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * duration;
  }, [duration]);

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  // 错误状态
  if (audioError) {
    return (
      <div className="voice-player voice-player-error">
        <span className="voice-player-icon">🔈</span>
        <span className="voice-player-error-text">语音已过期</span>
      </div>
    );
  }

  return (
    <div className="voice-player">
      <div className="voice-player-header">
        <span className="voice-player-icon">🔈</span>
        <span className="voice-player-label">语音消息</span>
      </div>

      <div className="voice-player-controls">
        <button
          className={`voice-player-play-btn ${isPlaying ? 'playing' : ''}`}
          onClick={togglePlay}
          disabled={!audioLoaded}
          title={isPlaying ? '暂停' : '播放'}
        >
          {!audioLoaded ? '⏳' : isPlaying ? '⏸' : '▶️'}
        </button>

        <div
          className="voice-player-progress-bar"
          ref={progressRef}
          onClick={handleSeek}
          title={isFinite(duration) ? `${formatTime(currentTime)} / ${formatTime(duration)}` : ''}
        >
          <div
            className="voice-player-progress-fill"
            style={{ width: `${progress}%` }}
          />
        </div>

        <span className="voice-player-time">
          {audioLoaded ? formatTime(currentTime) : '--:--'}
          {' / '}
          {audioLoaded ? formatTime(duration) : '--:--'}
        </span>
      </div>

      {voiceText && (
        <div className="voice-player-text">"{voiceText}"</div>
      )}
    </div>
  );
}

export default function VoicePlayer({ voiceTracks }: VoicePlayerProps) {
  if (!voiceTracks || voiceTracks.length === 0) return null;

  return (
    <>
      {voiceTracks.map((track, idx) => (
        <SingleVoicePlayer key={idx} {...track} />
      ))}
    </>
  );
}

/** 从 toolCalls 中提取 send_voice 的语音数据 */
export function extractVoiceTracks(toolCalls: ToolCallRecord[]): VoiceTrack[] {
  return toolCalls
    .filter(tc => tc.tool === 'send_voice' && tc.extra_data?.voice_path)
    .map(tc => ({
      voicePath: tc.extra_data!.voice_path,
      voiceText: tc.extra_data!.voice_text || '',
    }));
}
