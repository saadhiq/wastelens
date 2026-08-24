import { Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch, getToken, setToken } from "./lib/api";
import Analytics from "./pages/Analytics";
import Login from "./pages/Login";
import Review from "./pages/Review";
import Station from "./pages/Station";

/** Routes: /login is public; everything else requires a JWT. */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<AuthedLayout />}>
        <Route path="/" element={<Navigate to="/station" replace />} />
        <Route path="/station" element={<Station />} />
        <Route path="/review" element={<Review />} />
        <Route path="/analytics" element={<Analytics />} />
      </Route>
    </Routes>
  );
}

function AuthedLayout() {
  const navigate = useNavigate();
  const token = getToken();

  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<{ email: string; role: string }>("/auth/me"),
    enabled: token !== null,
    retry: false,
  });

  if (!token) return <Navigate to="/login" replace />;
  if (me.isError) {
    setToken(null); // expired/invalid token
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="flex items-center justify-between border-b bg-white px-4 py-3 sm:px-8">
        <nav className="flex items-center gap-4">
          <span className="font-bold text-emerald-700">WasteLens</span>
          <Link to="/station" className="text-sm text-gray-600 hover:text-emerald-700">
            Station
          </Link>
          <Link to="/review" className="text-sm text-gray-600 hover:text-emerald-700">
            Review
          </Link>
          <Link to="/analytics" className="text-sm text-gray-600 hover:text-emerald-700">
            Analytics
          </Link>
        </nav>
        <div className="flex items-center gap-3 text-sm">
          {me.data && (
            <span className="hidden text-gray-500 sm:inline">
              {me.data.email} · {me.data.role}
            </span>
          )}
          <button
            onClick={() => {
              setToken(null);
              navigate("/login");
            }}
            className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-100"
          >
            Sign out
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  );
}
