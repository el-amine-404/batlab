import { Rss } from "lucide-react";
import { Link } from "react-router";

import { Empty } from "@/components/layout/States";

export function NotFoundPage() {
  return (
    <Empty icon={<Rss />} title="There's no such page">
      <Link to="/" className="text-sky-300 hover:underline">Back to the downloads</Link>
    </Empty>
  );
}
