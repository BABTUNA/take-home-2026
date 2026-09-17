import type { Product, ProductSummary } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  return res.json();
}

export const fetchProducts = () => get<ProductSummary[]>("/products");
export const fetchProduct = (id: string) => get<Product>(`/products/${id}`);
