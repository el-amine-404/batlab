import { Check, Loader2, Undo2 } from "lucide-react";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Failed, Loading } from "@/components/layout/States";
import { Button } from "@/components/ui/button";

type Form = { dirty: boolean; saving: boolean; error: string | null; save: () => void; discard: () => void };

/** A settings screen: title, Save and Discard at the top (like the *arr apps), then its rows. */
export function SettingsScreen({
  title,
  description,
  form,
  ready,
  invalid = false,
  children,
}: {
  title: string;
  description: ReactNode;
  /** Left out: a screen whose changes apply at once (no Save). */
  form?: Form;
  ready: boolean;
  /** A field is wrong: Save waits. */
  invalid?: boolean;
  children: ReactNode;
}) {
  return (
    <>
      <PageHeader
        title={title}
        description={description}
        actions={
          form && (
            <>
              {form.dirty && (
                <Button variant="ghost" size="sm" disabled={form.saving} onClick={form.discard}>
                  <Undo2 /> Discard
                </Button>
              )}
              <Button size="sm" disabled={!form.dirty || form.saving || invalid} onClick={form.save}>
                {form.saving ? <Loader2 className="animate-spin" /> : <Check />} Save
              </Button>
            </>
          )
        }
      />
      {form?.error ? <Failed message={form.error} /> : ready ? <div className="flex flex-col gap-6">{children}</div> : <Loading />}
    </>
  );
}
