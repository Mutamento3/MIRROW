

import MusicComposer from './music/MusicComposer';
import { composeMusic } from './music/api';
import { setMusicDraft, useMusicDraft } from './music/draft';




import { useEffect, useRef, useState } from 'react';
import { useLoungeFocus } from '../hooks/useLoungeFocus';
import { useGameChatFocus } from '../hooks/useGameChatFocus';
import { showToast } from './Toast';
import {OutgoingPreparationError} from '../lib/outgoingMessage';
import { getPlatform } from '../config';
import { getApiBase } from '../config';
import VoiceRecordButton from './VoiceRecordButton';
import type { VoiceResult } from './VoiceRecordButton';
import type { VoiceAttachment, ModelType, ImageModelType, LongTextCard } from '../types';

type ChatInputProps = {
  isLoading: boolean;
  draftDisabled?: boolean;
  model: ModelType;
  imageModel: ImageModelType;
  useMemory: boolean;
  onSend: (messageText: string, imageFiles: File[], voiceData?: { voiceAttachment: VoiceAttachment; voiceProsody: Record<string, any> }, longTextCard?: LongTextCard) => false | void | Promise<void>;
  sessionId?:string;
  onTopicEnd: () => void;
  inputDisabled?: boolean;  attachmentsEnabled?: boolean;
  onVoiceProcessingChange?: (active: boolean) => void;
};

function ChatInput({ isLoading, draftDisabled = false, model, imageModel, useMemory, onSend, onTopicEnd , inputDisabled = false, attachmentsEnabled = true, onVoiceProcessingChange }: ChatInputProps) {
  const loungeBusy = useLoungeFocus();
  const gameBusy = useGameChatFocus();
  inputDisabled = inputDisabled || loungeBusy || gameBusy;
  const [input, setInput] = useState('');
  const [selectedImages, setSelectedImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);
  const [topicEndPending, setTopicEndPending] = useState(false);
  const [showPlusMenu, setShowPlusMenu] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [longTextCard, setLongTextCard] = useState<LongTextCard | null>(null);
  const [musicEditor, setMusicEditor] = useState(false);
  const sendInFlightRef = useRef(false);
  const [sendPending, setSendPending] = useState(false);
  
  
  const musicDraft = useMusicDraft();
  
  
  useEffect(() => {
    if (!musicDraft) return;
    if (!attachmentsEnabled) { alert('歌曲卡片保留在草稿中，请回到与 AI 的主会话添加。'); return; }
    if (longTextCard && !confirm('输入框已有一张卡片，要替换成这首歌吗？')) { setMusicDraft(null); return; }
    setLongTextCard(musicDraft); setMusicDraft(null);
  }, [musicDraft, attachmentsEnabled]);
  const [cardDraft, setCardDraft] = useState({ title: '', body: '' });
  const [cardEditorOpen, setCardEditorOpen] = useState(false);
  const [xhsEditorOpen, setXhsEditorOpen] = useState(false);
  const [xhsDraft, setXhsDraft] = useState({ link: '', title: '', body: '' });
  const [xhsError, setXhsError] = useState('');
  const [xhsBusy, setXhsBusy] = useState(false);
  const isMobile = getPlatform() === 'mobile';
  const createCardId = () => globalThis.crypto?.randomUUID?.()
    || `card_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

  const handleTopicEndClick = () => {
    if (isLoading || sendInFlightRef.current || inputDisabled) return;
    if (topicEndPending) { setTopicEndPending(false); onTopicEnd(); }
    else { setTopicEndPending(true); setTimeout(() => setTopicEndPending(false), 3000); }
  };

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const newImages = Array.from(files).slice(0, 5 - selectedImages.length);
    setSelectedImages([...selectedImages, ...newImages]);
    const newPreviews = newImages.map(file => URL.createObjectURL(file));
    setImagePreviews([...imagePreviews, ...newPreviews]);
    e.target.value = '';
  };

  const removeImage = (index: number) => {
    const newImages = [...selectedImages];
    const newPreviews = [...imagePreviews];
    URL.revokeObjectURL(newPreviews[index]);
    newImages.splice(index, 1);
    newPreviews.splice(index, 1);
    setSelectedImages(newImages);
    setImagePreviews(newPreviews);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    // 移动端：Enter = 换行（默认行为），桌面端保持 Enter = 发送
    if (isMobile) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleLocalSend();
    }
  };

  const handleLocalSend = async () => {
    if (sendInFlightRef.current || isLoading || inputDisabled) return;
    if (longTextCard && !attachmentsEnabled) { alert('当前会话不支持卡片，请切回主会话。'); return; }
    
    
    // The backend owns lounge admission. A second client-side fetch could
    // time out silently while chat transport was otherwise healthy.
    sendInFlightRef.current = true;
    setSendPending(true);
    const submittedText=input;
    const submittedImages=[...selectedImages];
    const submittedCard=longTextCard;
    try {
      const send = onSend(input.trim(), selectedImages, undefined, longTextCard || undefined);
      if (send === false) {
        showToast('error', '上一条还在发送，消息已留在输入框');
        return;
      }
      imagePreviews.forEach(url => URL.revokeObjectURL(url));
      setInput('');
      setSelectedImages([]);
      setImagePreviews([]);
      setLongTextCard(null);
      
      setShowPlusMenu(false);
      await send;    } catch (error) {
      if (error instanceof OutgoingPreparationError) {
        setInput(current => current || submittedText);
        setSelectedImages(current => current.length ? current : submittedImages);
        setImagePreviews(current => current.length ? current : submittedImages.map(file => URL.createObjectURL(file)));
        setLongTextCard(current => current || submittedCard);
        
      }
      
      console.error('发送未完成:', error);
      showToast('error', error instanceof OutgoingPreparationError ? `${error.message}，草稿已恢复` : '发送未完成，请查看聊天记录后再重试');
    } finally {
      sendInFlightRef.current = false;
      setSendPending(false);
    }
  };

  const plusMenuItems = [
    ...(attachmentsEnabled ? [{ label: '🎶 分享歌曲小卡片', action: () => { composeMusic(); setShowPlusMenu(false); } }] : []),
    ...(attachmentsEnabled ? [{ label: '🍠 分享小红书帖子', action: () => { setXhsDraft(longTextCard?.kind === 'xhs' ? { link: longTextCard.link || '', title: longTextCard.title, body: longTextCard.body } : { link: '', title: '', body: '' }); setXhsEditorOpen(true); setShowPlusMenu(false); } }] : []),
    { label: '📝 制作长文小卡片', action: () => { setCardDraft(longTextCard && !longTextCard.kind ? longTextCard : { title: '', body: '' }); setCardEditorOpen(true); setShowPlusMenu(false); } },
    {
      label: '📷 传图片',
      action: () => { document.getElementById('image-upload')?.click(); setShowPlusMenu(false); },
    },
    {
      label: '📸 现场拍照',
      action: () => { document.getElementById('camera-capture')?.click(); setShowPlusMenu(false); },
    },
    {
      label: topicEndPending ? '确认结束话题？' : '⏹ 聊到这里',
      action: () => { handleTopicEndClick(); setShowPlusMenu(false); },
    },
  ];

  return (
    <div className={`input-area${isMobile ? ' input-mobile' : ''}`}>
      
      {imagePreviews.length > 0 && (
        <div className="image-previews">
          {imagePreviews.map((preview, index) => (
            <div key={index} className="image-preview">
              <img src={preview} alt={`预览 ${index + 1}`} />
              <button type="button" className="remove-image-btn" onClick={() => removeImage(index)} title="移除图片">×</button>
            </div>
          ))}
        </div>
      )}
      
      {longTextCard?.kind === 'music' && <div className="music-pending" role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter') setMusicEditor(true); }} onClick={() => setMusicEditor(true)}><div className="music-cover">{longTextCard.cover ? <img src={longTextCard.cover} alt="" referrerPolicy="no-referrer" /> : '♪'}</div><div><small>{longTextCard.music_type === 'playlist' ? '待寄出的一张歌单' : '待寄出的一首歌'}</small><strong>{longTextCard.title}</strong><small>{longTextCard.music_type === 'playlist' ? `${longTextCard.track_count || 0} 首 · 点开核对` : `${longTextCard.artist} · 点开核对`}</small></div><button aria-label="移除音乐卡片" onClick={e => { e.stopPropagation(); setLongTextCard(null); }}>×</button></div>}
      {musicEditor && <MusicComposer initial={longTextCard} onClose={() => setMusicEditor(false)} onSave={card => { setLongTextCard(card); setMusicEditor(false); }} />}
      {longTextCard?.kind === 'xhs' && <div className="long-text-card-draft" onClick={() => { setXhsDraft({ link: longTextCard.link || '', title: longTextCard.title, body: longTextCard.body }); setXhsEditorOpen(true); }}>
        <span className="long-text-card-draft-icon">🍠</span><span className="long-text-card-draft-copy"><span className="long-text-card-kicker">待寄出的小红书帖子</span><strong>{longTextCard.title || '一篇帖子'}</strong><small>点开核对链接与附言</small></span>
        <button className="long-text-card-remove" type="button" aria-label="移除帖子卡片" onClick={e => { e.stopPropagation(); setLongTextCard(null); }}>×</button>
      </div>}
      {longTextCard && !longTextCard.kind && (
        <div className="long-text-card-draft" onClick={() => { setCardDraft(longTextCard); setCardEditorOpen(true); }}>
          <span className="long-text-card-draft-icon" aria-hidden="true">♡</span>
          <span className="long-text-card-draft-copy">
            <span className="long-text-card-kicker">待寄出的长文</span>
            <strong>{longTextCard.title || '无标题长文卡片'}</strong>
            <small>{longTextCard.body.length} 字 · 点开继续编辑</small>
          </span>
          <button className="long-text-card-remove" type="button" aria-label="移除长文卡片" onClick={(e) => { e.stopPropagation(); setLongTextCard(null); }}>×</button>
        </div>
      )}

      <div className="input-row">
        {/* 语音切换按钮 — 后宫模式隐藏 */}
        {(
        <button
          className={`voice-mode-toggle ${voiceMode ? 'active' : ''}`}
          onClick={() => { if (voiceMode || (!isLoading && !inputDisabled)) setVoiceMode(!voiceMode); }}
          onTouchEnd={(e) => { e.preventDefault(); if (voiceMode || (!isLoading && !inputDisabled)) setVoiceMode(!voiceMode); }}
          disabled={!voiceMode && (isLoading || inputDisabled)}
          title={voiceMode ? '切换键盘' : '切换语音'}
        >{voiceMode ? '⌨️' : '🎤'}</button>
        )}

        {voiceMode ? (
          <VoiceRecordButton
            voiceMode={voiceMode}
            onVoiceResult={async (result: VoiceResult) => {
              const va: VoiceAttachment = { path: result.voicePath, text: result.text, duration: result.voiceDuration, tone: result.tone };
              const vp = { ...result.prosody, cochlea_json: result.cochleaJson };
              const send = onSend(result.text || '🔈 语音消息', [], { voiceAttachment: va, voiceProsody: vp });
              if (send === false) {
                setInput(result.text || '');
                showToast('error', '上一条还在发送，语音转录已留在输入框');
              } else if (send) {
                void send.catch(error => {
                  if (error instanceof OutgoingPreparationError) {
                    setInput(current => current || result.text || '');
                    setVoiceMode(false);
                  }
                  console.error('语音发送未完成:', error);
                  showToast('error', '语音发送未完成，请先查看聊天记录');
                });
              }
            }}
            onVoiceModeChange={setVoiceMode}
            onProcessingChange={onVoiceProcessingChange}
            disabled={isLoading || inputDisabled}
          />
        ) : (
          <textarea
            className="message-input"
            placeholder={gameBusy ? 'AI 正在玩你邀请的游戏，结束后可发送；可以先写在这里…' : loungeBusy ? 'AI 正在会客，结束后可发送；可以先写在这里…' : isLoading ? '可以先写下一条，回复完成后发送…' : '输入消息...'}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyPress}
            disabled={draftDisabled}
            rows={isMobile ? 1 : 3}
          />
        )}

        <div className="input-actions">
          {isMobile && (
            <div className="plus-menu-container">
              <button className={`plus-btn${showPlusMenu ? ' active' : ''}`}
                onClick={() => setShowPlusMenu(!showPlusMenu)} disabled={draftDisabled} title="更多操作">
                {showPlusMenu ? '✕' : '+'}
              </button>
              {showPlusMenu && (<>
                <div className="plus-menu-backdrop" onClick={() => setShowPlusMenu(false)} />
                <div className="plus-menu">
                  {plusMenuItems.map((item, i) => (<button key={i} className="plus-menu-item" onClick={item.action}>{item.label}</button>))}
                </div>
              </>)}
            </div>
          )}
          <button className="send-button" onClick={handleLocalSend}
            disabled={(!input.trim() && selectedImages.length === 0 && !longTextCard) || isLoading || inputDisabled || sendPending}
            title={gameBusy ? 'AI 正在游玩，结束后可发送' : inputDisabled ? '主聊天暂不可发送' : '发送'}>
            {isLoading || sendPending ? '...' : inputDisabled ? '🔒' : isMobile ? '↑' : '发送'}
          </button>
        </div>
      </div>

      {/* 桌面端保留原有控制栏 */}
      {!isMobile && (
        <div className="input-controls">
          <div className="input-left">
            <div className="input-info">
              当前模型: <strong>{model === 'deepseek' ? 'DeepSeek' : 'GLM'}</strong>
              {' · '}图像分析: <strong>{imageModel === 'glm' ? 'GLM 4V' : imageModel === 'flash-vision' ? 'DS Flash 多模态' : 'DS V4'}</strong>
              {useMemory && ' · 记忆功能已启用'}
            </div>
            <div className="image-upload">
              <input type="file" id="image-upload" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={handleImageSelect} style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', clip: 'rect(0,0,0,0)' }} />
              <label htmlFor="image-upload" className="image-upload-btn">📷 添加图片</label>
              {selectedImages.length > 0 && <span className="image-count">({selectedImages.length}张)</span>}
              {attachmentsEnabled && <button type="button" className="image-upload-btn" onClick={() => composeMusic()}>🎶 歌曲卡片</button>}
              {attachmentsEnabled && <button type="button" className="image-upload-btn" onClick={() => { setXhsDraft(longTextCard?.kind === 'xhs' ? { link: longTextCard.link || '', title: longTextCard.title, body: longTextCard.body } : { link: '', title: '', body: '' }); setXhsEditorOpen(true); }}>🍠 帖子卡片</button>}
              <button type="button" className="image-upload-btn" style={{ marginLeft: 8 }} onClick={() => { setCardDraft(longTextCard && !longTextCard.kind ? longTextCard : { title: '', body: '' }); setCardEditorOpen(true); }}>📝 制作长文小卡片</button>
            </div>
          </div>
          <div className="input-buttons">
            <button className={`topic-end-button${topicEndPending ? ' topic-end-confirm' : ''}`} onClick={handleTopicEndClick} disabled={isLoading || inputDisabled} title={topicEndPending ? '再次点击确认结束话题' : '结束当前话题并生成摘要'}>
              {topicEndPending ? '确认结束？' : '聊到这里'}
            </button>
          </div>
        </div>
      )}

      {/* 移动端隐藏的图片输入 */}
      {isMobile && (
        <>
          <input type="file" id="image-upload" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={handleImageSelect} style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', clip: 'rect(0,0,0,0)' }} />
          <input type="file" id="camera-capture" accept="image/*" capture="environment" onChange={handleImageSelect} style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', clip: 'rect(0,0,0,0)' }} />
        </>
      )}
      {cardEditorOpen && (
        <div className="long-text-card-modal-backdrop" onMouseDown={() => setCardEditorOpen(false)}>
          <section className="long-text-card-modal" onMouseDown={e => e.stopPropagation()}>
            <header className="long-text-card-modal-header">
              <div>
                <span className="long-text-card-kicker">写给 AI 的一页心意</span>
                <h3>{longTextCard ? '继续修改小卡片' : '制作长文小卡片'}</h3>
              </div>
              <button className="long-text-card-close" type="button" aria-label="关闭" onClick={() => setCardEditorOpen(false)}>×</button>
            </header>
            <label className="long-text-card-field">
              <span>卡片标题 <small>可选，也会作为后续轮次的一句话摘要</small></span>
              <input value={cardDraft.title} maxLength={500} placeholder="比如：今晚想和你说的事" onChange={e => setCardDraft(d => ({ ...d, title: e.target.value }))} />
            </label>
            <label className="long-text-card-field long-text-card-body-field">
              <span>卡片正文 <small>必填</small></span>
              <textarea value={cardDraft.body} maxLength={100000} placeholder="把想交给 AI 的长文放在这里……" onChange={e => setCardDraft(d => ({ ...d, body: e.target.value }))} rows={12} />
            </label>
            <footer className="long-text-card-modal-footer">
              <span className="long-text-card-count">{cardDraft.body.length.toLocaleString()} 字</span>
              <div className="long-text-card-actions">
                <button className="long-text-card-button secondary" type="button" onClick={() => setCardEditorOpen(false)}>先不写了</button>
                <button className="long-text-card-button primary" type="button" disabled={!cardDraft.body.trim()} onClick={() => { if (longTextCard?.kind && !confirm('输入框已有一张卡片，要替换吗？')) return; setLongTextCard({ id: longTextCard && !longTextCard.kind ? longTextCard.id : createCardId(), title: cardDraft.title.trim(), body: cardDraft.body }); setCardEditorOpen(false); }}>装进小卡片</button>
              </div>
            </footer>
          </section>
        </div>
      )}
      {xhsEditorOpen && <div className="long-text-card-modal-backdrop" onMouseDown={() => setXhsEditorOpen(false)}>
        <section className="long-text-card-modal" onMouseDown={event => event.stopPropagation()}>
          <header className="long-text-card-modal-header"><div><span className="long-text-card-kicker">把偶遇的一篇帖子递给 AI</span><h3>分享小红书帖子</h3></div><button className="long-text-card-close" type="button" onClick={() => setXhsEditorOpen(false)} aria-label="关闭">×</button></header>
          <label className="long-text-card-field"><span>小红书分享链接或分享文字</span><textarea value={xhsDraft.link} onChange={event => setXhsDraft(d => ({ ...d, link: event.target.value }))} maxLength={4096} rows={3} placeholder="粘贴从小红书复制的分享链接……" /></label>
          <label className="long-text-card-field"><span>卡片标题 <small>可选；不会当作 AI 已读内容</small></span><input value={xhsDraft.title} onChange={event => setXhsDraft(d => ({ ...d, title: event.target.value }))} maxLength={500} placeholder="给这篇帖子起个名字" /></label>
          <label className="long-text-card-field"><span>附言 <small>可选</small></span><textarea value={xhsDraft.body} onChange={event => setXhsDraft(d => ({ ...d, body: event.target.value }))} maxLength={2000} rows={4} placeholder="想告诉 AI 为什么分享给他……" /></label>
          {xhsError && <p role="alert">{xhsError}</p>}
          <footer className="long-text-card-modal-footer"><small>链接仅作为分享，不代表 AI 已读帖子。</small><div className="long-text-card-actions"><button className="long-text-card-button secondary" type="button" onClick={() => setXhsEditorOpen(false)}>取消</button><button className="long-text-card-button primary" type="button" disabled={!xhsDraft.link.trim() || xhsBusy} onClick={async () => { if (longTextCard && longTextCard.kind !== 'xhs' && !confirm('输入框已有一张卡片，要替换吗？')) return; setXhsBusy(true); setXhsError(''); try { const response = await fetch(`${getApiBase()}/api/xhs-posts/resolve-preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: xhsDraft.link }) }); const resolved = await response.json(); if (!response.ok) throw new Error(resolved.detail || '分享链接未通过核对'); setLongTextCard({ id: longTextCard?.kind === 'xhs' ? longTextCard.id : createCardId(), kind: 'xhs', link: resolved.url, post_key: resolved.post_key, link_status: resolved.link_status, title: xhsDraft.title.trim(), body: xhsDraft.body }); setXhsEditorOpen(false); } catch (cause) { setXhsError(cause instanceof Error ? cause.message : '分享链接未通过核对'); } finally { setXhsBusy(false); } }}>{xhsBusy ? '正在核对…' : '装进小卡片'}</button></div></footer>
        </section>
      </div>}

    </div>
  );
}

export default ChatInput;
