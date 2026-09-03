/** 前端下载/打印工具。 */
export function downloadBlob(blob: Blob, filename: string): void {
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] as string));
}

/** 打开打印窗口渲染标题+正文 HTML，供浏览器「打印/存为 PDF」。 */
export function printHtml(title: string, contentHtml: string): void {
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;line-height:1.7;color:#111}` +
    `h1{font-size:24px}img{max-width:100%}pre{background:#f5f5f5;padding:12px;border-radius:6px;overflow:auto}` +
    `table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px}blockquote{border-left:3px solid #ddd;margin:0;padding-left:12px;color:#555}</style>` +
    `</head><body><h1>${escapeHtml(title)}</h1>${contentHtml}<` + `script>window.onload=function(){window.print()}<` + `/script></body></html>`,
  );
  w.document.close();
}
