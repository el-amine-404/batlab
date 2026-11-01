import { SmartScanner } from "./scanner";

export function CameraCapture({
  open,
  onClose,
  onCapture,
}: {
  open: boolean;
  onClose: () => void;
  onCapture: (file: File) => void;
}) {
  return <SmartScanner open={open} onClose={onClose} onCapture={onCapture} />;
}
