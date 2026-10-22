import { Navigate, useSearchParams } from "react-router";

/**
 * Where the phone's Share menu sends a link (manifest.webmanifest,
 * share_target): apps put it in url, or in text with words around it
 * ("Listen to this: https://open.spotify.com/…"). Opens Add with it filled in.
 */
export function SharePage() {
  const [params] = useSearchParams();
  const given = [params.get("url"), params.get("text"), params.get("title")].filter(Boolean).join(" ");
  const link = given.match(/https?:\/\/[^\s<>"']+/)?.[0];
  return <Navigate to={link ? `/?url=${encodeURIComponent(link)}` : "/"} replace />;
}
