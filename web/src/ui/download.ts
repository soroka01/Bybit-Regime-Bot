// Скачивание текста как файла целиком на клиенте (Blob + <a download>): без запросов в сеть.
export function downloadText(name: string, text: string, mime = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob(["﻿", text], { type: mime }));     // BOM — чтобы Excel распознал UTF-8
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}
