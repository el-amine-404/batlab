import { ListChecks } from "lucide-react";

import { SongRows } from "@/components/library/SongRows";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { useSongs, useStatus } from "@/lib/queries";

/**
 * To check: the songs the AI synced and was unsure of some lines, newest
 * first. A person listens to those lines in the editor, nudges them, and
 * marks them checked: the work that makes AI lyrics as good as a person's.
 */
export function CheckPage() {
  const { data: status } = useStatus();
  const songs = useSongs({ to_check: true, sort: "added" });
  const pages = songs.data?.pages ?? [];
  const rows = pages.flatMap((page) => page.songs);

  return (
    <>
      <PageHeader title="To check"
                  description={status ? `${status.to_check.lines} lines the AI was unsure of, in ${status.to_check.songs} songs. Open one, listen to the marked lines, fix and mark them checked.`
                    : "The lines the AI was unsure of."} />
      {songs.isPending ? (
        <Loading />
      ) : songs.error ? (
        <Failed error={songs.error} retry={() => void songs.refetch()} />
      ) : !rows.length ? (
        <Empty icon={<ListChecks />} title="Nothing to check">Every AI line has been looked at, or the AI hasn't synced anything yet.</Empty>
      ) : (
        <SongRows songs={rows} total={pages[0]?.total ?? 0} more={!!songs.hasNextPage} loadingMore={songs.isFetchingNextPage}
                  onMore={() => void songs.fetchNextPage()} />
      )}
    </>
  );
}
