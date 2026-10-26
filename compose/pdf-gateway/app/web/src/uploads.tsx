import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, upload } from "./api";

const MAX_BYTES = 50 * 1024 * 1024;

export type UploadItem = { id: string; file: File; progress: number; scanId?: string; error?: string; duplicateOf?: string };

type Uploads = { items: UploadItem[]; add: (files: FileList | File[] | null) => void; canUpload: boolean };
const Context = createContext<Uploads>({ items: [], add: () => undefined, canUpload: false });

/**
 * This session's uploads, kept above the pages: they carry on while the person moves
 * around the app, and a file dropped on any page joins the same list.
 */
export function UploadProvider({ canUpload, children }: { canUpload: boolean; children: ReactNode }) {
  const client = useQueryClient();
  const [items, setItems] = useState<UploadItem[]>([]);
  const update = useCallback((id: string, patch: Partial<UploadItem>) =>
    setItems((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item)), []);

  const add = useCallback(async (files: FileList | File[] | null) => {
    if (!files || !canUpload) return;
    const next: UploadItem[] = Array.from(files).map((file) => ({
      id: crypto.randomUUID(), file, progress: 0,
      // Checked again by the server; this only saves a pointless upload.
      error: file.size > MAX_BYTES ? "Larger than 50 MB" : !/\.pdf$/i.test(file.name) && file.type !== "application/pdf" ? "Not a PDF" : undefined,
    }));
    setItems((current) => [...next, ...current]);
    for (const item of next.filter((candidate) => !candidate.error)) {
      try {
        // Archive and Paperless: Paperless gets the clean copy once it is ready.
        const result = await upload(item.file, "ARCHIVE_AND_IMPORT", (progress) => update(item.id, { progress }));
        update(item.id, { progress: 1, scanId: result.scanId });
        client.invalidateQueries({ queryKey: ["scans"] });
      } catch (error) {
        const existing = error instanceof ApiError && error.code === "DUPLICATE_UPLOAD" ? error.location?.match(/scan-[0-9A-Z]{26}$/)?.[0] : undefined;
        update(item.id, existing ? { duplicateOf: existing, progress: 1 } : { error: error instanceof Error ? error.message : "Upload refused" });
      }
    }
  }, [canUpload, client, update]);

  return <Context.Provider value={{ items, add, canUpload }}>{children}</Context.Provider>;
}

export function useUploads() {
  return useContext(Context);
}
