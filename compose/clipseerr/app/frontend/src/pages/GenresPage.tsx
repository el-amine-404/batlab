import { Link } from "react-router";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { useCategories } from "@/lib/queries";
import { cn } from "@/lib/utils";

// A steady colour per genre name, so a tile looks the same every visit.
const HUES = [12, 28, 45, 330, 300, 270, 200, 160, 140, 350];
function hue(name: string) {
  let sum = 0;
  for (const char of name) sum = (sum * 31 + char.charCodeAt(0)) >>> 0;
  return HUES[sum % HUES.length];
}

export function GenresPage() {
  const { data, error, refetch, isPending } = useCategories();
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader eyebrow="Browse" title="Genres" description="Music video charts by genre, from YouTube and Apple Music." />
      <div className="flex flex-col gap-10">
        {data.map((group) => (
          <section key={group.title}>
            <h2 className="mb-3 text-lg font-semibold tracking-tight">{group.title}</h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {group.categories.map((category) => {
                const h = hue(category.title);
                return (
                  <Link
                    key={category.id}
                    to={`/genres/${category.id}`}
                    className={cn("group relative flex h-24 items-end overflow-hidden rounded-xl border border-white/5 p-3.5 transition hover:-translate-y-0.5 hover:shadow-xl sm:h-28")}
                    style={{ background: `linear-gradient(135deg, oklch(0.42 0.14 ${h}), oklch(0.22 0.06 ${h + 30}))` }}
                  >
                    {category.image ? (
                      <img src={category.image} alt="" loading="lazy" decoding="async"
                           className="absolute -right-4 -bottom-4 size-24 rotate-12 rounded-lg object-cover opacity-70 shadow-xl transition group-hover:rotate-6 sm:size-28" />
                    ) : (
                      // No cover (Apple's genres, Hot): its top videos' pictures, fanned like one.
                      category.images?.slice(0, 3).reverse().map((src, i, all) => (
                        <img key={src} src={src} alt="" loading="lazy" decoding="async"
                             style={{ right: `${-14 + (all.length - 1 - i) * 26}px`, transform: `rotate(${12 - (all.length - 1 - i) * 7}deg)` }}
                             className="absolute -bottom-3 aspect-video w-24 rounded-md object-cover opacity-75 shadow-xl ring-1 ring-black/30 transition group-hover:opacity-90 sm:w-28" />
                      ))
                    )}
                    <span className="relative text-[15px] leading-tight font-semibold text-white drop-shadow">{category.title}</span>
                  </Link>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
