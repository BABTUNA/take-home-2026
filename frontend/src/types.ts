// mirrors models.py; the frontend consumes extraction output verbatim

export interface Price {
  price: number;
  currency: string;
  compare_at_price: number | null;
}

export interface ProductSummary {
  id: string;
  name: string;
  brand: string;
  price: Price;
  image_url: string | null;
  hover_image_url: string | null;
  category: string;
  source: "assignment" | "unseen";
}

export interface Selection {
  name: string;
  value: string;
}

export interface Option {
  name: string;
  values: string[];
}

export interface Variant {
  selections: Selection[];
  sku: string | null;
  price: number | null;
  available: boolean | null;
  image_urls: string[];
}

export interface Product {
  id: string;
  source: "assignment" | "unseen";
  name: string;
  price: Price;
  description: string;
  key_features: string[];
  image_urls: string[];
  video_url: string | null;
  category: { name: string };
  brand: string;
  colors: string[];
  options: Option[];
  variants: Variant[];
}
