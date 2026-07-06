// Teaching empty state: every "nothing here" moment explains the domain and
// offers the next action.

import { Blankslate } from "@primer/react/experimental";
import type { ReactNode } from "react";

export function EmptyState({
  title,
  body,
  action,
  icon,
}: {
  title: string;
  body: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Blankslate spacious>
      {icon && <Blankslate.Visual>{icon}</Blankslate.Visual>}
      <Blankslate.Heading>{title}</Blankslate.Heading>
      <Blankslate.Description>{body}</Blankslate.Description>
      {action}
    </Blankslate>
  );
}
