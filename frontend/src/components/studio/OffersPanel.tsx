import { useEffect, useState, type FormEvent } from "react";
import { useToast } from "../../context/ToastContext";
import { isNotImplemented, productsApi, userMessageFor } from "../../services/api";
import type { CatalogProduct } from "../../types/api";
import { Button } from "../ui/Button";
import { Field, Input, Select } from "../ui/Field";
import { ErrorState, LoadingState } from "../ui/States";

export function OffersPanel() {
  const { push } = useToast();
  const [products, setProducts] = useState<CatalogProduct[] | null>(null);
  const [productId, setProductId] = useState("");
  const [offer, setOffer] = useState("");
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
  if (!products) return <LoadingState label="Loading offers…" />;

  const active = products.filter((item) => item.is_active !== false && item.offer);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!productId) {
      push("Choose a product for this offer.", "error");
      return;
    }
    setBusy(true);
    try {
      await productsApi.update(productId, { offer: offer.trim() });
      setOffer("");
      push("Offer saved.", "success");
      load();
    } catch (err) {
      if (isNotImplemented(err)) {
        setUnavailable(true);
        push("Offers are not available yet.", "info");
      } else {
        setError(userMessageFor(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      {unavailable ? <p className="muted">Offers are not available from the API yet.</p> : null}
      {active.length ? (
        <ul className="stack">
          {active.map((item) => (
            <li key={item.id} className="row">
              <strong>{item.name}</strong>
              <span>{item.offer}</span>
              {item.price != null && item.price !== "" ? <span className="muted">{item.price}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No active offers.</p>
      )}
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <Field label="Offer product">
          <Select value={productId} onChange={(event) => setProductId(event.target.value)}>
            <option value="">Choose a product</option>
            {products.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Offer details">
          <Input required value={offer} onChange={(event) => setOffer(event.target.value)} />
        </Field>
        <Button type="submit" disabled={busy || !products.length}>
          Save offer
        </Button>
      </form>
    </div>
  );
}
