import { useEffect, useState, type FormEvent } from "react";
import { useToast } from "../../context/ToastContext";
import { isNotImplemented, productsApi, userMessageFor } from "../../services/api";
import type { CatalogProduct } from "../../types/api";
import { Button } from "../ui/Button";
import { Field, Input, Textarea } from "../ui/Field";
import { ErrorState, LoadingState } from "../ui/States";

function parsePrice(value: string): string | undefined {
  const cleaned = value.replace(/[₹$,\s]/g, "");
  if (!cleaned) return undefined;
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) {
    throw new Error("Price must be a number like 4999 or 4999.00.");
  }
  return cleaned;
}

export function ProductPanel() {
  const { push } = useToast();
  const [products, setProducts] = useState<CatalogProduct[] | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [sku, setSku] = useState("");
  const [price, setPrice] = useState("");
  const [offer, setOffer] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);

  function load() {
    productsApi
      .list()
      .then((rows) => {
        setProducts(rows);
        setUnavailable(false);
      })
      .catch((err) => {
        if (isNotImplemented(err)) {
          setProducts([]);
          setUnavailable(true);
          return;
        }
        setError(userMessageFor(err));
      });
  }

  useEffect(() => {
    load();
  }, []);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!products) return <LoadingState label="Loading products…" />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    let parsedPrice: string | undefined;
    try {
      parsedPrice = parsePrice(price);
    } catch (err) {
      push(err instanceof Error ? err.message : "Price is invalid.", "error");
      return;
    }
    setBusy(true);
    try {
      const created = await productsApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        category: category.trim() || undefined,
        sku: sku.trim() || undefined,
        price: parsedPrice,
        offer: offer.trim() || undefined,
        is_active: true,
      });
      if (image) await productsApi.uploadImage(created.id, image);
      setName("");
      setDescription("");
      setCategory("");
      setSku("");
      setPrice("");
      setOffer("");
      setImage(null);
      push("Product saved.", "success");
      load();
    } catch (err) {
      if (isNotImplemented(err)) {
        setUnavailable(true);
        push("Product catalog is not available yet.", "info");
      } else {
        setError(userMessageFor(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      {unavailable ? <p className="muted">Product catalog is not available from the API yet.</p> : null}
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <Field label="Product name">
          <Input required value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Product description">
          <Textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <Field label="Product category">
          <Input value={category} onChange={(event) => setCategory(event.target.value)} />
        </Field>
        <Field label="SKU">
          <Input value={sku} onChange={(event) => setSku(event.target.value)} />
        </Field>
        <Field label="Price">
          <Input value={price} onChange={(event) => setPrice(event.target.value)} placeholder="4999.00" />
        </Field>
        <Field label="Offer">
          <Input value={offer} onChange={(event) => setOffer(event.target.value)} placeholder="Active offer text" />
        </Field>
        <Field label="Product image">
          <Input
            type="file"
            accept="image/png,image/jpeg,.png,.jpg,.jpeg"
            onChange={(event) => setImage(event.target.files?.[0] ?? null)}
          />
        </Field>
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save product"}
        </Button>
      </form>
      {products.length ? (
        <ul className="stack">
          {products.map((product) => (
            <li key={product.id} className="row">
              <strong>{product.name}</strong>
              <span className="faint">{product.category || product.sku || "Active"}</span>
              {product.offer ? <span>{product.offer}</span> : null}
              {product.price != null && product.price !== "" ? <span className="muted">{product.price}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No products yet.</p>
      )}
    </div>
  );
}
