import { Rss } from "lucide-react";
import { Link } from "react-router";

import { Empty } from "@/components/layout/States";

export function NotFoundPage() {
  return (
    <Empty icon={<Rss />} title="There's no such page">
      <Link to="/" className="text-orange-300 hover:underline">Back to the feeds</Link>
    </Empty>
  );
}
