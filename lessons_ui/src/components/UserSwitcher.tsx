import { useUser, roleLabels } from "@/context/UserContext";
import { UserCircle } from "lucide-react";

// "Act as": yourself or a referent group you belong to (decision 4). The list
// comes from /lessons/api/me, and the gateway refuses any other actor anyway.
const UserSwitcher = () => {
  const { currentUser, unreadCount, identities, switchIdentity } = useUser();

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-card border border-border">
      <div className="w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center relative">
        <UserCircle className="w-5 h-5 text-accent" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-destructive text-destructive-foreground text-[10px] rounded-full flex items-center justify-center font-bold">
            {unreadCount}
          </span>
        )}
      </div>
      <div className="text-right">
        {identities.length > 1 ? (
          <select
            aria-label="פעל בשם"
            value={currentUser.id}
            onChange={(e) => switchIdentity(e.target.value)}
            className="text-sm font-medium leading-none bg-transparent border-none p-0 focus:outline-none cursor-pointer"
          >
            {identities.map((u, i) => (
              <option key={u.id} value={u.id}>
                {i === 0 ? u.name : `${u.name} (קבוצה)`}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-sm font-medium leading-none">{currentUser.name}</p>
        )}
        <p className="text-[11px] text-muted-foreground mt-0.5">{roleLabels[currentUser.role]}</p>
      </div>
    </div>
  );
};

export default UserSwitcher;
