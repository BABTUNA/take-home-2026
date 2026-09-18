import { describe, expect, it } from "vitest";
import { resolveVariant, valueDisabled, variantImages, variantMatches, variantsAreDense } from "./resolveVariant";
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

describe("variantImages", () => {
  const withImgs = [
    v({ Color: "Navy", Size: "M" }, { image_urls: ["navy-1.jpg", "navy-2.jpg"] }),
    v({ Color: "Navy", Size: "L" }, { image_urls: ["navy-1.jpg"] }),
    v({ Color: "Lake", Size: "M" }, { image_urls: ["lake-1.jpg"] }),
  ];
  it("collects deduped images from variants compatible with a partial selection", () => {
    expect(variantImages(withImgs, { Color: "Navy" })).toEqual(["navy-1.jpg", "navy-2.jpg"]);
  });
  it("returns nothing for an empty selection (default gallery order)", () => {
    expect(variantImages(withImgs, {})).toEqual([]);
  });
  it("returns nothing when variants carry no images", () => {
    expect(variantImages(catalog, { Color: "Navy" })).toEqual([]);
  });
});

describe("valueDisabled", () => {
  // 3 variants over a 2x2 matrix = dense, the list is trusted to exclude
  const denseOptions = [
    { name: "Color", values: ["Navy", "Lake"] },
    { name: "Size", values: ["M", "L"] },
  ];
  it("disables combinations no variant supports when the list is dense", () => {
    // no Lake + L variant exists
    expect(valueDisabled(catalog, { Color: "Lake" }, "Size", "L", denseOptions)).toBe(true);
  });
  it("keeps supported combinations enabled", () => {
    expect(valueDisabled(catalog, { Color: "Navy" }, "Size", "L", denseOptions)).toBe(false);
  });
  it("re-picking the same axis is never a dead end", () => {
    expect(valueDisabled(catalog, { Color: "Lake", Size: "M" }, "Color", "Navy", denseOptions)).toBe(false);
  });
  it("never disables when the page asserted no variants", () => {
    expect(valueDisabled([], { Color: "Iron" }, "Size", "44", denseOptions)).toBe(false);
  });
  it("never disables when the list is sparse (3 verified of a big matrix)", () => {
    const sparseOptions = [
      { name: "Color", values: ["Navy", "Lake", "Black", "White", "Spruce", "Heather", "Blue", "Gray"] },
      { name: "Size", values: ["XS", "S", "M", "L", "XL", "XXL"] },
    ];
    expect(valueDisabled(catalog, { Color: "Lake" }, "Size", "L", sparseOptions)).toBe(false);
    expect(variantsAreDense(catalog, sparseOptions)).toBe(false);
  });
});
