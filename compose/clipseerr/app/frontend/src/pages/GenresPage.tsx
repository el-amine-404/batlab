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
                      // No cover (Apple's genres, Hot): its top videos' pictures, held like a hand of cards
                      // that fans out on hover.
                      <span className="fan absolute right-3 -bottom-5 h-16 w-28 sm:h-20 sm:w-32" aria-hidden>
                        {category.images?.slice(0, 3).map((src, i, all) => (
                          <img key={src} src={src} alt="" loading="lazy" decoding="async"
                               style={{ "--i": i - (all.length - 1) / 2 } as React.CSSProperties} />
                        ))}
                      </span>
                    )}
                    {/* The name stays legible over any picture. */}
                    <span className="pointer-events-none absolute inset-0 bg-gradient-to-r from-black/55 via-black/15 to-transparent" aria-hidden />
                    <span className="relative z-10 line-clamp-2 max-w-[58%] text-[15px] leading-tight font-semibold text-white [text-shadow:0_1px_6px_rgb(0_0_0/0.7)]">{category.title}</span>
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
