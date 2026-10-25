export function ReadingBody({ body }: { body: string }) {
  return <div className="wb-entry-body">{body.split('\n').map((line, i) => {
    if (/^#{1,4} /.test(line)) return <h3 className="wb-section-title" key={i}>{line.replace(/^#+ /, '')}</h3>;
    if (line.startsWith('- ')) return <p className="wb-bullet" key={i}>• {line.slice(2)}</p>;
    return line.trim() ? <p className="wb-paragraph" key={i}>{line}</p> : <div className="wb-spacer" key={i} />;
  })}</div>;
}
