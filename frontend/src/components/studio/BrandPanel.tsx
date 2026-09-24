import { useEffect, useState, type FormEvent } from "react";
import { useToast } from "../../context/ToastContext";
import { assetMediaUrl, brandApi, festivalsApi, isNotImplemented, userMessageFor } from "../../services/api";
import type { BrandProfile, Festival } from "../../types/api";
import { Button } from "../ui/Button";
import { Field, Input, Textarea } from "../ui/Field";
import { ErrorState, LoadingState } from "../ui/States";

function festivalLabel(item: Festival): string {
  return item.festival_name || item.name || "";
}

function guidelinesText(value: BrandProfile["guidelines"]): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  return value.map((item) => item.body).filter(Boolean).join("\n\n");
}

function colorsText(colors: BrandProfile["brand_colors"]): string {
  return (colors ?? []).map((item) => item.hex).join(", ");
}

function textToColors(value: string): { hex: string }[] {
  return value
    .split(/[,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((hex) => ({ hex }));
}

export function BrandPanel({ showFestivals = false }: { showFestivals?: boolean }) {
  const { push } = useToast();
  const [companyName, setCompanyName] = useState("");
  const [guidelines, setGuidelines] = useState("");
  const [colors, setColors] = useState("");
  const [website, setWebsite] = useState("");
  const [handle, setHandle] = useState("");
  const [logoId, setLogoId] = useState<string | null>(null);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [festivals, setFestivals] = useState<Festival[]>([]);
  const [selectedFestivals, setSelectedFestivals] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        let missing = false;
        const [brand, festivalRows] = await Promise.all([
          brandApi.get().catch((err: unknown) => {
            if (isNotImplemented(err)) {
              missing = true;
              return null;
            }
            throw err;
          }),
          showFestivals ? festivalsApi.list().catch(() => [] as Festival[]) : Promise.resolve([] as Festival[]),
        ]);
        if (cancelled) return;
        setUnavailable(missing);
        setCompanyName(brand?.company_name ?? "");
        setGuidelines(guidelinesText(brand?.guidelines));
        setColors(colorsText(brand?.brand_colors));
        setWebsite(brand?.website ?? "");
        setHandle(brand?.instagram_handle ?? "");
        setLogoId(brand?.logo_png?.id ?? brand?.logo_png_asset_id ?? null);
        setLogoUrl(assetMediaUrl(brand?.logo_png));
        setSelectedFestivals(brand?.festival_preferences ?? []);
        setFestivals(festivalRows);
      } catch (err) {
        if (!cancelled) setError(userMessageFor(err));
      } finally {
        if (!cancelled) setReady(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [showFestivals]);

  if (error) return <ErrorState message={error} />;
  if (!ready) return <LoadingState label="Loading brand…" />;

  function payload(nextLogoId: string | null) {
    return {
      company_name: companyName.trim(),
      website: website.trim() || null,
      instagram_handle: handle.trim() || null,
      brand_colors: textToColors(colors),
      guidelines: guidelines.trim() || null,
      logo_png_asset_id: nextLogoId,
      festival_preferences: selectedFestivals,
    };
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!companyName.trim()) {
      push("Company name is required.", "error");
      return;
    }
    setBusy(true);
    try {
      const saved = await brandApi.save(payload(logoId));
      setCompanyName(saved.company_name ?? companyName);
      setGuidelines(guidelinesText(saved.guidelines) || guidelines);
      setColors(colorsText(saved.brand_colors).length ? colorsText(saved.brand_colors) : colors);
      setSelectedFestivals(saved.festival_preferences ?? selectedFestivals);
      setUnavailable(false);
      push("Brand guidelines saved.", "success");
    } catch (err) {
      if (isNotImplemented(err)) {
        setUnavailable(true);
        push("Brand settings are not available yet.", "info");
      } else {
        setError(userMessageFor(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function uploadLogo() {
    if (!file) return;
    setBusy(true);
    try {
      const asset = await brandApi.uploadAsset(file, "logo_png");
      setLogoId(asset.id);
      setLogoUrl(assetMediaUrl(asset));
      setFile(null);
      if (companyName.trim()) {
        await brandApi.save(payload(asset.id));
      }
      push("Company logo uploaded.", "success");
    } catch (err) {
      if (isNotImplemented(err)) push("Logo upload is not available yet.", "info");
      else setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={(event) => void onSubmit(event)}>
      {unavailable ? <p className="muted">Brand settings are not available from the API yet. Existing routes still work.</p> : null}
      <div className="row">
        {logoUrl ? <img className="thumb" src={logoUrl} alt="Company logo" /> : <p className="muted">No logo uploaded</p>}
      </div>
      <Field label="Company name">
        <Input required value={companyName} onChange={(event) => setCompanyName(event.target.value)} />
      </Field>
      <Field label="Company logo">
        <Input
          type="file"
          accept="image/png,.png"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </Field>
      <Button type="button" variant="secondary" disabled={busy || !file} onClick={() => void uploadLogo()}>
        Upload logo
      </Button>
      <Field label="Brand guidelines">
        <Textarea rows={4} value={guidelines} onChange={(event) => setGuidelines(event.target.value)} />
      </Field>
      <Field label="Brand colors" hint="Comma-separated #RRGGBB colors">
        <Input value={colors} onChange={(event) => setColors(event.target.value)} />
      </Field>
      <Field label="Website">
        <Input value={website} onChange={(event) => setWebsite(event.target.value)} placeholder="https://" />
      </Field>
      <Field label="Instagram handle">
        <Input value={handle} onChange={(event) => setHandle(event.target.value)} />
      </Field>
      {showFestivals ? (
        <fieldset className="stack">
          <legend className="field-label">Festival preferences</legend>
          {festivals.length ? (
            festivals.map((item) => {
              const name = festivalLabel(item);
              if (!name) return null;
              const checked = selectedFestivals.includes(name);
              return (
                <label key={name} className="row">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setSelectedFestivals((current) =>
                        checked ? current.filter((entry) => entry !== name) : [...current, name],
                      );
                    }}
                  />
                  <span>{name}</span>
                </label>
              );
            })
          ) : (
            <p className="muted">No festivals returned yet.</p>
          )}
        </fieldset>
      ) : null}
      <Button type="submit" disabled={busy}>
        Save brand
      </Button>
    </form>
  );
}
