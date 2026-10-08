// The ten movement types the worker labels (worker/movements.py), as the timeline shows them.

export type MovementType =
  | "split_step"
  | "chasse"
  | "cross_step"
  | "running_step"
  | "lunge"
  | "scissor_jump"
  | "jump"
  | "pivot"
  | "recovery_step"
  | "hitting_step";

export const MOVEMENT_NAMES: Record<MovementType, string> = {
  split_step: "Split step",
  chasse: "Chassé",
  cross_step: "Cross-step",
  running_step: "Running step",
  lunge: "Lunge",
  scissor_jump: "Scissor jump",
  jump: "Jump",
  pivot: "Pivot",
  recovery_step: "Recovery step",
  hitting_step: "Hitting step",
};

// For chips too narrow for the full name.
const SHORT: Record<MovementType, string> = {
  split_step: "Split",
  chasse: "Chassé",
  cross_step: "Cross",
  running_step: "Run",
  lunge: "Lunge",
  scissor_jump: "Scissor",
  jump: "Jump",
  pivot: "Pivot",
  recovery_step: "Recover",
  hitting_step: "Hit",
};

export type Movement = {
  id: string;
  type: MovementType;
  foot: "L" | "R" | "both";
  zone: string | null;
  start: number;
  end: number;
  confidence: number | null;
  details: Record<string, number | string | boolean | null> | null;
};

/** "Lunge (R) → Front FH" */
export function movementText(m: Pick<Movement, "type" | "foot" | "zone">) {
  return `${MOVEMENT_NAMES[m.type]}${m.foot === "both" ? "" : ` (${m.foot})`} → ${m.zone ?? "?"}`;
}

/** Labels for a chip, longest first: "Lunge (R) → Front FH", "Lunge (R)", "Lunge". */
export function movementLabels(m: Pick<Movement, "type" | "foot" | "zone">) {
  const foot = m.foot === "both" ? "" : ` (${m.foot})`;
  return [movementText(m), `${MOVEMENT_NAMES[m.type]}${foot}`, SHORT[m.type]];
}
