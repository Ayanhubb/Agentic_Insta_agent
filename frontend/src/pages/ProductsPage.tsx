import { Card } from "../components/ui/Card";
import { OffersPanel } from "../components/studio/OffersPanel";
import { ProductPanel } from "../components/studio/ProductPanel";

export function ProductsPage() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Products</h1>
          <p className="lede">Product metadata, images, and the offer text the planner may quote.</p>
        </div>
      </div>
      <div className="grid two">
        <Card title="Product catalog">
          <ProductPanel />
        </Card>
        <Card title="Active offers">
          <OffersPanel />
        </Card>
      </div>
    </div>
  );
}
