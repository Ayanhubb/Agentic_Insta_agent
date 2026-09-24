import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AdminPage } from "./pages/AdminPage";
import { AiSettingsPage } from "./pages/AiSettingsPage";
import { AssetsPage } from "./pages/AssetsPage";
import { AutomationPage } from "./pages/AutomationPage";
import { BrandPage } from "./pages/BrandPage";
import { BusinessPage } from "./pages/BusinessPage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { FestivalsPage } from "./pages/FestivalsPage";
import { GeneratePage } from "./pages/GeneratePage";
import { ImagesPage } from "./pages/ImagesPage";
import { InstagramPage } from "./pages/InstagramPage";
import { LoginPage } from "./pages/LoginPage";
import { McpPage } from "./pages/McpPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PostsPage } from "./pages/PostsPage";
import { ProductsPage } from "./pages/ProductsPage";
import { RegisterPage } from "./pages/RegisterPage";
import { SettingsPage } from "./pages/SettingsPage";
import {
  AccountTrendsPage,
  OpportunitiesPage,
  ReportsPage,
  ResearchPage,
  TrendsLayout,
  TrendsOverviewPage,
} from "./pages/trends";

export default function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/generate"
        element={
          <ProtectedRoute>
            <GeneratePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/images"
        element={
          <ProtectedRoute>
            <ImagesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/posts"
        element={
          <ProtectedRoute>
            <PostsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/automation"
        element={
          <ProtectedRoute>
            <AutomationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/festivals"
        element={
          <ProtectedRoute>
            <FestivalsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/business"
        element={
          <ProtectedRoute>
            <BusinessPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/brand"
        element={
          <ProtectedRoute>
            <BrandPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/products"
        element={
          <ProtectedRoute>
            <ProductsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/assets"
        element={
          <ProtectedRoute>
            <AssetsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ai-settings"
        element={
          <ProtectedRoute>
            <AiSettingsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/mcp"
        element={
          <ProtectedRoute>
            <McpPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/trends"
        element={
          <ProtectedRoute>
            <TrendsLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<TrendsOverviewPage />} />
        <Route path="account" element={<AccountTrendsPage />} />
        <Route path="research" element={<ResearchPage />} />
        <Route path="opportunities" element={<OpportunitiesPage />} />
        <Route path="reports" element={<ReportsPage />} />
      </Route>
      <Route
        path="/campaigns"
        element={
          <ProtectedRoute>
            <CampaignsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/instagram"
        element={
          <ProtectedRoute>
            <InstagramPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            <SettingsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin"
        element={
          <ProtectedRoute admin>
            <AdminPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  );
}
