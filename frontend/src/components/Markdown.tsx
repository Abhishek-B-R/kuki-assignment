import type { ReactNode } from "react";

// Minimal markdown: paragraphs, headings, bullet/numbered lists, **bold**, *italic*, `code`.
// Output is built from React elements only, so model text is always escaped.

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "h"; level: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] };

function parse(src: string): Block[] {
  const blocks: Block[] = [];
  let cur: Block | null = null;
  const flush = () => {
    if (cur) blocks.push(cur);
    cur = null;
  };
  for (const raw of src.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flush();
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line.trim());
    const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (h) {
      flush();
      blocks.push({ kind: "h", level: h[1].length, text: h[2] });
    } else if (ul || ol) {
      const ordered = !ul;
      const text = (ul ?? ol)![1];
      const c = cur as Block | null;
      if (c && c.kind === "list" && c.ordered === ordered) c.items.push(text);
      else {
        flush();
        cur = { kind: "list", ordered, items: [text] };
      }
    } else {
      const c = cur as Block | null;
      if (c && c.kind === "list" && /^\s{2,}/.test(raw)) {
        c.items[c.items.length - 1] += " " + line.trim();
      } else if (c && c.kind === "p") {
        c.lines.push(line.trim());
      } else {
        flush();
        cur = { kind: "p", lines: [line.trim()] };
      }
    }
  }
  flush();
  return blocks;
}

export function Markdown({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  const blocks = parse(text);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        const k = `b${i}`;
        if (b.kind === "h") {
          return (
            <p key={k} className="md-h">
              <strong>{inline(b.text, k)}</strong>
            </p>
          );
        }
        if (b.kind === "list") {
          const items = b.items.map((it, j) => <li key={j}>{inline(it, `${k}-${j}`)}</li>);
          return b.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>;
        }
        return (
          <p key={k}>
            {b.lines.map((l, j) => (
              <span key={j}>
                {j > 0 && <br />}
                {inline(l, `${k}-${j}`)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}
