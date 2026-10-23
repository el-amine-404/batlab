import { Rss } from "lucide-react";
import { Link } from "react-router";

import { Empty } from "@/components/layout/States";

export function NotFoundPage() {
  return (
    <Empty icon={<Rss />} title="There's no such page">
      <Link to="/library" className="text-emerald-300 hover:underline">Back to the library</Link>
    </Empty>
  );
}
