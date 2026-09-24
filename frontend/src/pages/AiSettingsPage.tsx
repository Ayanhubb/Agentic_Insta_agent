import { Card } from "../components/ui/Card";
import { ProviderStatus } from "../components/studio/ProviderStatus";

export function AiSettingsPage() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>AI settings</h1>
          <p className="lede">Connection status only. This screen never receives provider secrets.</p>
        </div>
      </div>
      <Card title="Providers">
        <ProviderStatus />
      </Card>
    </div>
  );
}
