import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { CatalogPage } from "./pages/CatalogPage";
import { ProductPage } from "./pages/ProductPage";

export default function App() {
  return (
    <BrowserRouter>
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <Link to="/" className="font-display text-lg font-medium tracking-tight">
            Storefront
          </Link>
          <span className="eyebrow">Extracted catalog demo</span>
        </div>
      </header>
      <Routes>
        <Route path="/" element={<CatalogPage />} />
        <Route path="/product/:id" element={<ProductPage />} />
      </Routes>
    </BrowserRouter>
  );
}
