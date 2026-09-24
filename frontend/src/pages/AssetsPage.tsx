import { Card } from "../components/ui/Card";
import { AssetLibrary } from "../components/studio/AssetLibrary";

export function AssetsPage() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Assets</h1>
          <p className="lede">Logos and product files for this account. File paths stay on the server.</p>
        </div>
      </div>
      <Card title="Library">
        <AssetLibrary />
      </Card>
    </div>
  );
}
