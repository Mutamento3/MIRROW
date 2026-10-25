import type { ChangeEvent, ChangeEventHandler } from 'react';
import type { Persona } from '../types';
import { getApiBase } from '../config';
import './PersonaPage.css';



type PersonaPageProps = {
  persona: Persona;
  syncStatus: 'saved' | 'saving' | 'error';
  userAvatar: string;
  
  onPersonaChange: (persona: Persona) => void;
  onUserAvatarChange: (avatar: string) => void;
  onAiAvatarUpload: ChangeEventHandler<HTMLInputElement>;
  onUserAvatarUpload: ChangeEventHandler<HTMLInputElement>;
  
  onBack: () => void;
  
};

function Avatar({ value, alt }: { value: string; alt: string }) {
  return value.startsWith('data:')
    ? <img src={value} alt={alt} />
    : <span aria-label={alt}>{value || '🤖'}</span>;
}

export default function PersonaPage({
  persona,
  syncStatus,
  userAvatar,
  onPersonaChange,
  onUserAvatarChange,
  onAiAvatarUpload,
  onUserAvatarUpload,
  onBack,
}: PersonaPageProps) {
  const setPersonaField = <AI extends keyof Persona>(key: AI, value: Persona[AI]) => {
    onPersonaChange({ ...persona, [key]: value });
  };

  const handleBackgroundUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      alert('请选择图片文件');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      alert('原图不能超过 20MB');
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const source = String(reader.result || '');
      const image = new Image();
      image.onload = () => {
        const maxSide = 1920;
        const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        const context = canvas.getContext('2d');
        if (!context) {
          setPersonaField('chatBackground', source);
          return;
        }
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        setPersonaField('chatBackground', canvas.toDataURL('image/jpeg', 0.82));
      };
      image.onerror = () => alert('无法读取这张图片');
      image.src = source;
    };
    reader.readAsDataURL(file);
  };

  const backgroundOpacity = Math.round((persona.chatBackgroundOpacity ?? 0.42) * 100);
  const backgroundPreviewSrc = persona.chatBackground?.startsWith('/')
    ? `${getApiBase()}${persona.chatBackground}`
    : persona.chatBackground;

  return (
    <main className="persona-page">
      <div className="persona-page-shell">
        <header className="persona-page-header">
          <button type="button" className="persona-back" onClick={onBack} aria-label="返回聊天">←</button>
          <div>
            <h2>AI 的人设</h2>
            <p>身份、称呼与头像</p>
          </div>
          <span className={`persona-autosave ${syncStatus}`}>{syncStatus === 'saving' ? '正在保存…' : syncStatus === 'error' ? '尚未同步' : '已保存'}</span>
        </header>

        <section className="persona-section persona-identity-section">
          <div className="persona-section-heading">
            <span className="persona-section-icon">✦</span>
            <div>
              <h3>身份</h3>
              <p>聊天里稳定可见的名字、称呼和头像</p>
            </div>
          </div>

          <div className="persona-identity-grid">
            <div className="persona-avatar-column">
              <input id="persona-ai-avatar" type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={onAiAvatarUpload} hidden />
              <label className="persona-avatar-button persona-avatar-k" htmlFor="persona-ai-avatar">
                <Avatar value={persona.avatar} alt="AI 的头像" />
                <span>更换头像</span>
              </label>
              <button type="button" className="persona-text-action" onClick={() => setPersonaField('avatar', '🤖')}>恢复默认</button>
            </div>

            <div className="persona-fields">
              <label className="persona-field">
                <span>AI 的名字</span>
                <input value={persona.name} onChange={event => setPersonaField('name', event.target.value)} placeholder="AI" />
              </label>
              <label className="persona-field">
                <span>AI 对你的称呼</span>
                <input value={persona.nickname || ''} onChange={event => setPersonaField('nickname', event.target.value)} placeholder="人类伙伴" />
              </label>
              <label className="persona-field persona-emoji-field">
                <span>用表情作为 AI 的头像</span>
                <input
                  value={persona.avatar.startsWith('data:') ? '' : persona.avatar}
                  onChange={event => setPersonaField('avatar', event.target.value)}
                  placeholder="例如：✨"
                />
              </label>
            </div>
          </div>

          <div className="persona-self-avatar">
            <input id="persona-user-avatar" type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={onUserAvatarUpload} hidden />
            <label className="persona-mini-avatar" htmlFor="persona-user-avatar">
              <Avatar value={userAvatar} alt="人类伙伴的头像" />
              <span className="persona-avatar-edit">更换</span>
            </label>
            <div className="persona-self-copy">
              <strong>你的聊天头像</strong>
              <span>点击头像上传图片，支持 JPG、PNG、WebP 与 GIF</span>
            </div>
            <button type="button" className="persona-text-action" onClick={() => onUserAvatarChange('👤')}>恢复默认</button>
          </div>
        </section>

        <section className="persona-section">
          <div className="persona-section-heading">
            <span className="persona-section-icon">◈</span>
            <div>
              <h3>人格核心</h3>
              <p>这段内容直接定义 AI 的系统人设</p>
            </div>
          </div>
          <label className="persona-prompt-editor">
            <span>系统提示词</span>
            <textarea
              value={persona.systemPrompt}
              onChange={event => setPersonaField('systemPrompt', event.target.value)}
              rows={8}
              placeholder="AI 的身份、关系与稳定行为逻辑"
            />
          </label>
        </section>

        <section className="persona-section persona-background-section">
          <div className="persona-section-heading">
            <span className="persona-section-icon">▧</span>
            <div>
              <h3>聊天背景</h3>
              <p>给聊天区放一张淡淡的背景，不遮住气泡与文字</p>
            </div>
          </div>

          <div className={`persona-background-preview${persona.chatBackground ? ' has-image' : ''}`}>
            {backgroundPreviewSrc ? (
              <img src={backgroundPreviewSrc} alt="当前聊天背景预览" style={{ opacity: persona.chatBackgroundOpacity ?? 0.42 }} />
            ) : (
              <span>暖色纯色背景</span>
            )}
          </div>

          <div className="persona-background-actions">
            <input id="persona-chat-background" type="file" accept="image/png,image/jpeg,image/webp" onChange={handleBackgroundUpload} hidden />
            <label className="persona-background-button" htmlFor="persona-chat-background">从手机上传</label>
            
            <button
              type="button"
              className="persona-background-button subtle"
              onClick={() => setPersonaField('chatBackground', undefined)}
              disabled={!persona.chatBackground}
            >
              恢复暖色纯色
            </button>
          </div>

          <label className="persona-background-opacity">
            <span>背景清晰度 <strong>{backgroundOpacity}%</strong></span>
            <input
              type="range"
              min="20"
              max="80"
              step="1"
              value={backgroundOpacity}
              disabled={!persona.chatBackground}
              onChange={event => setPersonaField('chatBackgroundOpacity', Number(event.target.value) / 100)}
            />
          </label>
        </section>

        

        <footer className="persona-page-footer">
          <button
            type="button"
            onClick={() => onPersonaChange({ name: 'AI助手', avatar: '🤖', nickname: '', systemPrompt: '你是一个乐于助人的AI助手。' })}
          >
            重置整张人设卡
          </button>
        </footer>
      </div>
    </main>
  );
}
