import { Card } from "../components/ui/Card";
import { BrandPanel } from "../components/studio/BrandPanel";

export function BrandPage() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Brand</h1>
          <p className="lede">Guidelines, colors, and the company logo used in generated images.</p>
        </div>
      </div>
      <Card title="Brand profile">
        <BrandPanel showFestivals />
      </Card>
    </div>
  );
}
