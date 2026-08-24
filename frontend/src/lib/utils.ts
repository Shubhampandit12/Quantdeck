import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn helper: merge conditional class lists, then dedupe
// conflicting Tailwind utilities (e.g. "p-2 p-4" -> "p-4").
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
