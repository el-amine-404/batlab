import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Not found" description="songdrop has no screen at this address." />
      <Button asChild variant="secondary">
        <Link to="/">Add a link</Link>
      </Button>
    </>
  );
}
