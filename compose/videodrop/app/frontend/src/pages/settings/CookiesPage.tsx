import { PageHeader } from "@/components/layout/States";
import { CookiesCard } from "@/components/settings/CookiesCard";

export default function CookiesPage() {
  return (
    <>
      <PageHeader eyebrow="Settings" title="Cookies" description="For videos a site shows only to a signed-in viewer." />
      <CookiesCard />
    </>
  );
}
