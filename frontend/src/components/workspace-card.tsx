import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { gradientForId } from "@/lib/colors";
import type { Workspace } from "@/lib/api";

export function WorkspaceCard({
  workspace,
  onOpen,
}: {
  workspace: Workspace;
  onOpen?: (workspaceId: string) => void;
}) {
  return (
    <Link
      href={`/w/${workspace.id}`}
      onClick={() => onOpen?.(workspace.id)}
      className="block"
    >
      <Card className="overflow-hidden transition hover:shadow-md">
        <div className={`h-28 ${gradientForId(workspace.id)}`} />
        <CardHeader>
          <CardTitle className="truncate">{workspace.name}</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-zinc-500">
          <div className="truncate">
            {workspace.qdrant_alias
              ? `别名：${workspace.qdrant_alias}`
              : `Collection：${workspace.qdrant_collection}`}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

