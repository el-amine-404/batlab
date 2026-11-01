import { useCallback, useRef } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { api, errorText, type Point, type Profile } from "./api";
import { addUploads } from "./uploads";

const PROFILE = "scanloom.profile";
export const savedProfile = () => (localStorage.getItem(PROFILE) as Profile | null) ?? undefined;
export const saveProfile = (profile: Profile) => localStorage.setItem(PROFILE, profile);

export const scanName = () => `Scan ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date())}`;

/** A new scan the moment there is something to put in it: from dropped files, or the camera's first picture. */
export function useNewScan() {
  const navigate = useNavigate();
  const scan = useRef<Promise<string> | null>(null);

  const ensure = useCallback(() => (scan.current ??= api.createProject({ title: scanName(), profile: savedProfile() }).then((project) => project.public_id)), []);

  const fromFiles = useCallback(async (files: File[]) => {
    if (!files.length) return;
    scan.current = null;
    try {
      const id = await ensure();
      addUploads(id, files);
      navigate(`/scans/${id}`);
    } catch (error) {
      toast.error(errorText(error, "The scan couldn't be started."));
    } finally {
      scan.current = null;
    }
  }, [ensure, navigate]);

  const fromCamera = useCallback(async (file: File, hint?: Point[]) => {
    try {
      addUploads(await ensure(), [file], hint);
    } catch (error) {
      toast.error(errorText(error, "The picture couldn't be sent."));
    }
  }, [ensure]);

  const finishCamera = useCallback(async (count: number) => {
    if (count && scan.current) navigate(`/scans/${await scan.current}`);
    scan.current = null;
  }, [navigate]);

  return { fromFiles, fromCamera, finishCamera };
}
