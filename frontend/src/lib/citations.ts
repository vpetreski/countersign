// Evidence citations: the agent cites tool results as [ev-03] or [ev-02, ev-17, ev-18].

export type Segment = { type: "text"; text: string } | { type: "cite"; ids: string[]; raw: string };

const CITATION = /\[\s*(ev-\d+(?:\s*,\s*ev-\d+)*)\s*\]/g;

export function parseCitations(text: string | null | undefined): Segment[] {
  if (!text) return [];
  const out: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(CITATION)) {
    const start = match.index ?? 0;
    if (start > last) out.push({ type: "text", text: text.slice(last, start) });
    const ids = (match[1] ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    out.push({ type: "cite", ids, raw: match[0] });
    last = start + match[0].length;
  }
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}

/** Every evidence id the text cites, in order of first appearance. */
export function citedIds(text: string | null | undefined): string[] {
  const seen = new Set<string>();
  for (const s of parseCitations(text)) {
    if (s.type === "cite") for (const id of s.ids) seen.add(id);
  }
  return [...seen];
}

const OPERATIONS = "stop_vm|resize_vm|delete_disk|release_ip";
const BREAK = new RegExp(
  String.raw`(?<=[.\]])\s+(?=(?:act-\d{2}\s|(?:${OPERATIONS})\s|Not proposed:|Excluded:|Caveats?:))`,
  "g",
);

/**
 * The agent writes its summary and report as one paragraph. Split it into lines at each action or
 * "Not proposed:" section, so the change request reads as a list. Text is never altered, only split.
 */
export function splitSummary(text: string | null | undefined): string[] {
  if (!text) return [];
  return text
    .split(BREAK)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** "Not proposed: a; b; c" -> ["a", "b", "c"]; any other line returns null. */
export function notProposedItems(line: string): string[] | null {
  const m = /^Not proposed:\s*(.*)$/s.exec(line);
  if (!m) return null;
  return (m[1] ?? "")
    .split(/;\s+/)
    .map((s) => s.trim().replace(/\.$/, ""))
    .filter(Boolean);
}
