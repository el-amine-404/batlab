import { useSession } from "../App";
import { PageHeader } from "../components/ui";
import { AccountsCard, PasswordCard } from "./Accounts";

/** Your account; for admins, the people and apps that may use the gateway. */
export function SettingsPage() {
  const session = useSession();
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description={session.role === "ADMIN" ? "Your password, and who may use the gateway." : "Your password."} />
      <PasswordCard />
      {session.role === "ADMIN" && <AccountsCard me={session.username} />}
    </div>
  );
}
