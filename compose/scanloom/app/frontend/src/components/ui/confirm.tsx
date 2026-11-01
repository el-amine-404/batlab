import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** Asks before something that can't be undone. */
export function Confirm({ open, title, text, action, onCancel, onConfirm }: {
  open: boolean; title: string; text: string; action: string; onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-muted-foreground">{text}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="justify-end">
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          <Button className="bg-destructive text-black hover:bg-destructive/85" onClick={onConfirm}>{action}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
