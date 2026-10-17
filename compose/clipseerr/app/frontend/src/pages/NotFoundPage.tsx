import { Compass } from "lucide-react";
import { Link } from "react-router";

import { Empty } from "@/components/layout/States";

export function NotFoundPage() {
  return (
    <Empty icon={<Compass />} title="There's no such page">
      <Link to="/" className="text-rose-300 hover:underline">Back to Discover</Link>
    </Empty>
  );
}
