import type { Variant } from "../types";

export type Selections = Record<string, string>;

// a variant matches when every one of ITS selections agrees with the user's
// current choices; extra user choices on axes the variant doesn't carry are
// fine (a Size-only variant matches whatever Color is picked)
export function variantMatches(variant: Variant, selections: Selections): boolean {
  return variant.selections.every(
    (s) => selections[s.name] === undefined || selections[s.name] === s.value,
  );
}

// the resolved variant: matches the choices and pins down every chosen axis
// it knows about. Prefer the most specific match (most selections).
export function resolveVariant(variants: Variant[], selections: Selections): Variant | null {
  const chosen = Object.keys(selections);
  const candidates = variants.filter(
    (v) =>
      variantMatches(v, selections) &&
      v.selections.every((s) => chosen.includes(s.name)),
  );
  if (candidates.length === 0) return null;
  candidates.sort((a, b) => b.selections.length - a.selections.length);
  return candidates[0];
}

// images belonging to variants compatible with the current (possibly
// partial) selection, in variant order, deduped. Picking just "Color: Ochre"
// on a color+size product should surface every Ochre variant's photos even
// though nothing fully resolves yet. Empty selection -> [] (default gallery).
export function variantImages(variants: Variant[], selections: Selections): string[] {
  if (Object.keys(selections).length === 0) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of variants) {
    if (!variantMatches(v, selections)) continue;
    for (const url of v.image_urls) {
      if (!seen.has(url)) {
        seen.add(url);
        out.push(url);
      }
    }
  }
  return out;
}

// a value is disabled when picking it (keeping the other axes) leaves no
// compatible variant at all
export function valueDisabled(
  variants: Variant[],
  selections: Selections,
  axis: string,
  value: string,
): boolean {
  if (variants.length === 0) return false; // axes without variants never disable
  const test = { ...selections, [axis]: value };
  return !variants.some((v) => variantMatches(v, test));
}
