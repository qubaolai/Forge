export const CHAT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024;
export const KB_DOCUMENT_MAX_BYTES = 200 * 1024 * 1024;

export function formatUploadLimit(sizeBytes: number): string {
  const mib = 1024 * 1024;
  if (sizeBytes % mib === 0) return `${sizeBytes / mib}MB`;
  return `${sizeBytes} bytes`;
}

export function assertFileWithinLimit(file: File, maxBytes: number, label: string): void {
  if (file.size <= maxBytes) return;
  throw new Error(`${label}过大, 最大支持 ${formatUploadLimit(maxBytes)}: ${file.name}`);
}
