import { describe, expect, it } from "vitest";
import { resolveVariant, valueDisabled, variantMatches } from "./resolveVariant";
import type { Variant } from "../types";

const v = (sel: Record<string, string>, extra: Partial<Variant> = {}): Variant => ({
  selections: Object.entries(sel).map(([name, value]) => ({ name, value })),
  sku: null,
  price: null,
  available: null,
  image_urls: [],
  ...extra,
});

const catalog: Variant[] = [
  v({ Color: "Navy", Size: "M" }, { sku: "N-M", available: true }),
  v({ Color: "Navy", Size: "L" }, { sku: "N-L", available: false }),
  v({ Color: "Lake", Size: "M" }, { sku: "L-M", price: 24.99, available: true }),
];

describe("variantMatches", () => {
  it("matches when all variant selections agree", () => {
    expect(variantMatches(catalog[0], { Color: "Navy", Size: "M" })).toBe(true);
  });
  it("tolerates axes the variant doesn't carry", () => {
    expect(variantMatches(v({ Size: "M" }), { Color: "Navy", Size: "M" })).toBe(true);
  });
  it("rejects a conflicting choice", () => {
    expect(variantMatches(catalog[0], { Color: "Lake" })).toBe(false);
  });
});

describe("resolveVariant", () => {
  it("resolves a full selection to the concrete variant", () => {
    expect(resolveVariant(catalog, { Color: "Lake", Size: "M" })?.sku).toBe("L-M");
  });
  it("does not resolve on a partial selection that leaves axes open", () => {
    expect(resolveVariant(catalog, { Color: "Navy" })).toBeNull();
  });
  it("prefers the most specific match", () => {
    const withSizeOnly = [...catalog, v({ Size: "M" }, { sku: "SIZE-ONLY" })];
    expect(resolveVariant(withSizeOnly, { Color: "Navy", Size: "M" })?.sku).toBe("N-M");
  });
});

describe("valueDisabled", () => {
  it("disables combinations no variant supports", () => {
    // no Lake + L variant exists
    expect(valueDisabled(catalog, { Color: "Lake" }, "Size", "L")).toBe(true);
  });
  it("keeps supported combinations enabled", () => {
    expect(valueDisabled(catalog, { Color: "Navy" }, "Size", "L")).toBe(false);
  });
  it("re-picking the same axis is never a dead end", () => {
    expect(valueDisabled(catalog, { Color: "Lake", Size: "M" }, "Color", "Navy")).toBe(false);
  });
  it("never disables when the page asserted no variants", () => {
    expect(valueDisabled([], { Color: "Iron" }, "Size", "44")).toBe(false);
  });
});
