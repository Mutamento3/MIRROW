import { useImport } from '../hooks/useImport';

function ImportPanel({ onClose }: { onClose: () => void }) {
  const {
    importStep, importDates, importSelectedDates, importProgress, importResult,
    importTargetSession, setImportTargetSession,
    handleImportFile, toggleImportDate, selectAllImportDates,
    startImportConfirm, executeImport, getSelectedDatesCount, resetImport,
  } = useImport();

  const close = () => { resetImport(); onClose(); };

  return (
    <div className="ledger-overlay" onClick={close}>
      <div className="ledger-modal" style={{maxWidth:480}} onClick={e => e.stopPropagation()}>
        <div className="ledger-title">
          <span>📤 导入消息</span>
          <button className="ledger-close-btn" onClick={close}>✕</button>
        </div>

        {/* Step 1: Upload */}
        {importStep === 'upload' && (
          <div className="import-upload-area">
            <p>选择从 MIRROW 导出的 SQLite 文件 (.db)</p>
            <p style={{fontSize:'0.8rem', color:'var(--text-secondary)', marginBottom:'1rem'}}>
              也可以将 .db 文件放到 data/mirrow_import.db 固定路径
            </p>
            <input type="file" accept=".db" onChange={handleImportFile}
              style={{padding:'0.5rem'}} />
          </div>
        )}

        {/* Step 2: Date selection */}
        {importStep === 'dates' && (
          <>
            <div className="import-date-header">
              <span>选择要导入的日期：</span>
              <button className="import-select-all" onClick={selectAllImportDates}>
                {importSelectedDates.size === importDates.length ? '取消全选' : '全选'}
              </button>
            </div>
            <div className="import-date-list">
              {importDates.map(d => (
                <div
                  key={d.date}
                  className={`import-date-item${importSelectedDates.has(d.date) ? ' selected' : ''}`}
                  onClick={() => toggleImportDate(d.date)}
                >
                  <input type="checkbox" checked={importSelectedDates.has(d.date)} readOnly />
                  <span className="date-label">{d.date}</span>
                  <span className="date-count">{d.message_count} 条</span>
                  {d.already_exists && <span className="date-warning">⚠已存在</span>}
                </div>
              ))}
            </div>
            <div style={{marginTop:'1rem', textAlign:'right'}}>
              <button className="confirm-btn" onClick={startImportConfirm}
                disabled={importSelectedDates.size === 0}
                style={{padding:'0.6rem 1.5rem', background:'var(--accent)', color:'white', border:'none', borderRadius:'6px', cursor:'pointer', opacity: importSelectedDates.size===0?0.5:1}}>
                下一步 ({importSelectedDates.size} 天, {getSelectedDatesCount()} 条)
              </button>
            </div>
          </>
        )}

        {/* Step 3: Confirmation */}
        {importStep === 'confirm' && (
          <div className="import-confirm">
            <p>即将导入 <strong>{importSelectedDates.size}</strong> 天的 <strong>{getSelectedDatesCount()}</strong> 条消息到：</p>
            <label>
              <input type="radio" name="importTarget"
                checked={importTargetSession === 'new'}
                onChange={() => setImportTargetSession('new')} />
              新建会话（推荐）
            </label>
            <label>
              <input type="radio" name="importTarget"
                checked={importTargetSession === 'current'}
                onChange={() => setImportTargetSession('current')} />
              追加到当前会话
            </label>
            <button className="confirm-btn" onClick={executeImport}>确认导入</button>
          </div>
        )}

        {/* Step 4: Progress */}
        {importStep === 'progress' && (
          <div className="import-progress">
            <p>正在导入... {importProgress.current}/{importProgress.total}</p>
          </div>
        )}

        {/* Step 5: Done */}
        {importStep === 'done' && importResult && (
          <div className="import-done">
            <p>✅ 导入完成</p>
            <p>消息: <strong>{importResult.imported_messages}</strong> 条</p>
            <p>书签: <strong>{importResult.imported_bookmarks}</strong> 条</p>
            {importResult.title && <p>会话: {importResult.title}</p>}
            <button className="done-btn" onClick={close}>
              关闭
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default ImportPanel;
